"""Tests for the optional team setup."""

import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs
from hackbot_agents.bug_fix.agent import (
    PROMPTS,
    RULES_DIR,
    build_system_prompt,
    render_prompt,
)
from hackbot_agents.bug_fix.teamconfig import TEAMS_DIR, Team, load_team, team_dir
from pydantic import ValidationError


def _write_team(root, name="demo", rules="Use static_cast.\n"):
    path = root / name
    (path / "rules").mkdir(parents=True)
    (path / "team.toml").write_text(
        'label = "Demo Team"\n\n'
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


def test_team_dir_returns_the_team_directory(tmp_path):
    _write_team(tmp_path)
    assert team_dir(tmp_path, "demo") == tmp_path / "demo"


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


def test_media_team_loads():
    team = load_team(team_dir(TEAMS_DIR, "media") / "team.toml")
    assert team.label == "Firefox Audio/Video"
    assert ("Core", "Audio/Video: Playback") in {
        (c.product, c.component) for c in team.components
    }


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
    assert "- Core :: Demo\n- Core :: Demo: Other" in prompt
    assert "Never use C-style casts. {not a field}" in prompt


def test_system_prompt_asks_for_format_and_lint():
    system = (PROMPTS / "system.md").read_text()
    assert "./mach format" in system
    assert "./mach lint" in system


def test_team_input_defaults_to_none():
    assert AgentInputs(bug_id=1, broker_url="http://broker").team is None


def test_team_input_resolves_the_team_directory():
    inputs = AgentInputs(bug_id=1, broker_url="http://broker", team="media")
    assert inputs.team == TEAMS_DIR / "media"


def test_team_input_rejects_an_unknown_team():
    with pytest.raises(ValidationError, match="no team"):
        AgentInputs(bug_id=1, broker_url="http://broker", team="nope")
