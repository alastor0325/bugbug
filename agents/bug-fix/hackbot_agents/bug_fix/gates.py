"""The gates a team run must pass before it may record a patch.

A team run follows a loop: a test that fails on the unfixed tree, a fix that makes it
pass, a review by the `reviewer` subagent, and a verify step, with review and verify
repeated until clean. The agent records the test and verify gates with the
`record_gate` tool; a hook records each review from the reviewer's own result, so the
main agent cannot report a review that did not happen. A `PreToolUse` hook refuses
the patch actions until the gates pass on the current diff.
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError, tool, tools_in
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names

REVIEWER = "reviewer"
MAX_REVIEW_ROUNDS = 3
SERVER_NAME = "gates"
RECORD_GATE_TOOL = f"mcp__{SERVER_NAME}__record_gate"
SUBMIT_TOOL, UPDATE_TOOL = actions_to_tool_names(
    ["phabricator.submit_patch", "phabricator.update_patch"]
)
SPAWN_TOOLS = "Agent|Task"
# A new revision needs these before review; an update to one skips them.
TDD_STEPS = ("test_fails", "test_passes")
# Every patch needs these after the last review, on the current diff.
CHECK_STEPS = ("verify", "associated_tests")

_BLOCKING = re.compile(r"^BLOCKING:\s*(\d+)\s*$", re.MULTILINE)


@dataclass
class Review:
    # None when the reviewer's result had no `BLOCKING: <n>` line.
    blocking: int | None
    fp: str


@dataclass
class Gates:
    """The gate state of one run in the checkout ``repo``."""

    repo: Path
    tdd: dict[str, bool] = field(default_factory=dict)
    # For each check step, the fingerprint of the patch its last pass ran on.
    checked: dict[str, str] = field(default_factory=dict)
    reviews: list[Review] = field(default_factory=list)

    def fingerprint(self) -> str:
        return fingerprint(self.repo)

    def record(self, step: str, ok: bool, *, fp: str) -> None:
        if step in TDD_STEPS:
            self.tdd[step] = ok
        elif step in CHECK_STEPS:
            if ok:
                self.checked[step] = fp
            else:
                self.checked.pop(step, None)
        else:
            raise ValueError(f"unknown step {step!r}")

    def refusal(self, tool_name: str, fp: str) -> str | None:
        """Why the patch action ``tool_name`` is refused on diff ``fp``, or None."""
        for step in () if tool_name == UPDATE_TOOL else TDD_STEPS:
            if not self.tdd.get(step):
                return f"record a passing `{step}` gate first ({RECORD_GATE_TOOL})"
        if not self.reviews:
            return f"spawn the `{REVIEWER}` subagent to review the patch first"
        last = self.reviews[-1]
        if last.fp != fp:
            return (
                "the patch changed after the last review: "
                f"spawn the `{REVIEWER}` again, then verify again"
            )
        if last.blocking is None:
            return (
                f"the last `{REVIEWER}` result had no `BLOCKING: <n>` line; "
                "spawn it again"
            )
        if last.blocking:
            if len(self.reviews) >= MAX_REVIEW_ROUNDS:
                return (
                    f"{MAX_REVIEW_ROUNDS} review rounds still have blocking findings: "
                    "do not submit. Record a Bugzilla comment with your findings."
                )
            return (
                f"fix the {last.blocking} blocking findings, then spawn the "
                f"`{REVIEWER}` again"
            )
        for step in CHECK_STEPS:
            if self.checked.get(step) != fp:
                return f"run and record a passing `{step}` gate on the current patch"
        return None


def parse_blocking(text: str) -> int | None:
    """The count on the last `BLOCKING: <n>` line of a reviewer's result."""
    found = _BLOCKING.findall(text or "")
    return int(found[-1]) if found else None


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
    step: Literal[TDD_STEPS + CHECK_STEPS],
    ok: bool,
    evidence: str,
) -> str:
    """Record a finished step of the team loop.

    ``evidence`` is the command you ran and what it showed. ``test_fails``: the new
    test fails on the unfixed tree. ``test_passes``: it passes with the fix.
    ``verify``: format, lint and build pass on the current patch.
    ``associated_tests``: every associated test that can run here passes, or fails the
    same way without the patch. List each test and its result, and each test that could
    not run here.
    """
    try:
        ctx.record(step, ok, fp=await asyncio.to_thread(ctx.fingerprint))
    except ValueError as e:
        raise ToolError(str(e)) from e
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
    """The PreToolUse hook that refuses the patch actions until the gates pass."""

    async def guard(input_data: dict, tool_use_id: str | None, context) -> dict:
        fp = await asyncio.to_thread(gates.fingerprint)
        reason = gates.refusal(input_data.get("tool_name", ""), fp)
        return {} if reason is None else _deny(reason)

    return guard


def review_hook(gates: Gates):
    """The PostToolUse hook that records each `reviewer` result as a review round."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        if (input_data.get("tool_input") or {}).get("subagent_type") == REVIEWER:
            gates.reviews.append(
                Review(
                    parse_blocking(_texts(input_data.get("tool_response"))),
                    fp=await asyncio.to_thread(gates.fingerprint),
                )
            )
        return {}

    return hook
