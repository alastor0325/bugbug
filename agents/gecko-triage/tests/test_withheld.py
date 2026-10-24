"""Tests for the bugs that the broker withheld from the run's reads."""

import asyncio
import json

import pytest
from hackbot_agents.gecko_triage.withheld import (
    GET_BUGS_TOOL,
    UNCHECKED_SPILL,
    WithheldBugs,
    inaccessible_ids,
    note_withheld,
    spill_file,
    withheld_hook,
)

HIDDEN = 1234567
COMMENTS_TOOL = "mcp__bugzilla__get_bug_comments"


def _blocks(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def _get_bugs(response):
    return {"tool_name": GET_BUGS_TOOL, "tool_input": {}, "tool_response": response}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"bugs": [], "inaccessible": [HIDDEN, 7654321]}, {HIDDEN, 7654321}),
        ({"bugs": [], "inaccessible": []}, set()),
        ({"inaccessible": ["1234567", True, -1, None]}, set()),
        ({"bugs": []}, None),
        (["not", "a", "dict"], None),
    ],
)
def test_the_withheld_ids_are_the_inaccessible_list(payload, expected):
    assert inaccessible_ids({"content": _blocks(payload)}) == expected


@pytest.mark.parametrize(
    "response",
    [
        {"content": _blocks({"inaccessible": [HIDDEN]})},
        _blocks({"inaccessible": [HIDDEN]}),
        json.dumps({"inaccessible": [HIDDEN]}),
        json.dumps(_blocks({"inaccessible": [HIDDEN]})),
    ],
    ids=["content", "bare-list", "text", "blocks-as-text"],
)
def test_every_result_shape_gives_the_ids(response):
    store = WithheldBugs()
    note_withheld(store, _get_bugs(response))
    assert store == WithheldBugs({HIDDEN})


def test_a_result_that_is_not_json_withholds_nothing():
    store = WithheldBugs()
    note_withheld(store, _get_bugs({"content": [{"type": "text", "text": "oops"}]}))
    assert store == WithheldBugs()


def test_the_hook_collects_every_read():
    store = WithheldBugs()
    hook = withheld_hook(store)
    for ids in ([HIDDEN], [], [7654321]):
        post = _get_bugs({"content": _blocks({"bugs": [], "inaccessible": ids})})
        assert asyncio.run(hook(post, "toolu_1", None)) == {}
    assert store.ids == {HIDDEN, 7654321}


# --- refused reads ---------------------------------------------------------- #

DENIED = {"error": "access_denied", "bug_id": HIDDEN, "hint": "Skip it."}


def test_a_refusal_result_counts_its_bug():
    store = WithheldBugs()
    post = {
        "tool_name": COMMENTS_TOOL,
        "tool_input": {"bug_id": HIDDEN},
        "tool_response": {"content": _blocks(DENIED), "is_error": True},
    }
    note_withheld(store, post)
    assert store.ids == {HIDDEN}


@pytest.mark.parametrize(
    "error", [json.dumps(DENIED), "Error: access_denied", f"MCP error: {DENIED}"]
)
def test_a_failed_refusal_counts_its_bug(error):
    store = WithheldBugs()
    failure = {"tool_name": COMMENTS_TOOL, "tool_input": {"bug_id": HIDDEN}, "error": error}
    note_withheld(store, failure)
    assert store.ids == {HIDDEN}


def test_a_public_comment_that_quotes_access_denied_counts_nothing():
    store = WithheldBugs()
    comments = {"comments": [{"text": "It says access_denied."}], "bug_id": 9000001}
    post = {
        "tool_name": COMMENTS_TOOL,
        "tool_input": {"bug_id": 9000001},
        "tool_response": {"content": _blocks(comments)},
    }
    note_withheld(store, post)
    assert store == WithheldBugs()


def test_another_failure_counts_nothing():
    store = WithheldBugs()
    failure = {"tool_name": COMMENTS_TOOL, "tool_input": {"bug_id": 5}, "error": "timeout"}
    note_withheld(store, failure)
    assert store == WithheldBugs()


# --- a spilled get_bugs result ---------------------------------------------- #


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    folder = tmp_path / "config" / "projects" / "-r" / "s" / "tool-results"
    folder.mkdir(parents=True)
    return folder


def _notice(path):
    return (
        "Error: result (912,334 characters) exceeds maximum allowed tokens. Output has "
        f"been saved to {path}.\n"
    )


def _persisted(path):
    text = f"<persisted-output>\nOutput too large (67KB). Full output saved to: {path}\n"
    return [{"type": "text", "text": text}]


@pytest.mark.parametrize("notice", [_notice, _persisted])
def test_a_readable_spill_file_gives_the_ids(results_dir, notice):
    path = results_dir / "mcp-bugzilla-get_bugs-1.txt"
    path.write_text(json.dumps(_blocks({"bugs": [], "inaccessible": [HIDDEN]})))
    store = WithheldBugs()
    note_withheld(store, _get_bugs(notice(path)))
    assert store == WithheldBugs({HIDDEN})


@pytest.mark.parametrize(
    "content", [None, "not json", json.dumps({"bugs": []}), b"\xff\xfe"]
)
def test_a_spill_that_cannot_be_checked_holds_the_run(results_dir, content):
    path = results_dir / "mcp-bugzilla-get_bugs-2.txt"
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content)
    store = WithheldBugs()
    note_withheld(store, _get_bugs(_notice(path)))
    assert store.unchecked == UNCHECKED_SPILL
    assert store.ids == set()


def test_a_spill_outside_the_tool_results_folder_is_never_read(results_dir, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text(json.dumps({"inaccessible": [HIDDEN]}))
    sideways = results_dir.parent / "x.txt"
    sideways.write_text(json.dumps({"inaccessible": [HIDDEN]}))
    escape = results_dir / ".." / ".." / ".." / ".." / ".." / "elsewhere.txt"
    for path in (outside, sideways, escape):
        assert spill_file(_notice(path)) is None
        store = WithheldBugs()
        note_withheld(store, _get_bugs(_notice(path)))
        assert store == WithheldBugs(set(), UNCHECKED_SPILL)


def test_a_spill_of_another_tool_is_not_read(results_dir):
    store = WithheldBugs()
    post = {
        "tool_name": "mcp__bugzilla__search_bugs",
        "tool_input": {},
        "tool_response": _notice(results_dir / "x.txt"),
    }
    note_withheld(store, post)
    assert store == WithheldBugs()
