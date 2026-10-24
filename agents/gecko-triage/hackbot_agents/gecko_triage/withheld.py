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
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .actions_view import is_bug_id
from .closing import is_spill_notice, response_texts

GET_BUGS_TOOL = "mcp__bugzilla__get_bugs"
BROKER_TOOLS_MATCHER = r"mcp__bugzilla__.*"
ACCESS_DENIED = "access_denied"
UNCHECKED_SPILL = "a get_bugs result was saved to a file and could not be checked"
# The path in the spill notices of `closing`: the MCP notice and the persisted output.
_SPILL_PATH = re.compile(
    r"(?:Output has been saved to|Full output saved to:) (\S+?)\.?(?=\s|$)"
)
SPILL_FOLDER = "tool-results"


@dataclass
class WithheldBugs:
    """The ids the broker withheld, and why a read could not be checked."""

    ids: set[int] = field(default_factory=set)
    unchecked: str | None = None


def spill_root() -> Path:
    """The folder of the CLI's session files; a spill file must be under it."""
    config = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(config) / "projects"


def _json(text: object) -> object:
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _payloads(response: object) -> Iterator[dict]:
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
    lists = [p["inaccessible"] for p in _payloads(response) if "inaccessible" in p]
    if not lists:
        return None
    return {
        i
        for listed in lists
        if isinstance(listed, list)
        for i in listed
        if is_bug_id(i)
    }


def denied_bug_id(
    text: object, tool_input: object, failed: bool = False
) -> int | None:
    """The bug of an `access_denied` refusal: its payload `bug_id`.

    A ``failed`` call's error text may not hold the payload: then the input's `bug_id`.
    """
    if not isinstance(text, str) or ACCESS_DENIED not in text:
        return None
    for payload in _payloads(text):
        if payload.get("error") == ACCESS_DENIED and is_bug_id(payload.get("bug_id")):
            return payload["bug_id"]
    bug_id = tool_input.get("bug_id") if failed and isinstance(tool_input, dict) else None
    return bug_id if is_bug_id(bug_id) else None


def spill_file(response: object) -> Path | None:
    """The file a spill notice names, only when it is in a tool-results folder."""
    root = spill_root().resolve()
    for text in response_texts(response):
        match = _SPILL_PATH.search(text) if isinstance(text, str) else None
        if not match:
            continue
        path = Path(match[1]).resolve()
        if path.is_relative_to(root) and path.parent.name == SPILL_FOLDER:
            return path
    return None


def _spilled_ids(response: object) -> set[int] | None:
    path = spill_file(response)
    if path is None:
        return None
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return inaccessible_ids(text)


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
            if spilled and ids is None:
                store.unchecked = UNCHECKED_SPILL
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
