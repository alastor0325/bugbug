"""Tests for the pre-run bug lookup."""

import asyncio

from hackbot_agents.bug_fix import preflight


async def test_fetch_bug_fails_open(monkeypatch):
    def boom(url):
        raise OSError("broker down")

    monkeypatch.setattr(preflight, "streamablehttp_client", boom)
    assert await preflight.fetch_bug("http://b", 1) == {}


async def test_fetch_bug_times_out_to_no_team(monkeypatch):
    async def hang(url, bug):
        await asyncio.sleep(10)

    monkeypatch.setattr(preflight, "_get_bug", hang)
    monkeypatch.setattr(preflight, "TIMEOUT_SECONDS", 0.01)
    assert await preflight.fetch_bug("http://b", 1) == {}
