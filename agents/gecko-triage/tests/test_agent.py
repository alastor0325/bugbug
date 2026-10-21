"""Tests for one run of the agent, with the Claude session replaced by a fake."""

import contextlib

import pytest
from agent_tools.registry import ToolError
from claude_agent_sdk import SystemMessage
from conftest import (
    BUG,
    NOW,
    FakeClient,
    _fake_fetch,
    _record_verdict,
    _result,
    _run,
)
from hackbot_agents.gecko_triage import agent, attachments, closing, dupes, rerecord
from hackbot_agents.gecko_triage.__main__ import AgentInputs
from hackbot_agents.gecko_triage.authors import CommitFacts
from hackbot_agents.gecko_triage.closing import CloseVerdicts
from hackbot_runtime import AgentError
from hackbot_runtime.actions import ActionsRecorder


async def test_a_run_has_a_turn_and_a_cost_limit_by_default(
    fake_client, tmp_path, new_state
):
    await _run(tmp_path, new_state)
    assert fake_client.options.max_turns == agent.DEFAULT_MAX_TURNS
    assert fake_client.options.max_budget_usd == agent.DEFAULT_MAX_BUDGET_USD


async def test_the_limits_can_be_overridden(fake_client, tmp_path, new_state):
    await _run(tmp_path, new_state, max_turns=7, max_budget_usd=1.5)
    assert fake_client.options.max_turns == 7
    assert fake_client.options.max_budget_usd == 1.5


async def test_the_run_offers_fetch_attachment_bound_to_its_bug(
    fake_client, tmp_path, new_state, monkeypatch
):
    bound = []
    real = attachments.attachments_server
    monkeypatch.setattr(
        attachments, "attachments_server", lambda bug: bound.append(bug) or real(bug)
    )
    await _run(tmp_path, new_state)
    options = fake_client.options
    assert bound == [BUG]
    assert options.mcp_servers["attachments"]["type"] == "sdk"
    assert "mcp__attachments__fetch_attachment" in options.allowed_tools
    agents = options.agents
    for name in ("bug_investigator", "log_analyst", "investigator"):
        assert "mcp__attachments__fetch_attachment" in agents[name].tools, name


async def test_the_run_offers_search_concepts_to_the_duplicate_hunter_only(
    fake_client, tmp_path, new_state, monkeypatch
):
    bound = []
    real = dupes.concepts_server
    monkeypatch.setattr(
        dupes, "concepts_server", lambda ctx: bound.append(ctx) or real(ctx)
    )
    candidates = [{"id": 9000005, "summary": "[meta] Audio", "keywords": []}]
    await _run(tmp_path, new_state, duplicate_candidates=candidates)
    options = fake_client.options
    (ctx,) = bound
    assert ctx.bug_id == BUG and ctx.url == ""
    assert (ctx.products, ctx.components) == dupes.search_scope(new_state.bug)
    assert ctx.meta_ids == {9000005}
    assert options.mcp_servers[dupes.SERVER_NAME]["type"] == "sdk"
    assert dupes.SEARCH_CONCEPTS_TOOL in options.allowed_tools
    for name, sub in options.agents.items():
        assert (dupes.SEARCH_CONCEPTS_TOOL in sub.tools) == (
            name == "duplicate_hunter"
        ), name


async def test_a_meta_bug_the_concept_search_finds_is_refused_as_the_duplicate(
    fake_client, tmp_path, new_state, monkeypatch
):
    bound = []
    real = dupes.concepts_server
    monkeypatch.setattr(
        dupes, "concepts_server", lambda ctx: bound.append(ctx) or real(ctx)
    )
    recorder = ActionsRecorder()
    await _run(tmp_path, new_state, recorder)
    bound[0].meta_ids.add(9000006)
    recorder.record(
        "bugzilla.add_comment",
        {"bug_id": BUG, "text": "Short note.", "is_private": False},
        reasoning="r",
    )
    with pytest.raises(ToolError) as e:
        recorder.record(
            "bugzilla.update_bug",
            {
                "bug_id": BUG,
                "changes": {
                    "status": "RESOLVED",
                    "resolution": "DUPLICATE",
                    "dupe_of": 9000006,
                },
            },
            reasoning="r",
        )
    assert "meta bug" in str(e.value)


async def test_the_run_gives_its_searchfox_revision_to_the_bug_investigator(
    fake_client, tmp_path, new_state
):
    rev = "0123456789abcdef0123456789abcdef01234567"
    await _run(tmp_path, new_state, searchfox_rev=rev)
    assert f"`{rev}`" in fake_client.options.agents["bug_investigator"].prompt


def test_the_run_inputs_default_to_the_limits_and_the_env_overrides_them(
    monkeypatch,
):
    monkeypatch.setenv("BUG_ID", "1")
    monkeypatch.setenv("BROKER_URL", "http://broker")
    monkeypatch.delenv("MAX_TURNS", raising=False)
    monkeypatch.delenv("MAX_BUDGET_USD", raising=False)
    inputs = AgentInputs()
    assert inputs.max_turns == agent.DEFAULT_MAX_TURNS
    assert inputs.max_budget_usd == agent.DEFAULT_MAX_BUDGET_USD
    monkeypatch.setenv("MAX_TURNS", "20")
    monkeypatch.setenv("MAX_BUDGET_USD", "2.5")
    inputs = AgentInputs()
    assert (inputs.max_turns, inputs.max_budget_usd) == (20, 2.5)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "Done, no plan.",
        "```json\nnot json\n```",
        '```json\n{"decision": "garbled"}\n```',
        '```json\n{"decision": "skipped"}\n```',
    ],
)
async def test_a_run_without_a_readable_decision_fails(
    fake_client, tmp_path, new_state, text
):
    # A failed run is retried; an ok run with no decision would use up the bug's
    # dedupe key and leave it untriaged.
    fake_client.result = _result(text)
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state)
    assert "decision" in str(e.value)


async def test_a_run_with_a_decision_returns_it(fake_client, tmp_path, new_state):
    result = await _run(tmp_path, new_state)
    assert result.decision == "analyzed"
    assert result.num_turns == 3 and result.total_cost_usd == 0.5


async def test_an_analyzed_run_that_asked_for_data_fails(
    fake_client, tmp_path, new_state
):
    # A failed run is retried, so a mislabelled run never reaches the bug.
    fake_client.result = _result(
        '```json\n{"decision": "analyzed", "asked": ["profile"], '
        '"issue_type": "drm"}\n```'
    )
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state)
    assert "analyzed" in str(e.value) and "profile" in str(e.value)


async def test_a_needs_info_run_that_asked_for_data_returns_it(
    fake_client, tmp_path, new_state
):
    fake_client.result = _result(
        '```json\n{"decision": "needs_info", "asked": ["profile"], '
        '"severity": "S3", "priority": "P3", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state)
    assert (result.decision, result.asked) == ("needs_info", ["profile"])
    assert (result.severity, result.priority) == ("S3", "P3")


async def test_a_run_returns_its_issue_type(fake_client, tmp_path, new_state):
    fake_client.result = _result(
        '```json\n{"decision": "needs_info", "issue_type": "other", '
        '"issue_type_note": "Seek lag"}\n```'
    )
    result = await _run(tmp_path, new_state)
    assert (result.issue_type, result.issue_type_note) == ("other", "Seek lag")


@pytest.mark.parametrize(
    ("plan", "warning"),
    [
        ('{"decision": "analyzed"}', "no issue_type"),
        ('{"decision": "analyzed", "issue_type": "crash"}', "is not one of"),
        (
            '{"decision": "analyzed", "issue_type": "other", '
            '"issue_type_note": "ask a@example.com"}',
            "email",
        ),
    ],
    ids=["missing", "unknown", "email in the note"],
)
async def test_a_bad_issue_type_is_dropped_and_the_run_stands(
    fake_client, tmp_path, new_state, plan, warning, capsys
):
    recorder = ActionsRecorder()

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Short note.", "is_private": False},
            reasoning="r",
        )

    fake_client.script = script
    fake_client.result = _result(f"```json\n{plan}\n```")
    result = await _run(tmp_path, new_state, recorder)
    assert result.decision == "analyzed" and len(recorder.actions) == 1
    assert (result.issue_type, result.issue_type_note) == (None, None)
    assert warning in result.issue_type_warning
    assert result.auto_apply is True
    assert "dropped the issue_type" in capsys.readouterr().err


async def test_a_good_issue_type_has_no_warning(fake_client, tmp_path, new_state):
    result = await _run(tmp_path, new_state)
    assert result.issue_type == "playback-failure"
    assert result.issue_type_warning is None


async def test_an_error_result_fails_the_run(fake_client, tmp_path, new_state):
    fake_client.result = _result(None, is_error=True, subtype="error_max_turns")
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state)
    assert "error_max_turns" in str(e.value)


async def test_a_run_installs_the_hooks(fake_client, tmp_path, new_state):
    recorder = ActionsRecorder()
    refused = []

    def script():
        try:
            recorder.record(
                "bugzilla.add_comment",
                {"bug_id": BUG + 1, "text": "x", "is_private": False},
                reasoning="r",
            )
        except ToolError as e:
            refused.append(e)

    fake_client.script = script
    await _run(tmp_path, new_state, recorder)
    assert refused and recorder.actions == []


def _duplicate_script(recorder, dupe_of, stores, meta=True):
    from hackbot_agents.gecko_triage.config import TEAM_META_BUGS

    def script():
        _record_verdict(stores, "DUPLICATE", dupe_of)
        recorder.record(
            "bugzilla.add_comment",
            {
                "bug_id": BUG,
                "text": f"This is the same as bug {dupe_of}.",
                "is_private": False,
            },
            reasoning="r",
        )
        recorder.record(
            "bugzilla.update_bug",
            {
                "bug_id": BUG,
                "changes": {
                    "status": "RESOLVED",
                    "resolution": "DUPLICATE",
                    "dupe_of": dupe_of,
                },
            },
            reasoning="r",
        )
        if meta:
            recorder.record(
                "bugzilla.update_bug",
                {
                    "bug_id": dupe_of,
                    "changes": {"blocks": {"add": [min(TEAM_META_BUGS)]}},
                },
                reasoning="r",
            )

    return script


def _dupe_target(dupe, resolution, status="RESOLVED"):
    from hackbot_agents.gecko_triage.config import TRIAGE_SCOPE

    scope = TRIAGE_SCOPE[0]
    return {
        "id": dupe,
        "groups": [],
        "product": scope.product,
        "component": scope.component,
        "status": status,
        "resolution": resolution,
        "keywords": [],
        "summary": "s",
    }


async def test_the_meta_write_on_a_closed_dupe_target_is_dropped(
    fake_client, tmp_path, new_state, monkeypatch, verdict_stores
):
    dupe = 9000002
    calls = _fake_fetch(monkeypatch, {dupe: _dupe_target(dupe, "FIXED")})
    recorder = ActionsRecorder()
    fake_client.script = _duplicate_script(recorder, dupe, verdict_stores)
    fake_client.result = _result(
        '```json\n{"decision": "duplicate", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert calls == [[dupe]]
    assert [a["params"]["bug_id"] for a in recorder.actions] == [BUG, BUG]
    assert result.auto_apply is False
    assert f"bug {dupe}" in result.result and "closed" in result.result


@pytest.mark.parametrize(
    ("status", "resolution", "warning"),
    [
        ("RESOLVED", "FIXED", None),
        ("NEW", "", None),
        (
            "RESOLVED",
            "INVALID",
            "the duplicate target bug 9000002 is RESOLVED INVALID, not FIXED; a person "
            "must check that the current tree fixes every path this bug reports",
        ),
    ],
)
async def test_a_duplicate_of_a_target_closed_with_no_fix_waits_for_a_person(
    fake_client,
    tmp_path,
    new_state,
    monkeypatch,
    verdict_stores,
    status,
    resolution,
    warning,
):
    dupe = 9000002
    _fake_fetch(monkeypatch, {dupe: _dupe_target(dupe, resolution, status)})
    recorder = ActionsRecorder()
    fake_client.script = _duplicate_script(recorder, dupe, verdict_stores, meta=False)
    fake_client.result = _result(
        f'```json\n{{"decision": "duplicate", "duplicate_of": {dupe}, '
        '"issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert len(recorder.actions) == 2
    assert result.close_verification == "confirmed"
    assert result.auto_apply is (warning is None)
    assert result.close_warning == warning


async def test_a_duplicate_of_an_unreadable_bug_fails_the_run(
    fake_client, tmp_path, new_state, monkeypatch, verdict_stores
):
    _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _duplicate_script(recorder, 9000002, verdict_stores)
    fake_client.result = _result(
        '```json\n{"decision": "duplicate", "issue_type": "drm"}\n```'
    )
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state, recorder)
    assert "9000002" in str(e.value)


async def test_a_regressor_that_cannot_be_read_drops_only_the_regressed_by_write(
    fake_client, tmp_path, new_state, monkeypatch
):
    regressor = 9000003
    calls = _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {
                "bug_id": BUG,
                "text": "A recent change caused this.",
                "is_private": False,
            },
            reasoning="r",
        )
        recorder.record(
            "bugzilla.update_bug",
            {
                "bug_id": BUG,
                "changes": {"severity": "S3", "regressed_by": {"add": [regressor]}},
            },
            reasoning="r",
        )

    fake_client.script = script
    result = await _run(tmp_path, new_state, recorder)
    assert calls == [[regressor]]
    assert recorder.actions[1]["params"]["changes"] == {"severity": "S3"}
    assert result.auto_apply is False
    assert f"bug {regressor}" in result.restricted_warning
    assert "regressed_by" in result.result and f"bug {regressor}" in result.result


@pytest.mark.parametrize(
    "found", [{}, {9000003: {"id": 9000003, "groups": ["media-core-security"]}}]
)
async def test_a_comment_that_names_a_restricted_bug_fails_the_run(
    fake_client, tmp_path, new_state, monkeypatch, found
):
    calls = _fake_fetch(monkeypatch, found)
    recorder = ActionsRecorder()

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {
                "bug_id": BUG,
                "text": "Bug 9000003 caused this, see bug 9000004.",
                "is_private": False,
            },
            reasoning="r",
        )

    fake_client.script = script
    found[9000004] = {"id": 9000004, "groups": []}
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state, recorder)
    assert calls == [[9000003, 9000004]]
    assert "bug 9000003" in str(e.value) and "9000004" not in str(e.value)


async def test_a_comment_that_names_only_public_bugs_passes(
    fake_client, tmp_path, new_state, monkeypatch
):
    _fake_fetch(monkeypatch, {9000004: {"id": 9000004, "groups": []}})
    recorder = ActionsRecorder()

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "This is like bug 9000004.", "is_private": False},
            reasoning="r",
        )

    fake_client.script = script
    result = await _run(tmp_path, new_state, recorder)
    assert result.decision == "analyzed" and len(recorder.actions) == 1


async def test_a_run_on_the_triaged_bug_alone_reads_no_other_bug(
    fake_client, tmp_path, new_state, monkeypatch
):
    calls = _fake_fetch(monkeypatch, {})
    result = await _run(tmp_path, new_state)
    assert calls in ([], [[]]) and result.decision == "analyzed"


class FakeLibc:
    def __init__(self, result=0):
        self.calls, self.result = [], result

    def prctl(self, *args):
        self.calls.append(args)
        return self.result


def test_on_linux_the_agent_process_is_made_undumpable(monkeypatch):
    # Other processes of the same user, the CLI's Bash included, then cannot read
    # /proc/<pid>/environ of the agent and the results policy in it.
    libc = FakeLibc()
    monkeypatch.setattr(agent, "_libc", lambda: libc)
    monkeypatch.setattr(agent.sys, "platform", "linux")
    agent.make_undumpable()
    assert libc.calls == [(agent.PR_SET_DUMPABLE, 0, 0, 0, 0)]


def test_elsewhere_making_the_process_undumpable_does_nothing(monkeypatch):
    libc = FakeLibc()
    monkeypatch.setattr(agent, "_libc", lambda: libc)
    monkeypatch.setattr(agent.sys, "platform", "darwin")
    agent.make_undumpable()
    assert libc.calls == []


def test_a_failed_prctl_fails_the_run(monkeypatch):
    monkeypatch.setattr(agent, "_libc", lambda: FakeLibc(result=-1))
    monkeypatch.setattr(agent.sys, "platform", "linux")
    with pytest.raises(AgentError):
        agent.make_undumpable()


async def test_the_process_is_undumpable_before_the_cli_starts(
    fake_client, tmp_path, new_state, monkeypatch
):
    order = []
    monkeypatch.setattr(agent, "make_undumpable", lambda: order.append("undumpable"))
    monkeypatch.setattr(FakeClient, "script", lambda: order.append("cli"))
    await _run(tmp_path, new_state)
    assert order == ["undumpable", "cli"]


@pytest.mark.parametrize(
    "name",
    [
        "STRIPE_APIKEY",
        "APIKEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_CREDENTIAL",
        "SENTRY_DSN",
        "DB_PASSWD",
        "my_api_key",
        "Some_Secret",
        "results_policy_extra",
        "UPLOAD_POLICY",
    ],
)
def test_every_kind_of_secret_name_is_hidden_from_the_cli(name):
    assert agent.hidden_env({name: "s"})[name] == ""


@pytest.mark.parametrize(
    "name", ["KEYWORDS", "TOKENIZERS_PARALLELISM", "ANTHROPIC_API_KEY", "HOME"]
)
def test_other_names_reach_the_cli(name):
    assert name not in agent.hidden_env({name: "s"})


def test_every_subagent_has_a_turn_limit():
    for make in (
        agent.make_investigator,
        agent.make_profile_analyst,
        agent.make_bug_investigator,
        agent.make_log_analyst,
        agent.make_spec_checker,
    ):
        assert make().maxTurns, make
    assert agent.make_duplicate_hunter(()).maxTurns


def test_the_duplicate_hunter_never_names_an_inaccessible_bug():
    prompt = " ".join(agent.make_duplicate_hunter(()).prompt.split())
    assert (
        "Never propose or name a bug that `get_bugs` lists as inaccessible." in prompt
    )


def _init(**statuses):
    servers = [{"name": n, "status": st} for n, st in statuses.items()]
    return SystemMessage(subtype="init", data={"mcp_servers": servers})


def test_failed_servers_names_the_required_servers_that_did_not_connect():
    msg = _init(bugzilla="failed", searchfox="failed", actions="connected")
    assert agent.failed_servers(msg) == ["bugzilla"]
    assert agent.failed_servers(_init(bugzilla="connected")) == []
    assert agent.failed_servers(_init(bugzilla="pending")) == []
    assert agent.failed_servers(_init(bugzilla="needs-auth")) == ["bugzilla"]
    assert agent.failed_servers(_init(actions="disabled")) == ["actions"]
    assert agent.failed_servers(SystemMessage(subtype="other", data={})) == []


async def test_a_run_whose_bugzilla_server_failed_to_connect_fails(
    fake_client, tmp_path, new_state
):
    # The model cannot read the bug, so an ok run would use up its dedupe key.
    fake_client.before = (_init(bugzilla="failed", actions="connected"),)
    fake_client.result = _result(
        '```json\n{"decision": "watching", "issue_type": "drm"}\n```'
    )
    with pytest.raises(AgentError) as e:
        await _run(tmp_path, new_state)
    assert "bugzilla" in str(e.value)


async def test_a_duplicate_of_a_meta_candidate_is_refused_in_the_run(
    fake_client, tmp_path, new_state
):
    meta = {"id": 9000002, "summary": "[meta] Playback", "keywords": [], "words": []}
    recorder = ActionsRecorder()
    refusals = []

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Short note.", "is_private": False},
            reasoning="r",
        )
        try:
            recorder.record(
                "bugzilla.update_bug",
                {
                    "bug_id": BUG,
                    "changes": {
                        "status": "RESOLVED",
                        "resolution": "DUPLICATE",
                        "dupe_of": meta["id"],
                    },
                },
                reasoning="r",
            )
        except ToolError as e:
            refusals.append(str(e))

    fake_client.script = script
    await _run(tmp_path, new_state, recorder, duplicate_candidates=[meta])
    assert refusals and "meta bug" in refusals[0]
    assert len(recorder.actions) == 1


def test_the_close_verifier_only_reads():
    verifier = agent.make_close_verifier()
    assert verifier.maxTurns
    assert closing.RECORD_CLOSE_VERDICT_TOOL in verifier.tools
    for tool in (
        "Read",
        "Grep",
        "Glob",
        "Write",
        "Edit",
        "Bash",
        "WebFetch",
        "Agent",
        "Skill",
        "mcp__bugzilla__get_bug_attachments",
        attachments.FETCH_ATTACHMENT_TOOL,
        dupes.SEARCH_CONCEPTS_TOOL,
    ):
        assert tool not in verifier.tools, tool
    assert not [t for t in verifier.tools if t.startswith("mcp__actions__")]
    for tool in (
        "mcp__bugzilla__get_bugs",
        "mcp__bugzilla__get_bug_comments",
        *agent.SEARCHFOX_TOOLS,
        *agent.MOZILLA_VCS_TOOLS,
    ):
        assert tool in verifier.tools, tool


def test_the_close_verifier_prompt_tries_to_prove_the_close_wrong():
    prompt = " ".join(agent.make_close_verifier().prompt.split())
    assert "Try to prove it WRONG." in prompt
    assert "every comment" in prompt
    assert "`AssertedCast`, is not a safeguard in a release build" in prompt
    assert "Code that the comment quotes must exist in the tree" in prompt
    assert "the impact claims too" in prompt
    assert "fixing it must fix this bug" in prompt and "meta or tracking bug" in prompt
    assert "quiet for 14 days" in prompt and "not a developer" in prompt
    assert "`record_close_verdict` once" in prompt
    assert "A confirm can carry corrections too." in prompt


def test_the_close_verifier_refutes_what_only_a_run_can_rule_out():
    prompt = " ".join(agent.make_close_verifier().prompt.split())
    assert "Never build or run Firefox" in prompt
    assert "never run or open an attachment" in prompt
    assert 'refute it with the reason "needs a runtime check"' in prompt


def test_only_the_close_verifier_is_given_the_verdict_tool_and_the_guard_holds():
    hooks = agent.session_options(CloseVerdicts(), CommitFacts())["hooks"]["PreToolUse"]
    (guard,) = [m for m in hooks if m.matcher == closing.RECORD_CLOSE_VERDICT_TOOL]
    assert guard.hooks == [closing.verdict_caller_guard]


async def test_the_session_allows_one_close_verifier_spawn():
    store = CloseVerdicts()
    hooks = agent.session_options(store, CommitFacts())["hooks"]["PreToolUse"]
    (spawn,) = [m for m in hooks if m.matcher == closing.SPAWN_TOOLS]
    (guard,) = spawn.hooks
    spawn_input = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "close_verifier"},
    }
    assert await guard(spawn_input, None, None) == {}
    denied = await guard(spawn_input, None, None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    other = {"tool_name": "Agent", "tool_input": {"subagent_type": "log_analyst"}}
    assert await guard(other, None, None) == {}
    assert store.verifier_spawns == 1


async def test_the_run_registers_the_close_verifier_and_binds_its_store(
    fake_client, tmp_path, new_state, verdict_stores
):
    await _run(tmp_path, new_state)
    options = fake_client.options
    (store,) = verdict_stores
    assert store.verdicts == []
    assert options.mcp_servers[closing.SERVER_NAME]["type"] == "sdk"
    assert closing.RECORD_CLOSE_VERDICT_TOOL in options.allowed_tools
    for name, sub in options.agents.items():
        assert (closing.RECORD_CLOSE_VERDICT_TOOL in sub.tools) == (
            name == closing.VERIFIER
        ), name


async def test_the_run_offers_remove_recorded_actions_to_the_main_agent_only(
    fake_client, tmp_path, new_state, monkeypatch
):
    bound = []
    real = rerecord.removals_server
    monkeypatch.setattr(
        rerecord, "removals_server", lambda r: bound.append(r) or real(r)
    )
    recorder = ActionsRecorder()
    await _run(tmp_path, new_state, recorder)
    options = fake_client.options
    (removals,) = bound
    assert removals.recorder is recorder and removals.used == 0
    assert options.mcp_servers[rerecord.SERVER_NAME]["type"] == "sdk"
    assert rerecord.REMOVE_RECORDED_ACTIONS_TOOL in options.allowed_tools
    for name, sub in options.agents.items():
        assert rerecord.REMOVE_RECORDED_ACTIONS_TOOL not in sub.tools, name
    hooks = options.hooks["PreToolUse"]
    (guard,) = [m for m in hooks if m.matcher == rerecord.REMOVE_RECORDED_ACTIONS_TOOL]
    assert guard.hooks == [rerecord.removal_caller_guard]


def _close_script(recorder, stores, verdict):
    def script():
        _record_verdict(stores, "INVALID", verdict=verdict)
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Short note.", "is_private": False},
            reasoning="r",
        )
        with contextlib.suppress(ToolError):
            recorder.record(
                "bugzilla.update_bug",
                {
                    "bug_id": BUG,
                    "changes": {"status": "RESOLVED", "resolution": "INVALID"},
                },
                reasoning="r",
            )

    return script


@pytest.mark.parametrize(
    ("verdict", "outcome", "recorded", "auto_apply"),
    [("confirm", "confirmed", 2, True), ("refute", "refuted", 1, False)],
)
async def test_the_findings_say_what_the_close_verifier_said(
    fake_client,
    tmp_path,
    new_state,
    monkeypatch,
    verdict_stores,
    verdict,
    outcome,
    recorded,
    auto_apply,
):
    _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _close_script(recorder, verdict_stores, verdict)
    fake_client.result = _result(
        '```json\n{"decision": "invalid", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert result.close_verification == outcome
    assert len(recorder.actions) == recorded
    assert result.auto_apply is auto_apply
    assert (result.close_warning is None) == (verdict == "confirm")


async def test_a_run_that_records_its_close_again_keeps_the_confirm(
    fake_client, tmp_path, new_state, monkeypatch, verdict_stores
):
    _fake_fetch(monkeypatch, {})
    bound = []
    real = rerecord.removals_server
    monkeypatch.setattr(
        rerecord, "removals_server", lambda r: bound.append(r) or real(r)
    )
    recorder = ActionsRecorder()

    def record_both(text):
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": text, "is_private": False},
            reasoning="r",
        )
        recorder.record(
            "bugzilla.update_bug",
            {"bug_id": BUG, "changes": {"status": "RESOLVED", "resolution": "INVALID"}},
            reasoning="r",
        )

    async def script():
        _record_verdict(verdict_stores, "INVALID")
        record_both("The the note.")
        await rerecord.remove_recorded_actions_tool(bound[-1])
        record_both("The note.")

    fake_client.script = script
    fake_client.result = _result(
        '```json\n{"decision": "invalid", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert [a["params"].get("text") for a in recorder.actions] == ["The note.", None]
    assert result.close_verification == "confirmed"
    assert result.close_warning is None
    assert result.auto_apply is True


@pytest.mark.parametrize(
    ("decision", "fails"), [("needs_info", True), ("watching", False)]
)
async def test_a_run_that_removed_its_comment_and_did_not_record_it_again_fails(
    fake_client, tmp_path, new_state, monkeypatch, decision, fails
):
    _fake_fetch(monkeypatch, {})
    bound = []
    real = rerecord.removals_server
    monkeypatch.setattr(
        rerecord, "removals_server", lambda r: bound.append(r) or real(r)
    )
    recorder = ActionsRecorder()

    async def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "The the note.", "is_private": False},
            reasoning="r",
        )
        await rerecord.remove_recorded_actions_tool(bound[-1])

    fake_client.script = script
    fake_client.result = _result(
        f'```json\n{{"decision": "{decision}", "issue_type": "drm"}}\n```'
    )
    if fails:
        with pytest.raises(AgentError, match="did not record a comment again"):
            await _run(tmp_path, new_state, recorder)
    else:
        assert (await _run(tmp_path, new_state, recorder)).decision == "watching"


async def test_a_run_that_does_not_close_has_no_close_verification(
    fake_client, tmp_path, new_state
):
    result = await _run(tmp_path, new_state)
    assert result.close_verification is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("confirmed", "confirmed"), (" Refuted ", "refuted"), ("yes", None), (1, None)],
)
def test_the_close_verification_field_fails_closed(value, expected):
    result = agent.GeckoTriageResult(bug_id=BUG, num_turns=0, close_verification=value)
    assert result.close_verification == expected


def _closing_comment_first(recorder, stores, spawn):
    def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Closing as invalid.", "is_private": False},
            reasoning="r",
        )
        if spawn:
            spawn_input = {"tool_input": {"subagent_type": closing.VERIFIER}}
            assert closing.spawn_refusal(stores[-1], spawn_input) is not None
        with contextlib.suppress(ToolError):
            recorder.record(
                "bugzilla.update_bug",
                {
                    "bug_id": BUG,
                    "changes": {"status": "RESOLVED", "resolution": "INVALID"},
                },
                reasoning="r",
            )

    return script


@pytest.mark.parametrize("spawn", [True, False])
async def test_a_closing_comment_before_the_verifier_never_applies_unattended(
    fake_client, tmp_path, new_state, monkeypatch, verdict_stores, spawn
):
    _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _closing_comment_first(recorder, verdict_stores, spawn)
    fake_client.result = _result(
        '```json\n{"decision": "analyzed", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert [a["type"] for a in recorder.actions] == ["bugzilla.add_comment"]
    assert result.auto_apply is False
    assert "refused 1 close(s)" in result.close_warning


def test_the_close_verifier_refutes_a_bug_too_large_to_verify():
    prompt = " ".join(agent.make_close_verifier().prompt.split())
    assert "If a tool says that its result was saved to a file" in prompt
    assert (
        'refute with the reason "a tool result was too large to read, so the close '
        'cannot be verified"' in prompt
    )
    hooks = agent.session_options(CloseVerdicts(), CommitFacts())["hooks"]
    spill = hooks["PostToolUse"][0]
    assert spill.matcher == "mcp__.*" and len(spill.hooks) == 1
    assert not [m for m in hooks["PreToolUse"] if m.matcher == "Read"]


async def test_a_close_recorded_on_a_retry_applies_unattended(
    fake_client, tmp_path, new_state, monkeypatch, verdict_stores
):
    _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()

    def script():
        _record_verdict(verdict_stores, "INVALID")
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Short note.", "is_private": False},
            reasoning="r",
        )
        with pytest.raises(ToolError):
            recorder.record(
                "bugzilla.update_bug",
                {"bug_id": BUG, "changes": {"status": "RESOLVED"}},
                reasoning="r",
            )
        recorder.record(
            "bugzilla.update_bug",
            {"bug_id": BUG, "changes": {"status": "RESOLVED", "resolution": "INVALID"}},
            reasoning="r",
        )

    fake_client.script = script
    fake_client.result = _result(
        '```json\n{"decision": "invalid", "issue_type": "drm"}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert verdict_stores[-1].refused == [(None, None)]
    assert result.close_verification == "confirmed"
    assert result.close_warning is None
    assert result.auto_apply is True


async def test_a_vcs_result_lets_the_run_needinfo_its_commit_author():
    from hackbot_agents.gecko_triage import authors
    from hackbot_agents.gecko_triage.config import MOZILLA_VCS_TOOLS

    found = CommitFacts()
    post = agent.session_options(CloseVerdicts(), found)["hooks"]["PostToolUse"]
    (vcs,) = [m for m in post if m.matcher == authors.COMMIT_TOOLS_MATCHER]
    text = '{"author": "A <a@example.org>", "desc": "Bug 1234567 - Fix"}'
    response = {"content": [{"type": "text", "text": text}]}
    await vcs.hooks[0](
        {"tool_name": MOZILLA_VCS_TOOLS[0], "tool_response": response}, "toolu_1", None
    )
    assert found == CommitFacts({"a@example.org"}, {1234567})


@pytest.fixture
def run_authors(monkeypatch):
    """The commit facts of each run, as the run binds them to its vcs hook."""
    from hackbot_agents.gecko_triage import authors

    facts = []
    real = authors.commit_hook
    monkeypatch.setattr(
        authors, "commit_hook", lambda found: facts.append(found) or real(found)
    )
    return facts


def _author_needinfo_script(recorder, sets, text="A recent change caused this."):
    def script():
        sets[-1].authors.add("author@example.org")
        sets[-1].bugs.add(1234567)
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": text, "is_private": False},
            reasoning="r",
        )
        recorder.record(
            "bugzilla.update_bug",
            {
                "bug_id": BUG,
                "changes": {
                    "keywords": {"add": ["regression"]},
                    "flags": [
                        {
                            "name": "needinfo",
                            "status": "?",
                            "requestee": "author@example.org",
                        }
                    ],
                },
            },
            reasoning="r",
        )

    return script


async def test_a_commit_author_needinfo_never_applies_unattended(
    fake_client, tmp_path, new_state, monkeypatch, run_authors
):
    _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _author_needinfo_script(recorder, run_authors)
    result = await _run(tmp_path, new_state, recorder)
    assert len(recorder.actions) == 2
    assert result.auto_apply is False
    assert result.needinfo_warning == (
        "a commit author needinfo needs a person to check the Bugzilla login"
    )


async def test_a_run_with_no_commit_author_needinfo_has_no_needinfo_warning(
    fake_client, tmp_path, new_state, monkeypatch
):
    _fake_fetch(monkeypatch, {})
    result = await _run(tmp_path, new_state)
    assert result.needinfo_warning is None


def _comment_and_update(recorder, text, changes, before=lambda: None):
    def script():
        before()
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": text, "is_private": False},
            reasoning="r",
        )
        recorder.record(
            "bugzilla.update_bug", {"bug_id": BUG, "changes": changes}, reasoning="r"
        )

    return script


async def test_a_restricted_commit_message_bug_fails_the_run_in_any_spelling(
    fake_client, tmp_path, new_state, monkeypatch, run_authors
):
    calls = _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _comment_and_update(
        recorder,
        "The regressor is 1,234,567.",
        {"severity": "S3"},
        before=lambda: run_authors[-1].bugs.add(1234567),
    )
    with pytest.raises(AgentError, match="bug 1234567"):
        await _run(tmp_path, new_state, recorder)
    assert calls == [[1234567]]


PERSON_CHECK = (
    "a person must check that the comment does not name or describe these bugs"
)


async def test_a_restricted_commit_bug_holds_the_run_for_a_person(
    fake_client, tmp_path, new_state, monkeypatch, run_authors
):
    calls = _fake_fetch(monkeypatch, {})
    recorder = ActionsRecorder()
    fake_client.script = _comment_and_update(
        recorder,
        "A recent change caused this.",
        {"severity": "S3"},
        before=lambda: run_authors[-1].bugs.add(1234567),
    )
    result = await _run(tmp_path, new_state, recorder)
    assert calls == [[1234567]]
    assert result.auto_apply is False
    assert "bug 1234567" in result.restricted_warning
    assert PERSON_CHECK in result.restricted_warning
    assert "1234567" not in recorder.actions[0]["params"]["text"]


async def test_a_readable_commit_bug_needs_no_person(
    fake_client, tmp_path, new_state, monkeypatch, run_authors
):
    _fake_fetch(monkeypatch, {1234567: {"id": 1234567, "groups": []}})
    recorder = ActionsRecorder()
    fake_client.script = _comment_and_update(
        recorder,
        "A recent change caused this.",
        {"severity": "S3"},
        before=lambda: run_authors[-1].bugs.add(1234567),
    )
    result = await _run(tmp_path, new_state, recorder)
    assert result.restricted_warning is None
    assert result.auto_apply is True


async def test_a_failed_read_keeps_the_regressor_and_holds_the_run(
    fake_client, tmp_path, new_state, monkeypatch
):
    async def fail(server, ids):
        return None

    monkeypatch.setattr(agent, "fetch_bugs", fail)
    recorder = ActionsRecorder()
    fake_client.script = _comment_and_update(
        recorder,
        "A recent change caused this.",
        {"severity": "S3", "regressed_by": {"add": [9000003]}},
    )
    result = await _run(tmp_path, new_state, recorder)
    assert recorder.actions[1]["params"]["changes"]["regressed_by"] == {
        "add": [9000003]
    }
    assert result.auto_apply is False
    assert "bug 9000003" in result.restricted_warning


# --- whiteboard fix tags ---------------------------------------------------- #


def _fix_tag_script(recorder, tags):
    changes = {"severity": "S3", "whiteboard": {"add": tags}}
    return _comment_and_update(recorder, "Short note.", changes)


async def test_an_analyzed_run_keeps_its_fix_tags_and_reports_them(
    fake_client, tmp_path, bug_new, comments_new
):
    tags = [agent.TERMS.whiteboard.easy_fix, agent.TERMS.whiteboard.gfb_candidate]
    state = agent.TriageState(
        bug=dict(bug_new, whiteboard="[av1]"), comments=comments_new, now=NOW
    )
    recorder = ActionsRecorder()
    fake_client.script = _fix_tag_script(recorder, tags)
    result = await _run(tmp_path, state, recorder)
    changes = recorder.actions[-1]["params"]["changes"]
    assert changes["whiteboard"] == f"[av1] {tags[0]} {tags[1]}"
    assert result.fix_tags == tags
    assert result.auto_apply is True


async def test_a_run_with_no_fix_tag_reports_none(fake_client, tmp_path, new_state):
    result = await _run(tmp_path, new_state)
    assert result.fix_tags == []


@pytest.mark.parametrize("decision", ["needs_info", "stalled", "watching"])
async def test_a_fix_tag_on_a_run_that_is_not_analyzed_is_dropped(
    fake_client, tmp_path, new_state, decision
):
    recorder = ActionsRecorder()
    fake_client.script = _fix_tag_script(recorder, [agent.TERMS.whiteboard.easy_fix])
    fake_client.result = _result(
        f'```json\n{{"decision": "{decision}", "issue_type": "drm"}}\n```'
    )
    result = await _run(tmp_path, new_state, recorder)
    assert recorder.actions[-1]["params"]["changes"] == {"severity": "S3"}
    assert result.fix_tags == []
    assert result.auto_apply is True
    assert "whiteboard change" in result.result and decision in result.result


def test_fix_tag_drops_only_drop_on_a_run_that_is_not_analyzed():
    actions = [
        {
            "action_id": "a1",
            "type": "bugzilla.update_bug",
            "params": {"bug_id": BUG, "changes": {"whiteboard": "[x]"}},
        }
    ]
    assert agent.fix_tag_drops(agent.Plan(decision="analyzed"), actions, BUG) == []
    (drop,) = agent.fix_tag_drops(agent.Plan(decision="needs_info"), actions, BUG)
    assert (drop.action_id, drop.field) == ("a1", "whiteboard")
    assert agent.fix_tag_drops(agent.Plan(decision="needs_info"), [], BUG) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("[gecko-triage:easy-fix]", ["[gecko-triage:easy-fix]"]),
        (
            ["[gecko-triage:gfb-candidate]", "[gecko-triage:easy-fix]", "[x]"],
            ["[gecko-triage:easy-fix]", "[gecko-triage:gfb-candidate]"],
        ),
        (
            ["[gecko-triage:easy-fix]", "[gecko-triage:easy-fix]"],
            ["[gecko-triage:easy-fix]"],
        ),
        (42, []),
    ],
)
def test_the_fix_tags_finding_keeps_only_known_tags(value, expected):
    base = {"bug_id": BUG, "num_turns": 1, "total_cost_usd": 0.0}
    assert agent.GeckoTriageResult(**base, fix_tags=value).fix_tags == expected
    assert agent.GeckoTriageResult(**base).fix_tags == []
