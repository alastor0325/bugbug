"""The bugs that the broker withheld from the run's reads.

The broker serves public bugs only. `get_bugs` lists each requested bug it withholds as
`inaccessible`, and the other reads of one bug refuse a restricted bug with
`access_denied`. The hooks collect those ids from the main agent's reads and the
subagents' reads. The post-run check holds the run for a person when it met one, and
refuses a comment that contains its number.

A large result reaches the hook as a spill notice, with the result in a file. The hook
reads that file only when it is in the CLI's tool-results folder. When it cannot check
the file, the run waits for a person.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

from .actions_view import is_bug_id
from .closing import is_spill_notice, response_texts, spill_file

GET_BUGS_TOOL = "mcp__bugzilla__get_bugs"
BROKER_TOOLS_MATCHER = r"mcp__bugzilla__.*"
ACCESS_DENIED = "access_denied"
UNCHECKED_SPILL = "a get_bugs result was saved to a file and could not be checked"
UNCHECKED_RESULT = "a get_bugs result could not be checked"
# A larger saved file is not read, and holds the run; the SDK buffer has the same cap.
MAX_SPILL_BYTES = 32 * 1024 * 1024


@dataclass
class WithheldBugs:
    """The ids the broker withheld, and why a read could not be checked."""

    ids: set[int] = field(default_factory=set)
    unchecked: str | None = None


def _json(text: object) -> object:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def payloads(response: object) -> Iterator[dict]:
    """The JSON objects of a result, also when a text holds the content blocks."""
    for text in response_texts(response):
        data = _json(text) if isinstance(text, str) else text
        if isinstance(data, list) or (isinstance(data, dict) and "content" in data):
            for inner in response_texts(data):
                if isinstance(payload := _json(inner), dict):
                    yield payload
        elif isinstance(data, dict):
            yield data


def inaccessible_ids(response: object) -> set[int] | None:
    """The ids a `get_bugs` result lists as `inaccessible`; None with no such list."""
    lists = [p["inaccessible"] for p in payloads(response) if "inaccessible" in p]
    if not lists:
        return None
    return {
        i
        for listed in lists
        if isinstance(listed, list)
        for i in listed
        if is_bug_id(i)
    }


def denied_bug_id(text: object, tool_input: object, failed: bool = False) -> int | None:
    """The bug of an `access_denied` refusal: its payload `bug_id`.

    A ``failed`` call's error text may not hold the payload: then the input's `bug_id`.
    """
    if not isinstance(text, str) or ACCESS_DENIED not in text:
        return None
    for payload in payloads(text):
        if payload.get("error") == ACCESS_DENIED and is_bug_id(payload.get("bug_id")):
            return payload["bug_id"]
    bug_id = (
        tool_input.get("bug_id") if failed and isinstance(tool_input, dict) else None
    )
    return bug_id if is_bug_id(bug_id) else None


def _spilled_ids(response: object) -> set[int] | None:
    path = spill_file(response)
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_SPILL_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    return inaccessible_ids(text)


def _is_error(response: object) -> bool:
    """Whether a result is a tool error: it carries no bug, so it withholds none."""
    if isinstance(response, dict) and response.get("is_error"):
        return True
    return any("error" in payload for payload in payloads(response))


def note_withheld(store: WithheldBugs, input_data: dict) -> None:
    """Add the bugs that one broker result or refusal withheld to ``store``.

    A PostToolUseFailure input carries ``error`` in place of ``tool_response``.
    """
    tool_input = input_data.get("tool_input")
    failed = "error" in input_data
    if failed:
        texts = [input_data.get("error")]
    else:
        response = input_data.get("tool_response")
        if input_data.get("tool_name") == GET_BUGS_TOOL:
            spilled = is_spill_notice(response)
            ids = _spilled_ids(response) if spilled else inaccessible_ids(response)
            if ids is None and (spilled or not _is_error(response)):
                reason = UNCHECKED_SPILL if spilled else UNCHECKED_RESULT
                if store.unchecked is None:
                    store.unchecked = reason
                elif reason not in store.unchecked:
                    store.unchecked = f"{store.unchecked}; {reason}"
            store.ids |= ids or set()
        texts = response_texts(response)
    for text in texts:
        if (bug_id := denied_bug_id(text, tool_input, failed)) is not None:
            store.ids.add(bug_id)


def withheld_hook(store: WithheldBugs):
    """The PostToolUse and PostToolUseFailure hook on the broker tools."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_withheld(store, input_data)
        return {}

    return hook
