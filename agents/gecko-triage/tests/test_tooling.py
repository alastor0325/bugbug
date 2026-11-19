import asyncio

import pytest
from agent_tools.registry import ToolError
from conftest import (
    BOT_ACCOUNT,
    BUG,
    NOW,
    _comment,
    _recorder,
    _update,
    with_needinfo,
)
from hackbot_agents.gecko_triage import tooling
from hackbot_agents.gecko_triage.agent import session_options
from hackbot_agents.gecko_triage.authors import CommitFacts
from hackbot_agents.gecko_triage.closing import CloseVerdicts
from hackbot_agents.gecko_triage.investigations import Investigations
from hackbot_agents.gecko_triage.state import TriageState

OWNER = "alwu@mozilla.com"
REFUSAL = (
    "Error: Profile load failed: This profile is version 78, but this profiler-cli "
    "only supports up to version 77 of the processed profile format.\n"
    "Update to the latest version with:\n"
    "  npm install -g @firefox-devtools/profiler-cli@latest\n"
)


def _owner_flag():
    return {"name": "needinfo", "status": "?", "requestee": OWNER, "new": True}


def _gaps():
    gaps = tooling.ToolingGaps()
    gaps.profile_formats.add((78, 77))
    return gaps


@pytest.fixture
def commented(bug_new, comments_new):
    """A state with no open needinfo on the owner, and a recorder with the run's comment."""
    state = TriageState(bug=bug_new, comments=comments_new, now=NOW)
    rec = _recorder(state)
    _comment(rec)
    return state, rec


def test_a_format_refusal_in_a_bash_result_is_a_gap():
    assert tooling.profile_format_gaps({"stdout": REFUSAL, "stderr": ""}) == {(78, 77)}
    assert tooling.profile_format_gaps(
        {"content": [{"type": "text", "text": REFUSAL}]}
    ) == {(78, 77)}
    assert tooling.profile_format_gaps([REFUSAL, {"stderr": REFUSAL}]) == {(78, 77)}


def test_other_output_is_no_gap():
    for response in (
        {"stdout": "Loaded profile into session ap-1", "stderr": ""},
        {"stdout": "This profile is version 78", "stderr": ""},
        None,
        42,
    ):
        assert tooling.profile_format_gaps(response) == set()


def test_the_bash_hook_collects_the_gap_and_answers_as_a_hook():
    gaps = tooling.ToolingGaps()
    hook = tooling.gap_hook(gaps)
    answer = asyncio.run(
        hook({"tool_response": {"stdout": REFUSAL, "stderr": ""}}, None, None)
    )
    assert answer == {}
    assert gaps.profile_formats == {(78, 77)}
    asyncio.run(hook({"tool_response": {"stdout": "ok", "stderr": ""}}, None, None))
    assert gaps.profile_formats == {(78, 77)}


def test_the_bash_hook_reads_the_error_of_a_failed_call():
    gaps = tooling.ToolingGaps()
    asyncio.run(tooling.gap_hook(gaps)({"error": REFUSAL}, None, None))
    assert gaps.profile_formats == {(78, 77)}


def test_a_gap_adds_the_owner_to_the_runs_update(commented):
    state, rec = commented
    _update(rec, {"priority": "P3"})
    assert tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    updates = [a for a in rec.actions if a["type"] == "bugzilla.update_bug"]
    assert len(updates) == 1
    assert updates[0]["params"]["changes"]["flags"] == [_owner_flag()]


def test_a_gap_gets_its_own_update_when_the_run_has_none(commented):
    state, rec = commented
    assert tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    updates = [a for a in rec.actions if a["type"] == "bugzilla.update_bug"]
    assert [u["params"]["changes"] for u in updates] == [{"flags": [_owner_flag()]}]


def test_no_gap_no_owner_or_no_comment_asks_nobody(commented):
    state, rec = commented
    assert not tooling.ask_tooling_owner(rec, BUG, state, tooling.ToolingGaps(), OWNER)
    assert not tooling.ask_tooling_owner(rec, BUG, state, _gaps(), None)
    silent = _recorder(state)
    assert not tooling.ask_tooling_owner(silent, BUG, state, _gaps(), OWNER)
    assert [a["type"] for a in rec.actions] == ["bugzilla.add_comment"]
    assert silent.actions == []


def test_an_owner_with_an_open_needinfo_is_not_asked_again(bug_new, comments_new):
    bug = with_needinfo(dict(bug_new), OWNER, setter="person@example.com")
    state = TriageState(bug=bug, comments=comments_new, now=NOW)
    rec = _recorder(state)
    _comment(rec)
    assert not tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    assert [a["type"] for a in rec.actions] == ["bugzilla.add_comment"]


def test_an_owner_the_run_already_asked_is_not_added_twice(commented):
    state, rec = commented
    _update(rec, {"flags": [{"name": "needinfo", "status": "?", "requestee": OWNER}]})
    assert not tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    assert rec.actions[-1]["params"]["changes"]["flags"] == [_owner_flag()]


def test_every_session_collects_tooling_gaps_from_bash():
    gaps = tooling.ToolingGaps()
    options = session_options(
        CloseVerdicts(), CommitFacts(), Investigations(), tooling_gaps=gaps
    )
    for event in ("PostToolUse", "PostToolUseFailure"):
        bash = [m for m in options["hooks"][event] if m.matcher == tooling.BASH_TOOL]
        assert bash, event


def test_an_owner_the_needinfo_rules_refuse_is_skipped(commented):
    state, rec = commented
    assert not tooling.ask_tooling_owner(rec, BUG, state, _gaps(), BOT_ACCOUNT)
    assert [a["type"] for a in rec.actions] == ["bugzilla.add_comment"]


def test_a_run_with_no_team_lists_still_asks_the_owner(
    bug_new, comments_new, monkeypatch
):
    from hackbot_agents.gecko_triage import state as state_module

    monkeypatch.setattr(state_module, "NEEDINFO_EXPERTS", frozenset())
    monkeypatch.setattr(state_module, "NEEDINFO_FALLBACK", frozenset())
    state = TriageState(bug=bug_new, comments=comments_new, now=NOW)
    assert OWNER not in state.people
    rec = _recorder(state)
    _comment(rec)
    assert tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    assert rec.actions[-1]["params"]["changes"] == {"flags": [_owner_flag()]}


def test_the_model_may_not_ask_the_owner_before_the_run_ends(
    bug_new, comments_new, monkeypatch
):
    from hackbot_agents.gecko_triage import state as state_module

    monkeypatch.setattr(state_module, "NEEDINFO_EXPERTS", frozenset())
    monkeypatch.setattr(state_module, "NEEDINFO_FALLBACK", frozenset())
    state = TriageState(bug=bug_new, comments=comments_new, now=NOW)
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(
            rec, {"flags": [{"name": "needinfo", "status": "?", "requestee": OWNER}]}
        )


def test_a_full_update_gets_no_owner(bug_new, comments_new):
    asks = [f"p{i}@example.com" for i in range(3)]
    bug = dict(bug_new, cc=[*bug_new.get("cc", ()), *asks])
    state = TriageState(bug=bug, comments=comments_new, now=NOW)
    rec = _recorder(state)
    _comment(rec)
    _update(
        rec,
        {"flags": [{"name": "needinfo", "status": "?", "requestee": a} for a in asks]},
    )
    assert not tooling.ask_tooling_owner(rec, BUG, state, _gaps(), OWNER)
    flags = rec.actions[-1]["params"]["changes"]["flags"]
    assert [f["requestee"] for f in flags] == asks
