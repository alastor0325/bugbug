"""The comment numbers that a triage comment cites.

A cited comment of another bug must be one that the run read, and a sentence that
cites a comment for a patch or an attachment must cite the comment that adds it. The
hook on the broker tools notes each comment that a `get_bug_comments` or a `get_bugs`
result holds. The triaged bug's comments come from the run's state. A result saved to
a file is not noted, so a citation from it is refused until the run reads the comment
again in a smaller page.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .actions_view import is_bug_id
from .withheld import GET_BUGS_TOOL, payloads

COMMENTS_TOOL = "mcp__bugzilla__get_bug_comments"

_CROSS = re.compile(r"\bbug\s+(\d+)\s*,?\s*\(?comment\s+(\d+)\)?", re.IGNORECASE)
_OWN = re.compile(r"\bcomment\s+(\d+)\b", re.IGNORECASE)
_PATCH = re.compile(r"\b(?:patch|attachment)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class ReadComments:
    """Each comment the run read, as (bug, comment number): whether it adds an attachment."""

    seen: dict[tuple[int, int], bool] = field(default_factory=dict)


def note_comments(store: ReadComments, bug: object, comments: Iterable[object]) -> None:
    if not is_bug_id(bug):
        return
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        count = comment.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            store.seen[(bug, count)] = bool(comment.get("attachment_id"))


def note_read(store: ReadComments, input_data: dict) -> None:
    tool = input_data.get("tool_name")
    if tool not in (COMMENTS_TOOL, GET_BUGS_TOOL):
        return
    for payload in payloads(input_data.get("tool_response")):
        if tool == COMMENTS_TOOL:
            note_comments(store, payload.get("bug_id"), payload.get("comments") or ())
            continue
        for bug in payload.get("bugs") or ():
            if isinstance(bug, dict):
                note_comments(store, bug.get("id"), bug.get("comments") or ())


def read_hook(store: ReadComments):
    """The PostToolUse hook on the broker tools."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_read(store, input_data)
        return {}

    return hook


def _citations(sentence: str, bug: int) -> list[tuple[int, int]]:
    cited = [(int(b), int(n)) for b, n in _CROSS.findall(sentence)]
    rest = _CROSS.sub(" ", sentence)
    return cited + [(bug, int(n)) for n in _OWN.findall(rest)]


def citation_refusal(text: str, bug: int, store: ReadComments) -> str | None:
    """Why the comment cites a comment wrongly, or None."""
    for sentence in _SENTENCE_END.split(text):
        for cited_bug, count in _citations(sentence, bug):
            where = f"comment {count} of bug {cited_bug}"
            if (cited_bug, count) not in store.seen:
                if cited_bug == bug and not any(b == bug for b, _ in store.seen):
                    continue
                return (
                    f"your comment cites {where}, which this run did not read. Read "
                    f"it with get_bug_comments (start={count}, limit=1) before you "
                    "cite it, or remove the citation"
                )
            if _PATCH.search(sentence) and not store.seen[(cited_bug, count)]:
                return (
                    f"{where} adds no attachment, but your sentence cites it for a "
                    "patch or an attachment. Cite the comment that adds it, the one "
                    'that starts with "Created attachment"'
                )
    return None
