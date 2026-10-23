"""Tests for the test-evidence comment of a team run."""

import pytest
from hackbot_agents.bug_fix import comment, gates
from hackbot_agents.bug_fix.gates import SUBMIT_TOOL, Gates, Review, parse_nits
from hackbot_runtime.actions import ActionsRecorder


def _gates() -> Gates:
    g = Gates(repo=None)
    g.record(
        "test_fails", True, fp="", evidence="`FooTest.Bar` crashes without the fix."
    )
    g.record("test_passes", True, fp="", evidence="`FooTest.*`: 16 of 16 pass.")
    g.reviews.append(Review(1, "b", ()))
    g.reviews.append(Review(0, "c", ("Put the copied code into one function.",)))
    g.record("verify", True, fp="c", evidence="Format, lint and build pass.")
    g.record(
        "associated_tests", True, fp="c", evidence="`dom/media/gtest`: 790 of 792."
    )
    g.record("not_checked", True, fp="", evidence="Android.")
    return g


def _sent() -> Gates:
    g = _gates()
    g.sent = _gates()
    return g


def test_comment_has_one_row_per_gate_from_the_evidence():
    text = comment.render(_gates(), "macOS", try_url="https://try/1")

    assert text.startswith(comment.INTRO)
    for label in (
        "Platform",
        "Test fails first",
        "Test passes",
        "Review",
        "Verify",
        "Associated tests",
        "Not checked",
    ):
        assert f"| {label} " in text
    assert "`FooTest.Bar` crashes without the fix." in text
    assert "Hackbot ran on macOS." in text
    assert (
        "2 rounds. Round 1: 1 blocking finding. Round 2: 0 blocking findings." in text
    )
    assert "Put the copied code into one function." in text
    assert "`dom/media/gtest`: 790 of 792." in text
    assert "Android. Try push: https://try/1" in text


def test_comment_table_columns_line_up():
    lines = [
        line
        for line in comment.render(_gates(), "Linux", try_url=None).splitlines()
        if line.startswith("|")
    ]
    assert len({line.index(" |", 2) for line in lines}) == 1
    assert len({len(line) for line in lines}) == 1


def test_a_missing_step_says_so():
    text = comment.render(Gates(repo=None), "Linux", try_url=None)
    assert "Not recorded." in text
    assert "Nothing recorded." in text


def test_cells_cannot_break_the_table():
    g = _gates()
    g.record("verify", True, fp="c", evidence="rg x | head\nc")
    assert "rg x \u00a6 head c" in comment.render(g, "Linux", try_url=None)


def test_parse_nits_reads_the_small_findings():
    text = (
        "**Findings**\n"
        "- NIT, `a.cpp:3`: run the Pacer gtests too.\n"
        "- BLOCKING, `b.cpp:9`: wrong lock.\n"
        "- `NIT` `c.h:1`: share the helper.\n"
        "BLOCKING: 1"
    )
    assert parse_nits(text) == (
        "`a.cpp:3`: run the Pacer gtests too.",
        "`c.h:1`: share the helper.",
    )


async def test_the_guard_keeps_the_gates_it_let_through(monkeypatch):
    g = _gates()
    g.record("test_fails", True, fp="a")
    g.record("test_passes", True, fp="b")
    monkeypatch.setattr(Gates, "fingerprint", lambda self: "c")

    assert await gates.submit_guard(g)({"tool_name": SUBMIT_TOOL}, None, None) == {}
    g.record("verify", True, fp="d", evidence="later change")

    assert g.sent.evidence["verify"] == "Format, lint and build pass."


def _recorder(tmp_path) -> ActionsRecorder:
    recorder = ActionsRecorder(artifacts_dir=tmp_path)
    comment.label_refs(recorder)
    return recorder


@pytest.mark.parametrize(("ref", "expected"), [(None, "patch"), ("fix", "fix")])
def test_comment_goes_on_the_new_revision(tmp_path, ref, expected):
    recorder = _recorder(tmp_path)
    recorder.record("try_server.push", {"auto": True}, reasoning="r")
    recorder.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r", ref=ref)

    comment.record_test_comment(recorder, _sent(), "macOS")

    last = recorder.actions[-1]
    assert last["type"] == "phabricator.add_comment"
    assert last["params"]["revision_id"] == f"{{{{actions.{expected}.revision_id}}}}"
    assert "Try push: {{actions.try.url}}" in last["params"]["text"]


def test_comment_on_an_updated_revision_uses_its_id(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.record(
        "phabricator.update_patch", {"revision_id": 42, "bug_id": 1}, reasoning="r"
    )

    comment.record_test_comment(recorder, _sent(), "macOS")

    assert recorder.actions[-1]["params"]["revision_id"] == 42


@pytest.mark.parametrize("let_through", [True, False])
def test_no_comment_without_a_patch_the_guard_let_through(tmp_path, let_through):
    recorder = _recorder(tmp_path)
    if not let_through:
        recorder.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r")
    gates_state = _sent() if let_through else _gates()

    comment.record_test_comment(recorder, gates_state, "macOS")

    assert "phabricator.add_comment" not in [a["type"] for a in recorder.actions]


def test_each_unlabeled_patch_and_try_gets_its_own_ref(tmp_path):
    recorder = _recorder(tmp_path)
    for _ in range(2):
        recorder.record("phabricator.submit_patch", {"bug_id": 1}, reasoning="r")
        recorder.record("try_server.push", {"auto": True}, reasoning="r")
    assert [a["ref"] for a in recorder.actions] == ["patch", "try", "patch-2", "try-2"]


def test_comment_shows_paths_relative_to_the_checkout(tmp_path):
    g = _gates()
    g.repo = tmp_path
    g.record("verify", True, fp="c", evidence=f"`{tmp_path}/dom/media/A.cpp` builds.")
    text = comment.render(g, "Linux", try_url=None)
    assert "`dom/media/A.cpp` builds." in text
    assert str(tmp_path) not in text


def test_comment_keeps_small_findings_short():
    g = _gates()
    g.reviews.append(Review(0, "c", tuple("x" * 300 + f" {i}" for i in range(5))))
    review_row = next(
        line
        for line in comment.render(g, "Linux", try_url=None).splitlines()
        if line.startswith("| Review")
    )
    assert review_row.count("x" * 150) == 3
    assert "x" * 200 not in review_row
    assert "And 2 more." in review_row


def test_an_unfinished_review_lists_the_open_findings():
    g = _gates()
    g.reviews[:] = [Review(1, "c", blockers=("`a.cpp:3`: wrong lock.",))] * 3
    text = comment.render(g, "Linux", try_url=None)
    assert "Review did not finish after 3 rounds." in text
    assert "| Open findings" in text
    assert "`a.cpp:3`: wrong lock." in text
    assert "need a person's decision" in text


def test_a_finished_review_has_no_open_findings_row():
    assert "Open findings" not in comment.render(_gates(), "Linux", try_url=None)
