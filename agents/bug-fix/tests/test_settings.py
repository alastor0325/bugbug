"""Tests for the Claude Code settings the agent runs with."""

import json
from types import SimpleNamespace

import pytest
from hackbot_agents.bug_fix import agent


class _Stop(Exception):
    pass


async def test_run_turns_off_commit_and_pr_attribution(monkeypatch, tmp_path):
    captured = {}

    def fake_options(**kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(agent, "ClaudeAgentOptions", fake_options)
    monkeypatch.setattr(agent, "build_sdk_server", lambda *a, **k: {})
    with pytest.raises(_Stop):
        await agent.run_bug_fix(
            bugzilla_mcp_server={"type": "http", "url": "http://b"},
            phabricator_mcp_server={"type": "http", "url": "http://p"},
            source_repo=tmp_path,
            fx_ctx=SimpleNamespace(
                mozconfig=tmp_path / "mozconfig", objdir=tmp_path / "obj"
            ),
            bug=1,
        )
    assert json.loads(captured["settings"]) == {"attribution": {"commit": "", "pr": ""}}
