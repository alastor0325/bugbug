"""Tests for the run: a skipped bug costs neither a checkout nor a model turn."""

import asyncio
import os
import threading
from pathlib import Path

import pytest
from conftest import NOW
from hackbot_agents.gecko_triage import run
from hackbot_agents.gecko_triage.preflight import BugUnreadable
from hackbot_agents.gecko_triage.state import TriageState

BUG = 9000001


class FakeContext:
    def __init__(self):
        self.prepared = False
        self.repo_path = "/repo"
        self.log_path = "/log"
        self.actions = object()

    async def prepare_repo(self):
        self.prepared = True


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("BUG_ID", str(BUG))
    monkeypatch.setenv("BROKER_URL", "http://127.0.0.1:8765/")
    monkeypatch.setattr(run, "require_toolkit", lambda _dir: None)
    calls = {}

    def use_state(state):
        async def fetch_state(server, bug, **kwargs):
            calls["fetch_state"] = (server, bug, kwargs)
            return state

        monkeypatch.setattr(run, "fetch_state", fetch_state)

    async def resolve_index_revision():
        calls["searchfox"] = True
        return "abc"

    async def find_candidates(server, bug):
        calls["dupes"] = True
        return []

    async def run_gecko_triage(**kwargs):
        calls["run"] = kwargs
        return "ran"

    monkeypatch.setattr(run, "resolve_index_revision", resolve_index_revision)
    monkeypatch.setattr(run, "find_candidates", find_candidates)
    monkeypatch.setattr(run, "run_gecko_triage", run_gecko_triage)
    monkeypatch.setattr(run, "install_profiler_cli", lambda repo: None)
    return use_state, calls


async def test_a_restricted_bug_is_skipped_before_the_checkout_and_the_model(
    wired, bug_new, comments_new
):
    use_state, calls = wired
    use_state(
        TriageState(
            bug=dict(bug_new, groups=["core-security"]), comments=comments_new, now=NOW
        )
    )
    ctx = FakeContext()
    result = await run.main(ctx)
    assert result.decision == "skipped"
    assert result.num_turns == 0 and result.total_cost_usd == 0.0
    assert "restricted" in result.result
    assert result.agent_version
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_a_bug_that_bugzilla_withholds_is_skipped_not_failed(wired):
    use_state, calls = wired
    use_state(TriageState(bug={"id": BUG}, now=NOW, restricted=True))
    ctx = FakeContext()
    result = await run.main(ctx)
    assert result.decision == "skipped"
    assert "restricted" in result.result
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_an_unreadable_bug_fails_before_the_checkout(wired):
    use_state, calls = wired
    use_state(None)
    ctx = FakeContext()
    with pytest.raises(BugUnreadable):
        await run.main(ctx)
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_a_bug_to_triage_reaches_the_model_after_the_checkout(
    wired, new_state, monkeypatch
):
    monkeypatch.setenv("MAX_TURNS", "12")
    monkeypatch.setenv("MAX_BUDGET_USD", "3.5")
    use_state, calls = wired
    use_state(new_state)
    ctx = FakeContext()
    assert await run.main(ctx) == "ran"
    assert ctx.prepared
    server, bug, kwargs = calls["fetch_state"]
    assert kwargs == {}
    assert server == {"type": "http", "url": "http://127.0.0.1:8765/mcp"}
    assert bug == BUG
    assert calls["run"]["bug"] == BUG
    assert calls["run"]["state"] is new_state
    assert calls["run"]["searchfox_rev"] == "abc"
    assert calls["run"]["source_repo"] == "/repo"
    assert calls["run"]["max_turns"] == 12
    assert calls["run"]["max_budget_usd"] == 3.5


async def test_the_checkout_runs_beside_the_duplicate_search(
    wired, new_state, monkeypatch
):
    use_state, calls = wired
    use_state(new_state)
    searching, cloning = threading.Event(), threading.Event()

    class BlockingContext(FakeContext):
        async def prepare_repo(self):
            # A blocking clone, as `ensure_source_repo` does.
            cloning.set()
            self.prepared = searching.wait(5)

    async def find_candidates(server, bug):
        searching.set()
        for _ in range(200):
            if cloning.is_set():
                break
            await asyncio.sleep(0.01)
        calls["cloning_during_search"] = cloning.is_set()
        return []

    monkeypatch.setattr(run, "find_candidates", find_candidates)
    ctx = BlockingContext()
    assert await run.main(ctx) == "ran"
    assert calls["cloning_during_search"] and ctx.prepared


async def test_the_trees_profiler_cli_goes_first_on_path_after_the_checkout(
    wired, new_state, monkeypatch
):
    use_state, _ = wired
    use_state(new_state)
    monkeypatch.setenv("PATH", "/usr/bin")
    ctx = FakeContext()
    installed = []

    def install(repo):
        installed.append((repo, ctx.prepared))
        return Path("/state/bin")

    monkeypatch.setattr(run, "install_profiler_cli", install)
    assert await run.main(ctx) == "ran"
    assert installed == [(Path("/repo"), True)]
    assert os.environ["PATH"] == "/state/bin:/usr/bin"


async def test_a_failed_profiler_cli_install_leaves_path_alone(
    wired, new_state, monkeypatch
):
    use_state, _ = wired
    use_state(new_state)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert await run.main(FakeContext()) == "ran"
    assert os.environ["PATH"] == "/usr/bin"


async def test_with_no_path_the_trees_profiler_cli_goes_before_the_default_path(
    wired, new_state, monkeypatch
):
    use_state, _ = wired
    use_state(new_state)
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(run, "install_profiler_cli", lambda repo: Path("/state/bin"))
    assert await run.main(FakeContext()) == "ran"
    assert os.environ["PATH"] == f"/state/bin{os.pathsep}{os.defpath}"
