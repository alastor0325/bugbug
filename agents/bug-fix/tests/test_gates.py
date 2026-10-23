"""Tests for the TDD, review and verify gates of a team run."""

import subprocess

import pytest
from hackbot_agents.bug_fix import gates
from hackbot_agents.bug_fix.gates import (
    CHECK_STEPS,
    MAX_REVIEW_ROUNDS,
    REVIEWER,
    SUBMIT_TOOL,
    Gates,
    Review,
    fingerprint,
    parse_blocking,
)


def _ready(fp="fp1", *, require_tdd=True) -> Gates:
    g = Gates(repo=None, require_tdd=require_tdd)
    g.record("test_fails", True, fp="fp0")
    g.record("test_passes", True, fp=fp)
    g.record("verify", True, fp=fp)
    g.reviews.append(Review(0, fp))
    g.record("associated_tests", True, fp=fp)
    return g


def test_all_gates_on_the_current_diff_allow_a_submit():
    assert _ready().refusal("fp1") is None


@pytest.mark.parametrize("missing", ["test_fails", "test_passes"])
def test_each_tdd_gate_is_required(missing):
    g = _ready()
    del g.passed[missing]
    assert missing in g.refusal("fp1")


def test_test_passes_must_follow_a_change():
    g = _ready()
    g.record("test_passes", True, fp="fp0")
    assert "same tree" in g.refusal("fp1")


def test_a_new_failing_test_needs_a_new_pass():
    g = _ready()
    g.record("test_fails", True, fp="fp0")
    assert "test_passes" in g.refusal("fp1")


def test_an_update_run_needs_no_tdd():
    g = _ready(require_tdd=False)
    g.passed.pop("test_fails")
    g.passed.pop("test_passes")
    assert g.refusal("fp1") is None


@pytest.mark.parametrize("step", CHECK_STEPS)
def test_a_failed_check_does_not_count(step):
    g = _ready()
    g.record(step, False, fp="fp1")
    assert step in g.refusal("fp1")


@pytest.mark.parametrize("step", CHECK_STEPS)
def test_a_change_after_a_check_needs_it_again(step):
    g = _ready()
    g.reviews.append(Review(0, "fp2"))
    for other in CHECK_STEPS:
        if other != step:
            g.record(other, True, fp="fp2")
    assert step in g.refusal("fp2")


def test_a_review_is_required():
    g = _ready()
    g.reviews.clear()
    assert REVIEWER in g.refusal("fp1")


def test_a_change_after_review_needs_review_again():
    assert "changed after the last review" in _ready().refusal("fp2")


def test_blocking_findings_send_the_agent_back_to_fix():
    g = _ready()
    g.reviews.append(Review(2, "fp1"))
    assert "2 blocking" in g.refusal("fp1")


def test_an_unreadable_review_result_blocks():
    g = _ready()
    g.reviews.append(Review(None, "fp1"))
    assert "BLOCKING:" in g.refusal("fp1")


def test_unknown_steps_are_rejected():
    with pytest.raises(ValueError):
        Gates(repo=None).record("deploy", True, fp="fp")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("findings...\nBLOCKING: 0", 0),
        ("findings...\nBLOCKING: 3\n", 3),
        ("BLOCKING: 1\nmore text\nBLOCKING: 2", 2),
        ("`BLOCKING: 0`", 0),
        ("**BLOCKING: 1**", 1),
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
    assert g.passed["verify"] == "fp1"
    assert g.evidence["verify"] == "lint clean"


async def test_submit_guard_denies_until_ready_and_keeps_what_it_let_through(
    monkeypatch,
):
    g = _ready()
    del g.passed["test_fails"]
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "fp1")
    guard = gates.submit_guard(g)

    denied = await guard({"tool_name": SUBMIT_TOOL}, None, None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert g.sent is None

    g.record("test_fails", True, fp="fp0")
    g.record("test_passes", True, fp="fp1")
    assert await guard({"tool_name": SUBMIT_TOOL}, None, None) == {}
    assert g.sent_fp == "fp1"


async def test_submit_guard_fails_closed(monkeypatch):
    def boom(self):
        raise subprocess.CalledProcessError(128, "git add -A")

    monkeypatch.setattr(Gates, "fingerprint", boom)
    denied = await gates.submit_guard(_ready())({"tool_name": SUBMIT_TOOL}, None, None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


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


async def test_review_hook_records_an_unreadable_round_on_error(monkeypatch):
    def boom(self):
        raise OSError("disk")

    g = Gates(repo=None)
    monkeypatch.setattr(Gates, "fingerprint", boom)
    await gates.review_hook(g)(
        {"tool_input": {"subagent_type": REVIEWER}, "tool_response": "BLOCKING: 0"},
        None,
        None,
    )
    assert g.reviews == [Review(None, "")]


async def test_record_gate_refuses_long_evidence(monkeypatch):
    from agent_tools.registry import ToolError

    g = Gates(repo=None)
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "fp1")
    with pytest.raises(ToolError, match="shorter"):
        await gates.record_gate(g, "verify", True, "x" * (gates.MAX_EVIDENCE + 1))
    assert "verify" not in g.passed


def test_at_the_round_limit_the_patch_goes_out_with_open_findings():
    g = _ready()
    g.reviews[:] = [Review(1, "fp1", blockers=("wrong lock",))] * MAX_REVIEW_ROUNDS
    assert g.refusal("fp1") is None
    assert g.review_open()


def test_below_the_round_limit_blocking_findings_still_refuse():
    g = _ready()
    g.reviews[:] = [Review(1, "fp1")] * (MAX_REVIEW_ROUNDS - 1)
    assert "blocking" in g.refusal("fp1")
    assert not g.review_open()


def test_an_unreadable_round_counts_toward_the_limit():
    g = _ready()
    g.reviews[:] = [Review(None, "fp1")] * MAX_REVIEW_ROUNDS
    assert g.refusal("fp1") is None


def test_at_the_limit_the_other_gates_still_hold():
    g = _ready()
    g.reviews[:] = [Review(1, "fp1")] * MAX_REVIEW_ROUNDS
    g.record("verify", False, fp="fp1")
    assert "verify" in g.refusal("fp1")


def test_parse_blockers_reads_the_blocking_findings():
    text = (
        "- BLOCKING, `a.cpp:3`: wrong lock.\n"
        "- NIT, `b.cpp:9`: rename.\n"
        "- `BLOCKING` `c.h:1`: freed sink is used.\n"
        "BLOCKING: 2"
    )
    assert gates.parse_blockers(text) == (
        "`a.cpp:3`: wrong lock.",
        "`c.h:1`: freed sink is used.",
    )


def test_the_round_limit_comes_from_the_gates():
    g = _ready()
    g.max_review_rounds = 5
    g.reviews[:] = [Review(1, "fp1")] * 4
    assert "blocking" in g.refusal("fp1")
    g.reviews.append(Review(1, "fp1"))
    assert g.refusal("fp1") is None
