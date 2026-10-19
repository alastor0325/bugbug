from datetime import UTC, datetime

from hackbot_agents.review_load.conduit import parse_revision, parse_transactions
from testdata import ALICE, CAROL, GROUP, add_reviewers, revision, ts, txn


def test_parse_revision():
    rev = parse_revision(
        revision(42, reviewers={GROUP: "added", ALICE: "accepted"}), []
    )
    assert rev.id == 42
    assert rev.author_phid == CAROL
    assert rev.bug_id == 1900000
    assert rev.title == "Bug 1900000 - Change 42"
    assert rev.status == "needs-review"
    assert rev.is_draft is False
    assert rev.created == datetime(2026, 10, 1, tzinfo=UTC)
    assert rev.reviewers == {GROUP: "added", ALICE: "accepted"}


def test_parse_revision_without_a_bug():
    assert parse_revision(revision(1, bug=None), []).bug_id is None
    assert parse_revision(revision(1, bug=""), []).bug_id is None


def test_parse_transactions_keeps_added_reviewers():
    [t] = parse_transactions([add_reviewers(ts(2026, 10, 2), GROUP, ALICE)])
    assert t.type == "reviewers"
    assert t.created == datetime(2026, 10, 2, tzinfo=UTC)
    assert t.added_reviewers == frozenset({GROUP, ALICE})


def test_removed_reviewers_are_not_added():
    raw = add_reviewers(ts(2026, 10, 2), ALICE)
    raw["fields"]["operations"][0]["operation"] = "remove"
    [t] = parse_transactions([raw])
    assert t.added_reviewers == frozenset()


def test_untyped_transactions_are_dropped():
    assert parse_transactions([txn(None, ts(2026, 10, 2))]) == []
