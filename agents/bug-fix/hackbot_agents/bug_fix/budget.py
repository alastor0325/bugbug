"""The time budget of a team run.

The platform stops a run at its execution timeout, and a stopped run applies nothing.
Before that, the budget hook tells the agent once that its time is up, so it can end
with a gated patch or a comment that says what it checked. If the run goes on past
the budget by ``HARD_STOP_GRACE_MINUTES``, ``run_bug_fix`` stops the session itself,
so the result and the actions recorded so far are still written.
"""

import time
from collections.abc import Callable

DEFAULT_MINUTES = 100
HARD_STOP_GRACE_MINUTES = 20
# No single Bash command, such as a build or a test run, may take longer than this.
BASH_MAX_TIMEOUT_MS = 15 * 60 * 1000

FINISH = (
    "Your time budget is used up. Start no new work. If all the gates pass on the "
    "current patch, record it now. If not, record a Bugzilla comment that says what you "
    "checked, what passed, and what is left."
)


def hard_stop_seconds(minutes: int) -> float:
    return (minutes + HARD_STOP_GRACE_MINUTES) * 60


def budget_hook(minutes: int, *, clock: Callable[[], float] = time.monotonic):
    """The PostToolUse hook that tells the agent once, when its budget is used up."""
    deadline = clock() + minutes * 60
    warned = False

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        nonlocal warned
        if warned or clock() < deadline:
            return {}
        warned = True
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": FINISH,
            }
        }

    return hook
