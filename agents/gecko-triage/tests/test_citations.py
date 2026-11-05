"""Tests for the comment numbers that a triage comment cites."""

import asyncio
import json

import pytest
from hackbot_agents.gecko_triage.citations import (
    COMMENTS_TOOL,
    ReadComments,
    citation_refusal,
    note_comments,
    note_read,
    read_hook,
)
from hackbot_agents.gecko_triage.withheld import GET_BUGS_TOOL

BUG = 9000001
OTHER = 9000002


def _comment(count, attachment_id=None):
    return {"count": count, "attachment_id": attachment_id, "text": "x"}


def _read(*comments, bug=OTHER):
    store = ReadComments()
    note_comments(store, BUG, [_comment(0), _comment(1, 555)])
    note_comments(store, bug, list(comments))
    return store


def _blocks(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def test_a_get_bug_comments_result_is_noted():
    store = ReadComments()
    payload = {"bug_id": OTHER, "comments": [_comment(3, 777), _comment(4)]}
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": _blocks(payload)})
    assert store.seen == {(OTHER, 3): True, (OTHER, 4): False}


def test_the_comments_of_a_get_bugs_result_are_noted():
    store = ReadComments()
    payload = {"bugs": [{"id": OTHER, "comments": [_comment(2)]}], "inaccessible": []}
    note_read(store, {"tool_name": GET_BUGS_TOOL, "tool_response": _blocks(payload)})
    assert store.seen == {(OTHER, 2): False}


def test_another_tool_and_a_bad_result_note_nothing():
    store = ReadComments()
    note_read(store, {"tool_name": "mcp__searchfox__get_file", "tool_response": "x"})
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": "not json"})
    bad = {"bug_id": "9000002", "comments": [{"count": "3"}, "x"]}
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": _blocks(bad)})
    assert store.seen == {}


def test_the_read_hook_notes_the_result():
    store = ReadComments()
    payload = {"bug_id": OTHER, "comments": [_comment(1)]}
    hook = read_hook(store)
    result = asyncio.run(
        hook({"tool_name": COMMENTS_TOOL, "tool_response": _blocks(payload)}, None, None)
    )
    assert result == {} and (OTHER, 1) in store.seen


def test_a_cited_comment_of_another_bug_must_have_been_read():
    store = _read(_comment(3))
    reason = citation_refusal(f"See bug {OTHER} comment 5.", BUG, store)
    assert f"comment 5 of bug {OTHER}" in reason and "get_bug_comments" in reason
    assert citation_refusal(f"See bug {OTHER} comment 3.", BUG, store) is None


@pytest.mark.parametrize(
    "text",
    [
        f"Your WIP patch on bug {OTHER} (comment 4) already has some of these checks.",
        f"The patch in bug {OTHER}, comment 4, has some of them.",
        f"The attachment in bug {OTHER} comment 4 shows it.",
    ],
)
def test_a_patch_is_cited_by_the_comment_that_adds_it(text):
    store = _read(_comment(3, 777), _comment(4))
    reason = citation_refusal(text, BUG, store)
    assert "comment 4" in reason and "adds no attachment" in reason
    assert citation_refusal(text.replace("comment 4", "comment 3"), BUG, store) is None


def test_a_comment_cited_for_something_else_needs_no_attachment():
    store = _read(_comment(4))
    text = f"The reporter tested it in bug {OTHER} (comment 4)."
    assert citation_refusal(text, BUG, store) is None


def test_a_comment_of_the_triaged_bug_is_checked_too():
    store = _read()
    assert citation_refusal("As comment 1 shows, the patch works.", BUG, store) is None
    reason = citation_refusal("The patch in comment 0 fixes it.", BUG, store)
    assert "comment 0" in reason and "adds no attachment" in reason
    assert "comment 9" in citation_refusal("See comment 9.", BUG, store)


def test_a_comment_with_no_citation_passes():
    assert citation_refusal("The patch fixes it.", BUG, ReadComments()) is None
