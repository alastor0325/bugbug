"""Tests for the limits on a run that removes its recorded actions to record them again."""

import pytest
from agent_tools.registry import ToolError
from conftest import (
    DUPE_TARGET,
    KNOWN_META,
    SHORT_NOTE,
    _comment,
    _recorder,
    _text,
    _update,
    remove_as_the_agent,
)
from hackbot_agents.gecko_triage import closing, hooks
from hackbot_agents.gecko_triage.closing import (
    CloseVerdicts,
    record_close_verdict_tool,
    recorded_close,
    spawn_refusal,
)
from hackbot_agents.gecko_triage.removals import (
    LIST_ACTIONS_TOOL,
    MAX_COMMENT_REMOVALS,
    REMOVE_ACTION_TOOL,
    Removals,
    missing_comment_refusal,
    removal_guard,
)

INVALID = {"status": "RESOLVED", "resolution": "INVALID"}
DUPLICATE = {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": DUPE_TARGET}


async def _remove_one(rec, removals, action_id, **caller):
    return await remove_as_the_agent(removal_guard(removals), rec, action_id, **caller)


async def _remove(rec, removals=None):
    """Remove every recorded action, the comment first, as the rules tell the agent."""
    removals = removals or Removals(rec)
    for action in rec.list_actions():
        await _remove_one(rec, removals, action["action_id"])
    return removals


async def _verdict(store, resolution="INVALID", verdict="confirm", dupe_of=None):
    await record_close_verdict_tool(
        store, resolution, verdict, "checked", dupe_of=dupe_of
    )


async def test_the_runtime_tools_remove_the_comment_and_every_field_change(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, "DUPLICATE", dupe_of=DUPE_TARGET)
    _comment(rec)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    removals = await _remove(rec)
    assert rec.actions == []
    assert removals.comments == 1


async def test_a_new_comment_waits_until_every_field_change_is_removed(new_state):
    # The comment comes first again, so every field change is checked against it.
    rec = _recorder(new_state)
    removals = Removals(rec)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    comment, update = rec.list_actions()
    await _remove_one(rec, removals, comment["action_id"])
    with pytest.raises(ToolError, match="remove every recorded field change"):
        _comment(rec)
    await _remove_one(rec, removals, update["action_id"])
    _comment(rec)
    assert [a["type"] for a in rec.actions] == ["bugzilla.add_comment"]


async def test_a_re_record_runs_every_comment_hook_again(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    await _remove(rec)
    with pytest.raises(ToolError, match="dash as an aside"):
        _comment(rec, text=_text("The decoder fails -- always."))
    with pytest.raises(ToolError, match="publicly"):
        _comment(rec, is_private=True)
    with pytest.raises(ToolError, match="links to"):
        _comment(rec, text=_text("See https://example.com/x."))
    assert rec.actions == []
    _comment(rec)
    with pytest.raises(ToolError, match="already recorded a comment"):
        _comment(rec)
    assert len(rec.actions) == 1


async def test_a_re_record_runs_every_update_hook_again(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    await _remove(rec)
    with pytest.raises(ToolError, match="record the comment first"):
        _update(rec, {"severity": "S3", "priority": "P3"})
    _comment(rec)
    with pytest.raises(ToolError, match="never set S1"):
        _update(rec, {"severity": "S1"})
    _update(rec, {"severity": "S3", "priority": "P3"})
    with pytest.raises(ToolError, match="already recorded"):
        _update(rec, {"keywords": {"add": ["stalled"]}})
    assert [a["type"] for a in rec.actions] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
    ]


async def test_a_duplicate_target_change_needs_the_duplicate_again(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, "DUPLICATE", dupe_of=DUPE_TARGET)
    _comment(rec)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    await _remove(rec)
    _comment(rec)
    with pytest.raises(ToolError, match="after the DUPLICATE change is recorded"):
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert len(rec.actions) == 3


async def test_the_run_may_remove_its_comment_twice_and_no_more(new_state):
    rec = _recorder(new_state)
    removals = Removals(rec)
    for _ in range(MAX_COMMENT_REMOVALS):
        _comment(rec)
        await _remove(rec, removals)
    _comment(rec)
    (comment,) = rec.list_actions()
    with pytest.raises(ToolError, match=f"{MAX_COMMENT_REMOVALS} times already"):
        await _remove_one(rec, removals, comment["action_id"])
    assert len(rec.actions) == 1
    assert removals.comments == MAX_COMMENT_REMOVALS


async def test_a_field_change_removal_is_not_counted(new_state):
    rec = _recorder(new_state)
    removals = Removals(rec)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    (_, update) = rec.list_actions()
    await _remove_one(rec, removals, update["action_id"])
    assert removals.comments == 0


async def test_an_unknown_action_id_is_left_to_the_tool(new_state):
    rec = _recorder(new_state)
    removals = Removals(rec)
    with pytest.raises(ToolError, match="No recorded action"):
        await _remove_one(rec, removals, "nope")
    assert removals.comments == 0


def test_the_comment_refusal_names_the_tools():
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _comment(rec)
    assert LIST_ACTIONS_TOOL in str(e.value) and REMOVE_ACTION_TOOL in str(e.value)
    assert "revising it" not in str(e.value)


async def test_only_the_main_agent_may_remove_the_actions(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    (comment,) = rec.list_actions()
    removals = Removals(rec)
    for caller in (
        {"agent_type": "bug_investigator", "agent_id": "a1"},
        {"agent_type": closing.VERIFIER},
        {"agent_id": "a2"},
    ):
        with pytest.raises(ToolError, match="only the main agent"):
            await _remove_one(rec, removals, comment["action_id"], **caller)
    assert len(rec.actions) == 1 and removals.comments == 0


# --- the close verdict state ------------------------------------------------- #


async def test_a_confirm_before_the_first_comment_still_covers_a_re_record(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store)
    _comment(rec)
    _update(rec, INVALID)
    await _remove(rec)
    _comment(rec)
    _update(rec, INVALID)
    assert recorded_close(rec.actions) == ("INVALID", None)
    assert store.outcome(("INVALID", None)) == "confirmed"
    assert store.warning(("INVALID", None)) is None


async def test_a_verdict_after_the_first_comment_stays_late(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    await _verdict(store)
    await _remove(rec)
    assert store.comment_recorded
    _comment(rec)
    with pytest.raises(ToolError, match="after you recorded the comment"):
        _update(rec, INVALID)
    assert not store.confirms(("INVALID", None))
    assert store.warning(None) is not None


async def test_the_verifier_is_not_spawned_after_a_removal(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    await _remove(rec)
    spawn = {"tool_input": {"subagent_type": closing.VERIFIER}}
    assert spawn_refusal(store, spawn)
    assert store.verifier_spawns == 0


async def test_a_refute_still_refuses_the_close_and_a_closing_comment(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, verdict="refute")
    _comment(rec)
    await _remove(rec)
    with pytest.raises(ToolError, match="refuted the close"):
        _comment(rec, text=_text("This bug is INVALID."))
    _comment(rec)
    with pytest.raises(ToolError, match="refuted closing this bug"):
        _update(rec, INVALID)
    assert store.outcome(None) == "refuted"


async def test_a_refused_close_still_warns_after_a_re_record_without_it(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, INVALID)
    await _remove(rec)
    _comment(rec, text=SHORT_NOTE)
    assert store.refused == [("INVALID", None)]
    assert "refused 1 close(s)" in store.warning(recorded_close(rec.actions))


def test_the_hooks_name_the_removals_in_their_docstring():
    assert "removals.py" in hooks.__doc__


@pytest.mark.parametrize("decision", ["needs_info", "analyzed", "invalid", None])
def test_a_removal_with_no_comment_again_fails_the_run(new_state, decision):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    removals = Removals(rec, comments=1)
    for action in rec.list_actions():
        rec.remove_action(action["action_id"])
    reason = missing_comment_refusal(removals, rec.list_actions(), decision)
    assert "did not record a comment again" in reason


def test_a_removal_with_a_comment_again_or_no_comment_needed_passes(new_state):
    rec = _recorder(new_state)
    assert missing_comment_refusal(Removals(rec, comments=1), [], "watching") is None
    assert missing_comment_refusal(Removals(rec), [], "needs_info") is None
    _comment(rec)
    assert (
        missing_comment_refusal(
            Removals(rec, comments=1), rec.list_actions(), "analyzed"
        )
        is None
    )
