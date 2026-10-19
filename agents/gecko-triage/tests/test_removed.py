"""One regression guard for the memory the agent no longer keeps on a bug.

The agent used to find its own comments by their tag, end each comment with a footer and
read that footer back. A dry run could replay another account's history. All of it is
gone, and none of it may come back.
"""

import importlib.util
import inspect
from pathlib import Path

from hackbot_agents.gecko_triage import agent, hooks
from hackbot_agents.gecko_triage.__main__ import AgentInputs
from hackbot_agents.gecko_triage.state import TriageState, fetch_state

PACKAGE = Path(__file__).parents[1] / "hackbot_agents" / "gecko_triage"

REMOVED = {
    "state": (
        TriageState(bug={}),
        (
            "_ours",
            "our_comments",
            "been_here",
            "last_comment",
            "last_comment_time",
            "last_footer",
            "anything_new",
            "waiting_on",
            "stale_needinfos",
            "comments_by_author",
            "bot_account",
            "reporter_commented_after",
        ),
    ),
    "hooks": (hooks, ("footer_hook", "footer_module", "_body", "_check_capture_block")),
    "agent": (agent, ("footer_module", "capture_block_refusal")),
}

# Model-facing phrases of the old design.
REMOVED_TEXT = (
    "footer",
    "returning bug",
    "has been here",
    "what it asked last time",
    "when the agent has commented before",
    "agent's last comment",
    "you come back to a bug",
    "first time the agent looks at this bug",
)


def test_the_removed_memory_stays_removed():
    for where, (owner, names) in REMOVED.items():
        for name in names:
            assert not hasattr(owner, name), f"{where}.{name}"
    assert importlib.util.find_spec("hackbot_agents.gecko_triage.footer") is None
    params = inspect.signature(fetch_state).parameters
    assert "bot_account" not in params and "comments_by_author" not in params
    assert "bot_account" not in AgentInputs.model_fields

    texts = {
        str(path.relative_to(PACKAGE)): " ".join(path.read_text().lower().split())
        for path in (
            PACKAGE / "prompts" / "system.md",
            *(PACKAGE / "rules").glob("*.md"),
            *(PACKAGE / "teams").glob("*/rules/*.md"),
        )
    }
    assert any(name.startswith("teams/") for name in texts)
    for name, text in texts.items():
        for phrase in REMOVED_TEXT:
            assert phrase not in text, f"{phrase!r} in {name}"
