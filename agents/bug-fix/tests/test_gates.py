"""Tests for the TDD, review and verify gates of a team run."""

import subprocess

import pytest
from hackbot_agents.bug_fix import gates
from hackbot_agents.bug_fix.gates import (
    CHECK_STEPS,
    MAX_REVIEW_ROUNDS,
    REVIEWER,
    SUBMIT_TOOL,
    UPDATE_TOOL,
    Gates,
    Review,
    fingerprint,
    parse_blocking,
)


def _ready(fp="fp1") -> Gates:
    g = Gates(repo=None)
    g.record("test_fails", True, fp="fp0")
    g.record("test_passes", True, fp=fp)
    g.reviews.append(Review(0, fp))
    g.record("verify", True, fp=fp)
    g.record("associated_tests", True, fp=fp)
    return g


def test_all_gates_on_the_current_diff_allow_a_submit():
    assert _ready().refusal(SUBMIT_TOOL, "fp1") is None


@pytest.mark.parametrize("missing", ["test_fails", "test_passes"])
def test_each_tdd_gate_is_required(missing):
    g = _ready()
    del g.tdd[missing]
    assert missing in g.refusal(SUBMIT_TOOL, "fp1")


@pytest.mark.parametrize("step", CHECK_STEPS)
@pytest.mark.parametrize("tool_name", [SUBMIT_TOOL, UPDATE_TOOL])
def test_a_failed_check_does_not_count(step, tool_name):
    g = _ready()
    g.record(step, False, fp="fp1")
    assert step in g.refusal(tool_name, "fp1")


def test_a_review_is_required():
    g = _ready()
    g.reviews.clear()
    assert REVIEWER in g.refusal(SUBMIT_TOOL, "fp1")


def test_a_change_after_review_and_verify_needs_both_again():
    assert "changed after the last review" in _ready().refusal(SUBMIT_TOOL, "fp2")


@pytest.mark.parametrize("step", CHECK_STEPS)
def test_a_change_after_a_check_needs_it_again(step):
    g = _ready()
    g.reviews.append(Review(0, "fp2"))
    for other in CHECK_STEPS:
        if other != step:
            g.record(other, True, fp="fp2")
    assert step in g.refusal(SUBMIT_TOOL, "fp2")


def test_blocking_findings_send_the_agent_back_to_fix():
    g = _ready()
    g.reviews.append(Review(2, "fp1"))
    assert "2 blocking" in g.refusal(SUBMIT_TOOL, "fp1")


def test_review_rounds_are_limited():
    g = _ready()
    g.reviews[:] = [Review(1, "fp1")] * MAX_REVIEW_ROUNDS
    assert "do not submit" in g.refusal(SUBMIT_TOOL, "fp1")


def test_an_unreadable_review_result_blocks():
    g = _ready()
    g.reviews.append(Review(None, "fp1"))
    assert "BLOCKING:" in g.refusal(SUBMIT_TOOL, "fp1")


def test_an_update_to_an_existing_revision_needs_no_tdd():
    g = _ready()
    g.tdd.clear()
    assert g.refusal(UPDATE_TOOL, "fp1") is None


def test_unknown_steps_are_rejected():
    with pytest.raises(ValueError):
        Gates(repo=None).record("deploy", True, fp="fp")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("findings...\nBLOCKING: 0", 0),
        ("findings...\nBLOCKING: 3\n", 3),
        ("BLOCKING: 1\nmore text\nBLOCKING: 2", 2),
        ("no count here", None),
    ],
)
def test_parse_blocking(text, expected):
    assert parse_blocking(text) == expected


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_fingerprint_follows_edits_and_new_files_but_not_ignored_ones(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("obj*/\n")
    (tmp_path / "a.cpp").write_text("one\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b")

    clean = fingerprint(tmp_path)
    (tmp_path / "objdir-ff-asan").mkdir()
    (tmp_path / "objdir-ff-asan" / "libxul.so").write_text("big\n")
    assert fingerprint(tmp_path) == clean

    (tmp_path / "a.cpp").write_text("two\n")
    edited = fingerprint(tmp_path)
    (tmp_path / "test.cpp").write_text("new\n")
    with_new_file = fingerprint(tmp_path)

    assert len({clean, edited, with_new_file}) == 3
    assert fingerprint(tmp_path) == with_new_file
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "?? test.cpp" in status, "the checkout's own index must not change"


async def test_record_gate_records_on_the_current_diff(monkeypatch):
    g = Gates(repo=None)
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "fp1")
    await gates.record_gate(g, "verify", True, "lint clean")
    assert g.checked["verify"] == "fp1"


async def test_submit_guard_denies_until_ready(monkeypatch):
    g = _ready()
    g.tdd.clear()
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "fp1")
    guard = gates.submit_guard(g)

    denied = await guard({"tool_name": SUBMIT_TOOL}, None, None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"

    g.tdd.update(test_fails=True, test_passes=True)
    assert await guard({"tool_name": SUBMIT_TOOL}, None, None) == {}


async def test_review_hook_records_only_the_reviewer(monkeypatch):
    g = Gates(repo=None)
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "fp1")
    hook = gates.review_hook(g)

    await hook(
        {
            "tool_input": {"subagent_type": REVIEWER},
            "tool_response": {"content": [{"type": "text", "text": "x\nBLOCKING: 1"}]},
        },
        None,
        None,
    )
    await hook(
        {
            "tool_input": {"subagent_type": "investigator"},
            "tool_response": "BLOCKING: 0",
        },
        None,
        None,
    )

    assert g.reviews == [Review(1, "fp1")]
