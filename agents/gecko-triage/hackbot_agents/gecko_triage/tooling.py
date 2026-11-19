"""Gaps in our own tools that a run meets, and the person asked to close them.

`profiler-cli` refuses a profile whose format is newer than the version it knows. The run
cannot read that profile until someone bumps the pinned version, so the agent's
`tooling_owner` (in `shared/people.toml`) gets a needinfo with the run's comment.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder

from .actions_view import UPDATE_BUG, comment_texts, needinfo_requestees, updates
from .config import MAX_NEEDINFOS
from .state import TriageState

BASH_TOOL = "Bash"
PROFILE_FORMAT_GAP = re.compile(
    r"This profile is version (\d+), but this profiler-cli only supports up to "
    r"version (\d+)"
)


@dataclass
class ToolingGaps:
    # (profile version, newest version `profiler-cli` reads) of each refused profile.
    profile_formats: set[tuple[int, int]] = field(default_factory=set)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def profile_format_gaps(response: object) -> set[tuple[int, int]]:
    """The format refusals of `profiler-cli` in a tool result."""
    return {
        (int(found), int(known))
        for text in _strings(response)
        for found, known in PROFILE_FORMAT_GAP.findall(text)
    }


def gap_hook(gaps: ToolingGaps):
    """The PostToolUse hook on Bash that collects ``gaps``."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        # A failed call carries its output in `error`, not in `tool_response`.
        response = [input_data.get("tool_response"), input_data.get("error")]
        gaps.profile_formats |= profile_format_gaps(response)
        return {}

    return hook


def ask_tooling_owner(
    recorder: ActionsRecorder,
    bug: int,
    state: TriageState,
    gaps: ToolingGaps,
    owner: str | None,
) -> bool:
    """Needinfo ``owner`` when the run met a tooling gap, and say whether it did.

    The needinfo goes with the run's comment, which says the profile was not read, in
    the run's first update of the bug or in a new one. Nobody is asked twice.
    """
    if not gaps.profile_formats or not owner:
        return False
    if not comment_texts(recorder.actions) or owner in state.bot_logins:
        return False
    found = updates(recorder.actions, bug)
    asked = {r for u in found for r in needinfo_requestees(u.changes)}
    if owner in asked or owner in {n.requestee for n in state.open_needinfos}:
        return False
    flag = {"name": "needinfo", "status": "?", "requestee": owner}
    if found:
        # Hackbot merges a bug's updates with later fields winning, so the request goes
        # in the run's first update. A change made here skips the record hook, which
        # adds `new` to each request.
        changes = found[0].changes
        if len(needinfo_requestees(changes)) >= MAX_NEEDINFOS:
            return False
        changes["flags"] = [*(changes.get("flags") or ()), {**flag, "new": True}]
        return True
    versions = ", ".join(
        f"{a} (reads up to {b})" for a, b in sorted(gaps.profile_formats)
    )
    # The record hook accepts the owner only from here, not from the model's turn.
    state.tooling_owner = owner
    try:
        recorder.record(
            UPDATE_BUG,
            {"bug_id": bug, "changes": {"flags": [flag]}},
            reasoning=f"profiler-cli cannot read profile format {versions}; asks the "
            "tooling owner to bump it.",
        )
    except ToolError:
        # The needinfo rules refuse the owner; the comment still names the gap.
        return False
    return True
