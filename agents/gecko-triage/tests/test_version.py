"""Tests for `findings.agent_version`: the code that made a run."""

import subprocess
from pathlib import Path

from hackbot_agents.gecko_triage import version
from hackbot_agents.gecko_triage.agent import GeckoTriageResult
from hackbot_agents.gecko_triage.version import (
    COMMIT_ENV,
    agent_version,
    current_agent_version,
    git_commit,
    package_version,
)

DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
CLOUDBUILD = Path(__file__).parents[2] / "cloudbuild.yaml"


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> str:
    """A git checkout with one commit, and its short commit."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(
        tmp_path,
        "-c",
        "user.name=T",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-qm",
        "x",
    )
    return _git(tmp_path, "rev-parse", "--short", "HEAD")


def test_a_git_checkout_gives_its_short_commit(tmp_path):
    sha = _repo(tmp_path)
    assert git_commit(tmp_path) == sha
    # The checkout wins over the image's env var.
    assert agent_version(tmp_path, {COMMIT_ENV: "image"}) == sha
    # A dirty tree is visible.
    (tmp_path / "f").write_text("y")
    assert git_commit(tmp_path) == f"{sha}-dirty"


def test_a_tag_does_not_replace_the_commit(tmp_path):
    sha = _repo(tmp_path)
    _git(tmp_path, "tag", "v1")
    assert git_commit(tmp_path) == sha


def test_outside_a_checkout_there_is_no_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert git_commit(tmp_path) is None


def test_without_git_there_is_no_commit(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(version.subprocess, "run", missing)
    assert git_commit() is None


def test_a_failing_git_gives_no_commit(monkeypatch):
    for error in (
        subprocess.TimeoutExpired(["git"], 5),
        subprocess.CalledProcessError(128, ["git"]),
        ValueError("odd"),
    ):

        def fail(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(version.subprocess, "run", fail)
        assert git_commit() is None, error
        assert agent_version(env={COMMIT_ENV: "image"}) == "image"


def test_the_image_gives_the_commit_in_its_env(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "git_commit", lambda where: None)
    assert agent_version(tmp_path, {COMMIT_ENV: " abc1234 \n"}) == "abc1234"


def test_the_last_fallback_is_the_package_version(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "git_commit", lambda where: None)
    for env in ({}, {COMMIT_ENV: ""}, {COMMIT_ENV: "  "}):
        assert agent_version(tmp_path, env) == f"{package_version()}+unknown"


def test_an_unknown_version_never_fails_the_run(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no version")

    monkeypatch.setattr(version, "agent_version", boom)
    current_agent_version.cache_clear()
    try:
        assert current_agent_version() == "unknown"
    finally:
        current_agent_version.cache_clear()


def test_every_result_names_the_agent_version():
    result = GeckoTriageResult(bug_id=1, num_turns=0)
    assert result.agent_version == current_agent_version()
    assert result.model_dump()["agent_version"]


def test_the_image_takes_the_commit_as_a_build_arg():
    text = DOCKERFILE.read_text()
    assert "ARG GIT_COMMIT" in text
    assert f"ENV {COMMIT_ENV}=$GIT_COMMIT" in text
    assert "--build-arg GIT_COMMIT=$COMMIT_SHA" in CLOUDBUILD.read_text()
