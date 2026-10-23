"""Tests for the optional team setup."""

import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs
from hackbot_agents.bug_fix.agent import (
    RULES_DIR,
    build_system_prompt,
    render_prompt,
)
from hackbot_agents.bug_fix.teamconfig import (
    TEAMS_DIR,
    Team,
    load_team,
    team_dir,
    team_for_bug,
)
from pydantic import ValidationError


def _write_team(root, name="demo", rules="Use static_cast.\n", top=""):
    path = root / name
    (path / "rules").mkdir(parents=True)
    (path / "team.toml").write_text(
        f'label = "Demo Team"\n{top}\n'
        '[[components]]\nproduct = "Core"\ncomponent = "Demo"\n\n'
        '[[components]]\nproduct = "Core"\ncomponent = "Demo: Other"\n'
    )
    (path / "rules" / "implementation.md").write_text(rules)
    return path


@pytest.mark.parametrize("name", ["../media", "Media", "media/rules", "", "1media"])
def test_team_dir_rejects_names_that_are_not_plain(tmp_path, name):
    with pytest.raises(ValueError, match="lower-case name"):
        team_dir(tmp_path, name)


def test_team_dir_rejects_unknown_team(tmp_path):
    with pytest.raises(ValueError, match="no team 'nope'"):
        team_dir(tmp_path, "nope")


def test_team_dir_rejects_team_without_rules(tmp_path):
    path = _write_team(tmp_path)
    (path / "rules" / "implementation.md").unlink()
    with pytest.raises(ValueError, match="implementation.md"):
        team_dir(tmp_path, "demo")


def test_team_needs_a_component():
    with pytest.raises(ValidationError):
        Team.model_validate({"label": "Demo", "components": []})


def test_team_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Team.model_validate(
            {
                "label": "Demo",
                "components": [{"product": "Core", "component": "Demo"}],
                "reviewer": "someone",
            }
        )


def test_no_team_keeps_the_system_prompt_unchanged():
    assert build_system_prompt(RULES_DIR, None) == render_prompt(
        "system.md", rules_dir=str(RULES_DIR.resolve())
    )


def test_team_appends_its_section_to_the_system_prompt(tmp_path):
    path = _write_team(tmp_path, rules="Never use C-style casts. {not a field}\n")
    prompt = build_system_prompt(RULES_DIR, path)

    assert prompt.startswith(
        render_prompt("system.md", rules_dir=str(RULES_DIR.resolve()))
    )
    assert "Demo Team" in prompt
    assert "Never use C-style casts. {not a field}" in prompt


def test_team_input_defaults_to_none():
    assert AgentInputs(bug_id=1, broker_url="http://broker").team is None


def test_team_input_resolves_the_team_directory():
    inputs = AgentInputs(bug_id=1, broker_url="http://broker", team="media")
    assert inputs.team == TEAMS_DIR / "media"


def test_team_input_rejects_an_unknown_team():
    with pytest.raises(ValidationError, match="no team"):
        AgentInputs(bug_id=1, broker_url="http://broker", team="nope")


@pytest.mark.parametrize(
    ("bug", "expected"),
    [
        ({"product": "Core", "component": "Demo: Other"}, "demo"),
        ({"product": "Core", "component": "Layout"}, None),
        ({"product": "Firefox", "component": "Demo"}, None),
        ({}, None),
    ],
)
def test_team_for_bug(tmp_path, bug, expected):
    _write_team(tmp_path, "demo")
    assert team_for_bug(tmp_path, bug) == (tmp_path / expected if expected else None)


def test_media_owns_its_components():
    bug = {"product": "Core", "component": "Audio/Video: Playback"}
    assert team_for_bug(TEAMS_DIR, bug) == TEAMS_DIR / "media"


class _Stop(Exception):
    pass


async def _options(monkeypatch, tmp_path, team_dir):
    from types import SimpleNamespace

    from hackbot_agents.bug_fix import agent

    captured = {}

    def fake_options(**kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(agent, "ClaudeAgentOptions", fake_options)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {})
    monkeypatch.setattr(agent, "base_commit", lambda repo: "base")
    with pytest.raises(_Stop):
        await agent.run_bug_fix(
            bugzilla_mcp_server={"type": "http", "url": "http://b"},
            phabricator_mcp_server={"type": "http", "url": "http://p"},
            source_repo=tmp_path,
            fx_ctx=SimpleNamespace(
                mozconfig=tmp_path / "mozconfig", objdir=tmp_path / "obj"
            ),
            bug=1,
            team_dir=team_dir,
        )
    return captured


async def test_no_team_run_has_no_gates(monkeypatch, tmp_path):
    options = await _options(monkeypatch, tmp_path, None)
    assert options["hooks"] is None
    assert options["env"] == {}
    assert set(options["agents"]) == {"investigator"}
    assert "gates" not in options["mcp_servers"]


async def test_team_run_has_the_reviewer_and_the_gates(monkeypatch, tmp_path):
    from hackbot_agents.bug_fix.gates import RECORD_GATE_TOOL, REVIEWER

    team = _write_team(tmp_path / "teams")
    options = await _options(monkeypatch, tmp_path, team)

    assert REVIEWER in options["agents"]
    assert "gates" in options["mcp_servers"]
    assert RECORD_GATE_TOOL in options["allowed_tools"]
    assert set(options["hooks"]) == {"PreToolUse", "PostToolUse"}
    assert options["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


def test_reviewer_reads_the_team_rules_and_review_docs(tmp_path):
    from hackbot_agents.bug_fix.agent import make_reviewer

    path = _write_team(
        tmp_path,
        rules="Never use C-style casts.\n",
        top='review_docs = ["dom/media/docs/review/*.md"]\n',
    )
    reviewer = make_reviewer(path, "abc123")

    assert "Never use C-style casts." in reviewer.prompt
    assert "dom/media/docs/review/*.md" in reviewer.prompt
    assert "BLOCKING: <n>" in reviewer.prompt
    assert "review-context.toml" in reviewer.prompt
    assert "git diff abc123" in reviewer.prompt
    assert "Edit" not in (reviewer.tools or [])


@pytest.mark.parametrize(
    ("value", "name"),
    [
        ("linux", "Linux"),
        ("darwin", "macOS"),
        ("win32", "Windows"),
        ("freebsd14", "freebsd14"),
    ],
)
def test_runner_platform_names(value, name):
    from hackbot_agents.bug_fix.agent import runner_platform

    assert runner_platform(value) == name


def test_team_prompt_names_the_runner_platform(tmp_path, monkeypatch):
    from hackbot_agents.bug_fix import agent

    monkeypatch.setattr(agent.sys, "platform", "win32")
    prompt = build_system_prompt(RULES_DIR, _write_team(tmp_path))
    assert "This run is on Windows." in prompt


async def test_team_run_records_the_test_comment_after_its_patch(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from hackbot_agents.bug_fix import agent
    from hackbot_runtime.actions import ActionsRecorder

    recorder = ActionsRecorder(artifacts_dir=tmp_path / "artifacts")

    class _Result:
        is_error = False
        result = "done"
        num_turns = 3
        total_cost_usd = 0.1

    class _Session:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            pass

        async def receive_response(self):
            from hackbot_agents.bug_fix.gates import Gates

            state.sent = Gates(repo=None)
            state.sent_fp = "same"
            recorder.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r")
            yield _Result()

    monkeypatch.setattr(agent, "ClaudeSDKClient", _Session)
    monkeypatch.setattr(agent, "ResultMessage", _Result)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {})
    monkeypatch.setattr(agent, "base_commit", lambda repo: "base")
    real_session = agent.team_session
    state = None

    def team_session(*args, **kwargs):
        nonlocal state
        session = real_session(*args, **kwargs)
        state = session["gates"]
        return session

    monkeypatch.setattr(agent, "team_session", team_session)
    from hackbot_agents.bug_fix import gates as gates_module

    monkeypatch.setattr(gates_module, "fingerprint", lambda repo: "same")

    await agent.run_bug_fix(
        bugzilla_mcp_server={"type": "http", "url": "http://b"},
        phabricator_mcp_server={"type": "http", "url": "http://p"},
        source_repo=tmp_path,
        fx_ctx=SimpleNamespace(
            mozconfig=tmp_path / "mozconfig", objdir=tmp_path / "obj"
        ),
        bug=1,
        team_dir=_write_team(tmp_path / "teams"),
        actions_recorder=recorder,
    )

    types = [a["type"] for a in recorder.actions]
    assert types == ["phabricator.submit_patch", "phabricator.add_comment"]
    assert (
        recorder.actions[-1]["params"]["revision_id"] == "{{actions.patch.revision_id}}"
    )


def test_team_for_bug_skips_a_broken_team(tmp_path, capsys):
    _write_team(tmp_path, "demo")
    broken = tmp_path / "aaa-broken"
    broken.mkdir()
    (broken / "team.toml").write_text('label = "x"\n')
    assert team_for_bug(tmp_path, {"product": "Core", "component": "Demo"}) == (
        tmp_path / "demo"
    )
    assert "skipped team aaa-broken" in capsys.readouterr().err


def test_team_for_bug_skips_a_team_without_rules(tmp_path):
    (tmp_path / "norules").mkdir()
    (tmp_path / "norules" / "team.toml").write_text(
        'label = "x"\n\n[[components]]\nproduct = "Core"\ncomponent = "Demo"\n'
    )
    assert team_for_bug(tmp_path, {"product": "Core", "component": "Demo"}) is None


async def test_a_change_after_the_guard_fails_the_run(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from hackbot_agents.bug_fix import agent, gates
    from hackbot_runtime import AgentError
    from hackbot_runtime.actions import ActionsRecorder

    recorder = ActionsRecorder(artifacts_dir=tmp_path / "artifacts")

    class _Result:
        is_error = False
        result = "done"
        num_turns = 3
        total_cost_usd = 0.1

    class _Session:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            pass

        async def receive_response(self):
            state.sent = gates.Gates(repo=None)
            state.sent_fp = "sent"
            recorder.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r")
            yield _Result()

    real_session = agent.team_session
    state = None

    def team_session(*args, **kwargs):
        nonlocal state
        session = real_session(*args, **kwargs)
        state = session["gates"]
        return session

    monkeypatch.setattr(agent, "ClaudeSDKClient", _Session)
    monkeypatch.setattr(agent, "ResultMessage", _Result)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {})
    monkeypatch.setattr(agent, "base_commit", lambda repo: "base")
    monkeypatch.setattr(agent, "team_session", team_session)
    monkeypatch.setattr(gates, "fingerprint", lambda repo: "changed")

    with pytest.raises(AgentError, match="changed after the gates"):
        await agent.run_bug_fix(
            bugzilla_mcp_server={"type": "http", "url": "http://b"},
            phabricator_mcp_server={"type": "http", "url": "http://p"},
            source_repo=tmp_path,
            fx_ctx=SimpleNamespace(
                mozconfig=tmp_path / "mozconfig", objdir=tmp_path / "obj"
            ),
            bug=1,
            team_dir=_write_team(tmp_path / "teams"),
            actions_recorder=recorder,
        )


def test_a_team_can_set_its_review_round_limit(tmp_path):
    path = _write_team(tmp_path, top="max_review_rounds = 5\n")
    assert load_team(path).max_review_rounds == 5
    assert "After 5 review rounds" in build_system_prompt(RULES_DIR, path)


def test_the_review_round_limit_defaults_to_three(tmp_path):
    from hackbot_agents.bug_fix.gates import MAX_REVIEW_ROUNDS

    assert load_team(_write_team(tmp_path)).max_review_rounds == MAX_REVIEW_ROUNDS == 3


def test_the_review_round_limit_must_be_positive():
    with pytest.raises(ValidationError):
        Team.model_validate(
            {
                "label": "Demo",
                "components": [{"product": "Core", "component": "Demo"}],
                "max_review_rounds": 0,
            }
        )
