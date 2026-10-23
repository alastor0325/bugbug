"""The gates a team run must pass before it may record a patch.

A team run follows a loop: a test that fails on the unfixed tree, a fix that makes it
pass, a verify step, a review by the `reviewer` subagent, and the associated tests,
with verify, review and the tests repeated after any change. The agent records the
test and verify steps with the `record_gate` tool; a hook records each review from the
reviewer's own result, so the main agent cannot report a review that did not happen.
A `PreToolUse` hook refuses the patch actions until the gates pass on the current
diff, and fails closed when it cannot tell.
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError, tool, tools_in
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names

REVIEWER = "reviewer"
# The default number of review rounds with blocking findings after which the patch
# goes out anyway, with the open findings listed for a person. A team can change it.
MAX_REVIEW_ROUNDS = 3
SERVER_NAME = "gates"
RECORD_GATE_TOOL = f"mcp__{SERVER_NAME}__record_gate"
SUBMIT_TOOL, UPDATE_TOOL = actions_to_tool_names(
    ["phabricator.submit_patch", "phabricator.update_patch"]
)
SPAWN_TOOLS = "Agent|Task"
# Required unless the run updates an existing revision.
TDD_STEPS = ("test_fails", "test_passes")
# Every patch needs these after the last change, on the current diff.
CHECK_STEPS = ("verify", "associated_tests")
# What could not be checked here. Recorded for the test comment; never required.
NOT_CHECKED = "not_checked"
# The longest evidence a step may record; it becomes one cell of the test comment.
MAX_EVIDENCE = 400

# The reviewer may wrap its count in backticks or emphasis.
_BLOCKING = re.compile(r"^[\s`*_]*BLOCKING:\s*(\d+)[\s`*_.]*$", re.MULTILINE)
_NIT = re.compile(r"^\s*-\s*`?NIT`?[,:]?\s*(.+?)\s*$", re.MULTILINE)
_BLOCKER = re.compile(r"^\s*-\s*`?BLOCKING`?[,:]?\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class Review:
    # None when the reviewer's result had no `BLOCKING: <n>` line.
    blocking: int | None
    fp: str
    # The small findings of this round, which the agent may leave.
    nits: tuple[str, ...] = ()
    # The blocking findings of this round.
    blockers: tuple[str, ...] = ()


@dataclass
class Gates:
    """The gate state of one run in the checkout ``repo``.

    ``require_tdd`` is False for a run that updates an existing revision.
    """

    repo: Path
    require_tdd: bool = True
    max_review_rounds: int = MAX_REVIEW_ROUNDS
    # For each passed step, the fingerprint of the tree it passed on.
    passed: dict[str, str] = field(default_factory=dict)
    # The evidence of each step's last pass, for the test comment.
    evidence: dict[str, str] = field(default_factory=dict)
    reviews: list[Review] = field(default_factory=list)
    # The gates as they were when the submit guard last let a patch through, and the
    # fingerprint of that patch.
    sent: "Gates | None" = None
    sent_fp: str | None = None

    def fingerprint(self) -> str:
        return fingerprint(self.repo)

    def record(self, step: str, ok: bool, *, fp: str, evidence: str = "") -> None:
        if step not in TDD_STEPS + CHECK_STEPS + (NOT_CHECKED,):
            raise ValueError(f"unknown step {step!r}")
        if step == "test_fails":
            # A new failing test needs a new pass after it.
            self.passed.pop("test_passes", None)
        if ok:
            self.passed[step] = fp
            self.evidence[step] = evidence
        else:
            self.passed.pop(step, None)

    def blocking_rounds(self) -> int:
        """Rounds with blocking findings; an unreadable round counts too."""
        return sum(1 for r in self.reviews if r.blocking != 0)

    def review_open(self) -> bool:
        """True when the round limit is reached and the last review is not clean."""
        return (
            self.blocking_rounds() >= self.max_review_rounds
            and bool(self.reviews)
            and self.reviews[-1].blocking != 0
        )

    def refusal(self, fp: str) -> str | None:
        """Why a patch action is refused on diff ``fp``, or None."""
        if self.require_tdd:
            for step in TDD_STEPS:
                if step not in self.passed:
                    return f"record a passing `{step}` gate first ({RECORD_GATE_TOOL})"
            if self.passed["test_fails"] == self.passed["test_passes"]:
                return (
                    "`test_passes` was recorded on the same tree as `test_fails`: "
                    "record `test_passes` again after the fix"
                )
        if not self.reviews:
            return f"spawn the `{REVIEWER}` subagent to review the patch first"
        last = self.reviews[-1]
        if last.fp != fp:
            return (
                "the patch changed after the last review: verify, spawn the "
                f"`{REVIEWER}` and run the associated tests again"
            )
        if not self.review_open():
            if last.blocking is None:
                return (
                    f"the last `{REVIEWER}` result had no `BLOCKING: <n>` line; "
                    "spawn it again"
                )
            if last.blocking:
                return (
                    f"fix the {last.blocking} blocking findings, then verify and "
                    f"spawn the `{REVIEWER}` again"
                )
        for step in CHECK_STEPS:
            if self.passed.get(step) != fp:
                return f"run and record a passing `{step}` gate on the current patch"
        return None


def parse_blocking(text: str) -> int | None:
    """The count on the last `BLOCKING: <n>` line of a reviewer's result."""
    found = _BLOCKING.findall(text or "")
    return int(found[-1]) if found else None


def parse_nits(text: str) -> tuple[str, ...]:
    """The small findings in a reviewer's result: its `- NIT, ...` lines."""
    return tuple(_NIT.findall(text or ""))


def parse_blockers(text: str) -> tuple[str, ...]:
    """The blocking findings in a reviewer's result: its `- BLOCKING, ...` lines."""
    return tuple(_BLOCKER.findall(text or ""))


def fingerprint(repo: Path) -> str:
    """The tree id of the checkout as it is now: commits, edits and new files.

    Git builds it in a copy of the index, so only changed files are hashed and the
    checkout's own index is not touched. Ignored files, such as the objdir, are left
    out.
    """

    def git(*args: str, env: dict | None = None) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()

    index = Path(git("rev-parse", "--path-format=absolute", "--git-path", "index"))
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "index"
        if index.exists():
            # copy2 keeps the index's mtime, which git needs to rehash files changed
            # in the same second as the index (racy git).
            shutil.copy2(index, copy)
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        git("add", "-A", env=env)
        return git("write-tree", env=env)


@tool
async def record_gate(
    ctx: Gates,
    step: Literal[TDD_STEPS + CHECK_STEPS + (NOT_CHECKED,)],
    ok: bool,
    evidence: str,
) -> str:
    """Record a finished step of the team loop.

    ``evidence`` is the command you ran and what it showed. ``test_fails``: the new
    test fails on the unfixed tree. ``test_passes``: it passes with the fix.
    ``verify``: format, lint and build pass on the current patch.
    ``associated_tests``: every associated test that can run here passes, or fails the
    same way without the patch. List each test and its result, and each test that could
    not run here. ``not_checked``: what you could not check here, and why.

    The evidence goes into a table cell of a comment on the revision, so keep it under
    400 characters: short, simple sentences, test names in backticks, counts as
    numbers, and paths relative to the source tree.
    """
    if len(evidence) > MAX_EVIDENCE:
        raise ToolError(
            f"evidence is {len(evidence)} characters; record a shorter summary of at "
            f"most {MAX_EVIDENCE}"
        )
    try:
        fp = await asyncio.to_thread(ctx.fingerprint) if step != NOT_CHECKED else ""
        ctx.record(step, ok, fp=fp, evidence=evidence)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        raise ToolError(f"could not record {step}: {e}") from e
    return f"recorded {step}"


def gates_server(gates: Gates):
    """The in-process MCP server with the `record_gate` tool."""
    return build_sdk_server(SERVER_NAME, gates, tools_in(__name__))


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _texts(response: object) -> str:
    if isinstance(response, dict):
        response = response.get("content")
    if isinstance(response, list):
        return "\n".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in response
        )
    return str(response or "")


def submit_guard(gates: Gates):
    """The PreToolUse hook that refuses the patch actions until the gates pass.

    It fails closed: an error while it checks refuses the patch.
    """

    async def guard(input_data: dict, tool_use_id: str | None, context) -> dict:
        try:
            fp = await asyncio.to_thread(gates.fingerprint)
            reason = gates.refusal(fp)
        except Exception as e:  # any error refuses the patch
            return _deny(f"the gates could not be checked ({type(e).__name__}: {e})")
        if reason is not None:
            return _deny(reason)
        gates.sent = Gates(
            gates.repo,
            require_tdd=gates.require_tdd,
            max_review_rounds=gates.max_review_rounds,
            evidence=dict(gates.evidence),
            reviews=list(gates.reviews),
        )
        gates.sent_fp = fp
        return {}

    return guard


def review_hook(gates: Gates):
    """The PostToolUse hook that records each `reviewer` result as a review round.

    A result it cannot read, or a tree it cannot fingerprint, becomes an unreadable
    round, which the guard refuses.
    """

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        if (input_data.get("tool_input") or {}).get("subagent_type") != REVIEWER:
            return {}
        try:
            text = _texts(input_data.get("tool_response"))
            fp = await asyncio.to_thread(gates.fingerprint)
            gates.reviews.append(
                Review(parse_blocking(text), fp, parse_nits(text), parse_blockers(text))
            )
        except Exception as e:  # an unreadable round blocks the patch
            print(f"[bug_fix] review not recorded ({e})", file=sys.stderr)
            gates.reviews.append(Review(None, ""))
        return {}

    return hook
