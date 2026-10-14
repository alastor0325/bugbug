"""Tests for HackbotContext capabilities and results plumbing."""

import json
import logging
import subprocess
from pathlib import Path

import pytest
from hackbot_runtime import HackbotContext
from hackbot_runtime.changes import ChangeSet
from hackbot_runtime.config import FirefoxConfig, HackbotConfig, SourceConfig


def _hb(tmp_path, config: HackbotConfig) -> HackbotContext:
    hb = HackbotContext(run_id="local-test", artifacts_dir=tmp_path / "artifacts")
    hb._config = config
    return hb


async def test_source_repo_without_declaration_raises(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="\\[source\\]"):
        await hb.prepare_repo()


def test_firefox_without_declaration_raises(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="\\[firefox\\]"):
        _ = hb.firefox


def test_firefox_disabled_raises(tmp_path):
    cfg = HackbotConfig(
        source=SourceConfig(repo_url="x"), firefox=FirefoxConfig(enabled=False)
    )
    hb = _hb(tmp_path, cfg)
    with pytest.raises(RuntimeError, match="\\[firefox\\]"):
        _ = hb.firefox


def _git_repo(path: Path) -> Path:
    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    path.mkdir()
    git("init", "-q")
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "one",
    )
    git("tag", "first")
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "two",
    )
    return path


def _ready(monkeypatch, path: Path, ref: str | None = None):
    def fake_ensure(*args, **kwargs):
        raise AssertionError("ensure_source_repo must not run on a ready checkout")

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.setenv("SOURCE_REPO", str(path))
    monkeypatch.setenv("SOURCE_REPO_READY", "1")
    if ref is None:
        monkeypatch.delenv("SOURCE_REF", raising=False)
    else:
        monkeypatch.setenv("SOURCE_REF", ref)


async def test_source_repo_ready_skips_the_clone_and_reset(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "worktree")
    _ready(monkeypatch, repo)

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    assert await hb.prepare_repo() == repo
    assert hb.repo_path == repo


async def test_source_repo_ready_refuses_a_path_that_is_not_a_checkout(
    tmp_path, monkeypatch
):
    (tmp_path / "plain").mkdir()
    _ready(monkeypatch, tmp_path / "plain")

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    with pytest.raises(RuntimeError, match="not a git checkout"):
        await hb.prepare_repo()


async def test_source_repo_ready_refuses_a_subdirectory_of_a_checkout(
    tmp_path, monkeypatch
):
    repo = _git_repo(tmp_path / "worktree")
    (repo / "sub").mkdir()
    _ready(monkeypatch, repo / "sub")

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    with pytest.raises(RuntimeError, match="not the top of a git checkout"):
        await hb.prepare_repo()


async def test_source_repo_ready_accepts_a_link_to_a_checkout(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "worktree")
    (tmp_path / "link").symlink_to(repo)
    _ready(monkeypatch, tmp_path / "link")

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    assert await hb.prepare_repo() == tmp_path / "link"


async def test_source_repo_ready_warns_when_head_is_not_the_requested_ref(
    tmp_path, monkeypatch, caplog
):
    repo = _git_repo(tmp_path / "worktree")
    _ready(monkeypatch, repo, ref="first")

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    with caplog.at_level(logging.WARNING, logger="hackbot_runtime.context"):
        assert await hb.prepare_repo() == repo
    assert "not at the requested ref 'first'" in caplog.text


async def test_source_repo_ready_is_quiet_when_head_is_the_requested_ref(
    tmp_path, monkeypatch, caplog
):
    repo = _git_repo(tmp_path / "worktree")
    _ready(monkeypatch, repo, ref="HEAD")

    hb = _hb(tmp_path, HackbotConfig(source=SourceConfig(repo_url="x")))
    with caplog.at_level(logging.WARNING, logger="hackbot_runtime.context"):
        await hb.prepare_repo()
    assert "requested ref" not in caplog.text


async def test_source_repo_prepares_and_honors_env_override(tmp_path, monkeypatch):
    calls = []

    def fake_ensure(
        path: Path, repo_url: str, ref: str | None = None, depth: int | None = None
    ) -> None:
        calls.append((path, repo_url, ref, depth))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.setenv("SOURCE_REPO", str(tmp_path / "from-env"))
    monkeypatch.delenv("SOURCE_REF", raising=False)
    monkeypatch.delenv("SOURCE_REPO_READY", raising=False)

    cfg = HackbotConfig(
        source=SourceConfig(
            repo_url="https://example.com/r.git",
            checkout_path=Path("/from/toml"),
        )
    )
    hb = _hb(tmp_path, cfg)

    assert await hb.prepare_repo() == tmp_path / "from-env"
    assert calls == [(tmp_path / "from-env", "https://example.com/r.git", None, None)]


async def test_source_repo_honors_source_ref_env(tmp_path, monkeypatch):
    calls = []

    def fake_ensure(
        path: Path, repo_url: str, ref: str | None = None, depth: int | None = None
    ) -> None:
        calls.append((path, repo_url, ref, depth))

    monkeypatch.setattr("hackbot_runtime.context.ensure_source_repo", fake_ensure)
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setenv("SOURCE_REF", "deadbeef")

    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)

    assert await hb.prepare_repo() == Path("/from/toml")
    assert calls == [(Path("/from/toml"), "r", "deadbeef", None)]


async def test_source_repo_uses_toml_path_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REPO", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    cfg = HackbotConfig(
        source=SourceConfig(repo_url="r", checkout_path=Path("/from/toml"))
    )
    hb = _hb(tmp_path, cfg)
    assert await hb.prepare_repo() == Path("/from/toml")


async def test_prepare_repo_explicit_ref_overrides_env(tmp_path, monkeypatch):
    refs = []
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo",
        lambda path, repo_url, ref=None, depth=None: refs.append(ref),
    )
    monkeypatch.setenv("SOURCE_REF", "from-env")
    cfg = HackbotConfig(source=SourceConfig(repo_url="r", checkout_path=Path("/x")))
    hb = _hb(tmp_path, cfg)

    # An explicit ref (e.g. a revision's base commit) wins over SOURCE_REF, and
    # is threaded straight to ensure_source_repo — no env mutation.
    await hb.prepare_repo(ref="base9")
    assert refs == ["base9"]


async def test_prepare_repo_conflicting_ref_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REF", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    cfg = HackbotConfig(source=SourceConfig(repo_url="r", checkout_path=Path("/x")))
    hb = _hb(tmp_path, cfg)

    await hb.prepare_repo()  # prepares at the default ref first
    with pytest.raises(RuntimeError, match="already prepared"):
        await hb.prepare_repo(ref="base9")


async def test_checkout_moves_the_base_the_diff_is_taken_against(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_REF", raising=False)
    monkeypatch.setattr(
        "hackbot_runtime.context.ensure_source_repo", lambda *a, **k: None
    )
    checked_out = []
    monkeypatch.setattr(
        "hackbot_runtime.context.checkout_commit",
        lambda path, ref: checked_out.append(ref),
    )
    heads = iter(["headcommit", "earliercommit"])
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.base_commit", lambda path: next(heads)
    )
    cfg = HackbotConfig(source=SourceConfig(repo_url="r", checkout_path=Path("/x")))
    hb = _hb(tmp_path, cfg)
    await hb.prepare_repo()
    assert hb._source_base == "headcommit"

    assert hb.checkout("earlier") == "earliercommit"
    assert checked_out == ["earlier"]
    # Both bases move: the diff and any try push are taken against the new one.
    assert hb._source_base == hb._published_base == "earliercommit"


def test_checkout_needs_a_prepared_repo(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    with pytest.raises(RuntimeError, match="not prepared"):
        hb.checkout("earlier")


def test_results_plumbing(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())

    assert hb.run_id == "local-test"

    log = tmp_path / "agent.log"
    log.write_text("hello")
    key = hb.publish_file("logs/agent.log", log)
    assert key == "logs/agent.log"
    written = tmp_path / "artifacts" / "local-test" / "logs" / "agent.log"
    assert written.read_text() == "hello"

    hb.actions.record("bugzilla.update_bug", {"bug_id": 1}, reasoning="r")
    assert hb.actions.actions[0]["type"] == "bugzilla.update_bug"


def _hb_with_source(tmp_path, monkeypatch):
    """Wire a context to publish changes without a real checkout.

    Sets a recorded source base and mocks changes.collect so
    publish_changes() runs its body.
    """
    cfg = HackbotConfig(source=SourceConfig(repo_url="https://example.com/r.git"))
    hb = _hb(tmp_path, cfg)
    hb._source_base = hb._published_base = "basecommit"
    # prepare_repo would normally clone and set this; publish_changes only reads
    # repo_path and passes it to the (mocked) changes helpers, so a bare path is
    # enough here.
    hb._repo_path = tmp_path / "src"
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.collect",
        lambda repo, base, repo_url, message: ChangeSet(
            patch=b"x", metadata={"base": base}
        ),
    )
    return hb


@pytest.mark.parametrize(
    ("actions", "message"),
    [
        # The try push is recorded first, as in mozilla/bugbug#6773.
        (
            [
                ("try_server.push", {"tasks": ["t"], "title": "Bug 1 - verify"}),
                ("phabricator.submit_patch", {"bug_id": 1, "title": "Bug 1 - Fix"}),
            ],
            "Bug 1 - Fix",
        ),
        (
            [
                ("phabricator.update_patch", {"revision_id": 42}),
                ("try_server.push", {"tasks": ["t"], "title": "Bug 1 - verify"}),
            ],
            "Bug 1 - verify",
        ),
        (
            [("try_server.push", {"tasks": ["t"], "title": None})],
            "Uncommitted agent changes",
        ),
    ],
)
def test_publish_changes_names_the_agents_commit_after_a_recorded_title(
    tmp_path, monkeypatch, actions, message
):
    hb = _hb_with_source(tmp_path, monkeypatch)
    messages = []

    def _collect(repo, base, repo_url, message):
        messages.append(message)
        return ChangeSet(patch=b"x", metadata={"base": base})

    monkeypatch.setattr("hackbot_runtime.context.changes.collect", _collect)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff", lambda *a: None
    )
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_try_push", lambda *a: None
    )
    for action_type, params in actions:
        hb.actions.record(action_type, params, reasoning="r")

    hb.publish_changes()

    assert messages == [message]


@pytest.mark.parametrize(
    ("action_type", "params"),
    [
        ("phabricator.submit_patch", {"bug_id": 1, "title": "Fix"}),
        ("phabricator.update_patch", {"revision_id": 42}),
    ],
)
def test_publish_changes_builds_phabricator_diff_when_action_recorded(
    tmp_path, monkeypatch, action_type, params
):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff",
        lambda repo, base, repo_url: {
            "diff": {"changes": [], "sourceControlBaseRevision": base},
            "local_commits": {"node": {"author": "A"}},
        },
    )
    hb.actions.record(action_type, params, reasoning="r")

    hb.publish_changes()

    # One artifact holds both the creatediff payload and the local:commits data.
    submission = json.loads(
        (
            tmp_path / "artifacts" / "local-test" / "changes" / "phabricator_diff.json"
        ).read_text()
    )
    assert submission["diff"]["sourceControlBaseRevision"] == "basecommit"
    assert submission["local_commits"]["node"]["author"] == "A"


def test_publish_changes_builds_try_push_when_action_recorded(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_try_push",
        lambda repo, base: {"base_commit": base, "patches": ["cGF0Y2g="]},
    )
    hb.actions.record(
        "try_server.push",
        {"tasks": ["build-linux64/opt"], "title": None},
        reasoning="r",
    )

    hb.publish_changes()

    payload = json.loads(
        (
            tmp_path / "artifacts" / "local-test" / "changes" / "try_push.json"
        ).read_text()
    )
    assert payload["base_commit"] == "basecommit"


def test_try_push_uses_the_published_base_not_a_local_one(tmp_path, monkeypatch):
    # A stacked-revision checkout seeds local commits and re-records the source
    # base onto one of them (see revision.checkout_revision). Lando has to
    # resolve the base in its own clone, so it must still be given the commit
    # the checkout was fetched at — a local sha would look valid (40 hex chars)
    # and then fail at Lando.
    hb = _hb_with_source(tmp_path, monkeypatch)
    bases = {}

    def _try_push(repo, base):
        bases["try"] = base
        return {"base_commit": base, "patches": []}

    def _phabricator_diff(repo, base, repo_url):
        bases["diff"] = base
        return None

    monkeypatch.setattr("hackbot_runtime.context.changes.build_try_push", _try_push)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff", _phabricator_diff
    )
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.base_commit", lambda repo: "localseededsha"
    )
    hb.record_source_base()
    hb.actions.record("try_server.push", {"tasks": ["t"], "title": None}, reasoning="r")
    hb.actions.record("phabricator.update_patch", {"revision_id": 1}, reasoning="r")

    hb.publish_changes()

    assert bases["try"] == "basecommit"
    # The submitted diff still measures from the seeded commit, so it covers
    # only the revision plus the agent's edits.
    assert bases["diff"] == "localseededsha"


def test_publish_changes_skips_try_push_without_action(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_try_push",
        lambda *a, **k: called.append(a) or {},
    )
    hb.actions.record("bugzilla.add_comment", {"bug_id": 1}, reasoning="r")

    hb.publish_changes()

    assert called == []
    assert not (
        tmp_path / "artifacts" / "local-test" / "changes" / "try_push.json"
    ).exists()


def test_publish_changes_skips_phabricator_diff_without_action(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.build_phabricator_diff",
        lambda *a, **k: called.append(a) or {},
    )
    hb.actions.record("bugzilla.add_comment", {"bug_id": 1}, reasoning="r")

    hb.publish_changes()

    assert called == []
    written = (
        tmp_path / "artifacts" / "local-test" / "changes" / "phabricator_diff.json"
    )
    assert not written.exists()


def test_source_changed_is_false_for_an_agent_that_touched_no_source(tmp_path):
    hb = _hb(tmp_path, HackbotConfig())
    assert hb.source_changed is False


def test_source_changed_asks_git_without_disturbing_the_tree(tmp_path, monkeypatch):
    # Read-only, unlike collect(): a notification gating on this must not commit
    # the working tree out from under the agent.
    hb = _hb_with_source(tmp_path, monkeypatch)
    collected = []
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.collect",
        lambda repo, base, repo_url: collected.append(base),
    )
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.has_changes", lambda repo, base: True
    )
    assert hb.source_changed is True
    assert collected == []


def test_source_changed_is_false_when_the_agent_changed_nothing(tmp_path, monkeypatch):
    hb = _hb_with_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "hackbot_runtime.context.changes.has_changes", lambda repo, base: False
    )
    assert hb.source_changed is False
