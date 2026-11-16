"""The limits on a run that removes its recorded actions to record them again.

The comment hook allows one comment per run, so a run that finds an error in its recorded
comment removes it with hackbot-runtime's `remove_action`, after `list_actions` gives the
action IDs, and records it again. `add_comment_hook` refuses a new comment while a field
change is still recorded, so the run removes every field change too, records the comment
first, and every record hook runs again in order.

A run may remove its comment `MAX_COMMENT_REMOVALS` times, and only the main agent may
remove actions. A run that removed its comment and ends with no comment fails, unless its
decision needs no comment.
"""

from dataclasses import dataclass

from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names

from .actions_view import ADD_COMMENT
from .closing import deny
from .config import LIST_ACTIONS_TYPE, NO_COMMENT_DECISIONS, REMOVE_ACTION_TYPE

LIST_ACTIONS_TOOL, REMOVE_ACTION_TOOL = actions_to_tool_names(
    [LIST_ACTIONS_TYPE, REMOVE_ACTION_TYPE]
)
MAX_COMMENT_REMOVALS = 2


@dataclass
class Removals:
    """The run's recorder and the number of times the run removed its comment."""

    recorder: ActionsRecorder
    comments: int = 0


def _removes_the_comment(removals: Removals, input_data: dict) -> bool:
    action_id = (input_data.get("tool_input") or {}).get("action_id")
    return any(
        a["action_id"] == action_id and a["type"] == ADD_COMMENT
        for a in removals.recorder.list_actions()
    )


def removal_refusal(removals: Removals, input_data: dict) -> str | None:
    """Why a call to `remove_action` is refused, or None.

    The CLI gives a hook ``agent_id`` and ``agent_type`` only for a subagent. An unknown
    action ID is left to the tool, which refuses it.
    """
    if input_data.get("agent_id") or input_data.get("agent_type"):
        return "only the main agent removes its recorded actions"
    if (
        _removes_the_comment(removals, input_data)
        and removals.comments >= MAX_COMMENT_REMOVALS
    ):
        return (
            f"you removed the recorded comment {MAX_COMMENT_REMOVALS} times already; "
            "keep the actions as they are and name the error in your final message"
        )
    return None


def removal_guard(removals: Removals):
    """The PreToolUse hook on `remove_action`, bound to ``removals``.

    It counts each comment removal that it lets through.
    """

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        reason = removal_refusal(removals, input_data)
        if reason is not None:
            return deny(reason)
        if _removes_the_comment(removals, input_data):
            removals.comments += 1
        return {}

    return hook


def missing_comment_refusal(
    removals: Removals, actions: list[dict], decision: str | None
) -> str | None:
    """Why a run that removed its comment fails: it did not record a comment again.

    The run then fails, so BugBot runs it again, instead of ending ok with no comment.
    """
    if not removals.comments or decision in NO_COMMENT_DECISIONS:
        return None
    if any(a["type"] == ADD_COMMENT for a in actions):
        return None
    return (
        "the run removed its recorded comment and did not record a comment again, "
        f"but the decision is {decision}"
    )
