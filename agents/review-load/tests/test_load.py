from datetime import UTC, datetime

import pytest
from hackbot_agents.review_load.conduit import parse_revision
from hackbot_agents.review_load.config import AuthorSettings, ReportSettings
from hackbot_agents.review_load.load import (
    build_report,
    classify_author,
    direct_only,
    in_scope,
    report_rows,
    waiting_since,
)
from testdata import (
    ALICE,
    BOB,
    CAROL,
    GROUP,
    OUTSIDER,
    add_reviewers,
    revision,
    ts,
    txn,
)

# Monday.
NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
WATCHED = {GROUP: "#example-reviewers", ALICE: "alice", BOB: "bob"}
SETTINGS = ReportSettings(slack_channel="#x")


def rev(rev_id, txns=(), **kwargs):
    return parse_revision(revision(rev_id, **kwargs), list(txns))


class TestWaitingSince:
    def test_defaults_to_creation(self):
        r = rev(1, created=ts(2026, 10, 5))
        assert waiting_since(r, GROUP) == datetime(2026, 10, 5, tzinfo=UTC)

    def test_uses_the_last_time_the_reviewer_was_added(self):
        r = rev(
            1,
            [
                add_reviewers(ts(2026, 10, 6), ALICE),
                add_reviewers(ts(2026, 10, 8), ALICE),
            ],
            reviewers={ALICE: "added"},
        )
        assert waiting_since(r, ALICE) == datetime(2026, 10, 8, tzinfo=UTC)

    def test_another_reviewer_add_does_not_count(self):
        r = rev(1, [add_reviewers(ts(2026, 10, 8), BOB)], reviewers={ALICE: "added"})
        assert waiting_since(r, ALICE) == datetime(2026, 10, 1, tzinfo=UTC)

    @pytest.mark.parametrize("kind", ["request-review", "reopen", "reclaim", "update"])
    def test_a_return_to_review_restarts_the_count(self, kind):
        r = rev(1, [add_reviewers(ts(2026, 10, 2), ALICE), txn(kind, ts(2026, 10, 9))])
        assert waiting_since(r, ALICE) == datetime(2026, 10, 9, tzinfo=UTC)

    @pytest.mark.parametrize("kind", ["comment", "plan-changes", "accept"])
    def test_other_transactions_do_not_restart_it(self, kind):
        r = rev(1, [add_reviewers(ts(2026, 10, 2), ALICE), txn(kind, ts(2026, 10, 9))])
        assert waiting_since(r, ALICE) == datetime(2026, 10, 2, tzinfo=UTC)


class TestClassifyAuthor:
    AUTHORS = AuthorSettings(
        staff_accounts=["contractor"], external_accounts=["volunteer"]
    )

    @pytest.mark.parametrize(
        ("handle", "email", "kind"),
        [
            ("dev", "dev@mozilla.com", "staff"),
            ("dev", "Dev@Mozilla.COM", "staff"),
            ("dev", "dev@example.com", "external"),
            ("dev", "dev@notmozilla.com", "external"),
            ("dev", None, "unknown"),
            ("contractor", "c@example.com", "staff"),
            ("contractor", None, "staff"),
            ("volunteer", "v@mozilla.com", "external"),
        ],
    )
    def test_kinds(self, handle, email, kind):
        assert classify_author(handle, email, self.AUTHORS) == kind


def _report(revisions, kinds=None):
    return build_report(
        revisions,
        watched=WATCHED,
        author_kinds=kinds or {},
        now=NOW,
        settings=SETTINGS,
    )


class TestBuildReport:
    def test_counts_by_bucket_and_author(self):
        report = _report(
            [
                rev(1, created=ts(2026, 10, 19, 6)),
                rev(2, created=ts(2026, 10, 15, 12), author=OUTSIDER),
                rev(3, created=ts(2026, 10, 13, 12)),
                rev(4, created=ts(2026, 10, 1)),
            ],
            kinds={CAROL: "staff", OUTSIDER: "external"},
        )
        assert [b.total for b in report.buckets] == [1, 1, 1, 1]
        assert report.buckets[1].by_author == {"external": 1}
        assert report.buckets[3].by_author == {"staff": 1}
        assert report.new == report.buckets[0]
        assert report.total == 4

    def test_unknown_author_kind(self):
        report = _report([rev(1)])
        assert report.reviews[0].author == "unknown"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": "changes-planned"},
            {"status": "accepted"},
            {"draft": True},
            {"reviewers": {GROUP: "accepted"}},
            {"reviewers": {GROUP: "resigned"}},
            {"reviewers": {OUTSIDER: "added"}},
            {"reviewers": {ALICE: "added"}, "author": ALICE},
        ],
    )
    def test_not_waiting(self, kwargs):
        assert _report([rev(1, **kwargs)]).total == 0

    def test_blocking_counts(self):
        assert _report([rev(1, reviewers={ALICE: "blocking"})]).total == 1

    def test_a_revision_counts_once_but_for_each_reviewer(self):
        report = _report(
            [
                rev(
                    1,
                    [
                        add_reviewers(ts(2026, 10, 1), GROUP),
                        add_reviewers(ts(2026, 10, 16), ALICE),
                    ],
                    reviewers={GROUP: "added", ALICE: "added", BOB: "accepted"},
                )
            ]
        )
        assert report.total == 1
        [review] = report.reviews
        assert review.reviewers == ("#example-reviewers", "alice")
        assert review.since == datetime(2026, 10, 1, tzinfo=UTC)
        assert {r.label: (r.waiting, r.overdue) for r in report.per_reviewer} == {
            "#example-reviewers": (1, 1),
            "alice": (1, 0),
        }

    def test_overdue_is_oldest_first(self):
        report = _report(
            [
                rev(1, created=ts(2026, 9, 20)),
                rev(2, created=ts(2026, 9, 1)),
                rev(3, created=ts(2026, 10, 19, 6)),
            ]
        )
        assert [r.revision.id for r in report.overdue] == [2, 1]

    def test_per_reviewer_is_busiest_first(self):
        report = _report(
            [
                rev(1, reviewers={BOB: "added"}),
                rev(2, reviewers={BOB: "added", ALICE: "added"}),
            ]
        )
        assert [r.label for r in report.per_reviewer] == ["bob", "alice"]

    def test_empty(self):
        report = _report([])
        assert report.total == 0
        assert report.overdue == []
        assert report.per_reviewer == []
        assert len(report.buckets) == 4


class TestStale:
    def test_stale_reviews_are_counted_apart(self):
        report = _report(
            [rev(1, created=ts(2026, 1, 5)), rev(2, created=ts(2026, 10, 1))],
            kinds={CAROL: "staff"},
        )
        assert report.total == 1
        assert [r.revision.id for r in report.reviews] == [2]
        assert report.stale.total == 1
        assert report.stale.by_author == {"staff": 1}
        assert sum(b.total for b in report.buckets) == 1
        assert [r.revision.id for r in report.overdue] == [2]

    def test_stale_reviews_are_not_in_per_reviewer(self):
        report = _report([rev(1, created=ts(2026, 1, 5))])
        assert report.per_reviewer == []

    def test_a_fresh_request_on_an_old_revision_is_not_stale(self):
        report = _report(
            [
                rev(
                    1,
                    [add_reviewers(ts(2026, 10, 16), ALICE)],
                    created=ts(2026, 1, 5),
                    reviewers={GROUP: "added", ALICE: "added"},
                )
            ]
        )
        assert report.stale.total == 0
        [review] = report.reviews
        assert review.reviewers == ("alice",)
        assert review.since == datetime(2026, 10, 16, tzinfo=UTC)


class TestScope:
    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            (["dom/media/A.cpp"], True),
            (["dom/media"], True),
            (["gfx/B.cpp", "dom/media/x/C.h"], True),
            (["dom/mediasource/A.cpp"], False),
            (["dom/media/webrtc/A.cpp"], False),
            (["gfx/B.cpp"], False),
            ([], False),
        ],
    )
    def test_in_scope(self, files, expected):
        assert in_scope(files, ["dom/media"], ["dom/media/webrtc"]) is expected

    def test_direct_only(self):
        assert direct_only(rev(1, reviewers={ALICE: "added"}), WATCHED, GROUP)
        assert not direct_only(
            rev(1, reviewers={GROUP: "added", ALICE: "added"}), WATCHED, GROUP
        )


class TestRows:
    def test_rows_cover_every_review_with_its_state(self):
        report = _report(
            [
                rev(1, created=ts(2026, 10, 19, 6)),
                rev(2, created=ts(2026, 10, 14)),
                rev(3, created=ts(2026, 10, 1)),
                rev(4, created=ts(2026, 1, 5)),
            ],
            kinds={CAROL: "staff"},
        )
        rows = {row["id"]: row for row in report_rows(report)}
        assert {i: r["state"] for i, r in rows.items()} == {
            1: "new",
            2: "waiting",
            3: "overdue",
            4: "stale",
        }
        assert rows[3] == {
            "id": 3,
            "url": "https://phabricator.services.mozilla.com/D3",
            "title": "Bug 1900000 - Change 3",
            "bug_id": 1900000,
            "author": "staff",
            "reviewers": ["#example-reviewers"],
            "waiting_since": "2026-10-01T00:00:00+00:00",
            "age_work_days": 12.5,
            "state": "overdue",
        }

    def test_stale_reviews_keep_their_details(self):
        report = _report([rev(4, created=ts(2026, 1, 5))])
        [stale] = report.stale_reviews
        assert stale.revision.id == 4
        assert stale.reviewers == ("#example-reviewers",)
