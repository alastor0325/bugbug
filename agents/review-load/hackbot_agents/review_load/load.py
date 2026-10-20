"""Count the waiting reviews. Pure: no network, the clock is an argument."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Literal

from .conduit import Revision, revision_url
from .config import AuthorSettings, ReportSettings
from .workdays import work_days_between

AuthorKind = Literal["staff", "external", "unknown"]

# A reviewer in one of these states has not acted on the current diff.
WAITING_STATUSES = frozenset({"added", "blocking"})
# Transactions that put a revision back in front of its reviewers.
RESTART_TYPES = frozenset({"request-review", "reopen", "reclaim", "update"})


@dataclass(frozen=True)
class WaitingReview:
    revision: Revision
    # Labels of the watched reviewers it waits on, in watched order.
    reviewers: tuple[str, ...]
    since: datetime
    age: float
    author: AuthorKind


@dataclass(frozen=True)
class ReviewerLoad:
    label: str
    overdue: int
    # The revisions waiting on this reviewer, oldest request first.
    revision_ids: tuple[int, ...]

    @property
    def waiting(self) -> int:
        return len(self.revision_ids)


@dataclass(frozen=True)
class Report:
    now: datetime
    settings: ReportSettings
    # Oldest first.
    reviews: list[WaitingReview]
    per_reviewer: list[ReviewerLoad]
    # Oldest first.
    stale_reviews: list[WaitingReview]

    @property
    def total(self) -> int:
        return len(self.reviews)

    def _aged(self, low: float, high: float) -> list[WaitingReview]:
        return [r for r in self.reviews if low <= r.age < high]

    @cached_property
    def new(self) -> list[WaitingReview]:
        return self._aged(0, self.settings.new_work_days)

    @cached_property
    def middle(self) -> list[WaitingReview]:
        s = self.settings
        return self._aged(s.new_work_days, s.overdue_work_days)

    @cached_property
    def overdue(self) -> list[WaitingReview]:
        return self._aged(self.settings.overdue_work_days, float("inf"))


def author_split(reviews: list[WaitingReview]) -> dict[AuthorKind, int]:
    return dict(Counter(r.author for r in reviews))


def waiting_since(revision: Revision, reviewer_phid: str) -> datetime:
    """When ``reviewer_phid`` was last asked: the later of the add and the last return to review."""
    since = revision.created
    for txn in revision.transactions:
        if txn.type in RESTART_TYPES or reviewer_phid in txn.added_reviewers:
            since = max(since, txn.created)
    return since


def classify_author(
    handle: str | None, email: str | None, authors: AuthorSettings
) -> AuthorKind:
    if handle in authors.staff_accounts:
        return "staff"
    if handle in authors.external_accounts:
        return "external"
    if not email:
        return "unknown"
    domain = "@" + email.rsplit("@", 1)[-1].lower()
    return "staff" if domain in authors.staff_email_domains else "external"


def pending_reviewers(revision: Revision, watched: dict[str, str]) -> list[str]:
    """The watched reviewers that ``revision`` waits on, in watched order."""
    if revision.status != "needs-review" or revision.is_draft:
        return []
    return [
        phid
        for phid in watched
        if revision.reviewers.get(phid) in WAITING_STATUSES
        and phid != revision.author_phid
    ]


def direct_only(revision: Revision, watched: dict[str, str], group_phid: str) -> bool:
    """True if ``revision`` waits on watched members but not on the group."""
    pending = pending_reviewers(revision, watched)
    return bool(pending) and group_phid not in pending


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + "/")


def in_scope(files: list[str], paths: list[str], excludes: list[str]) -> bool:
    """True if one of ``files`` is under ``paths`` and not under ``excludes``."""
    return any(
        any(_under(f, p) for p in paths) and not any(_under(f, e) for e in excludes)
        for f in files
    )


def build_report(
    revisions: list[Revision],
    *,
    watched: dict[str, str],
    author_kinds: dict[str, AuthorKind],
    now: datetime,
    settings: ReportSettings,
) -> Report:
    """``watched`` maps a reviewer PHID (the group or a member) to its label."""
    reviews = []
    # Label -> (waiting since, revision id, age) for each waiting review.
    waiting: defaultdict[str, list[tuple[datetime, int, float]]] = defaultdict(list)
    stale_reviews = []
    for revision in revisions:
        pending = pending_reviewers(revision, watched)
        if not pending:
            continue
        since_by_phid = {phid: waiting_since(revision, phid) for phid in pending}
        age_by_phid = {p: work_days_between(t, now) for p, t in since_by_phid.items()}
        fresh = [p for p in pending if age_by_phid[p] < settings.stale_work_days]
        for phid in fresh:
            waiting[watched[phid]].append(
                (since_by_phid[phid], revision.id, age_by_phid[phid])
            )
        oldest = max(fresh or pending, key=age_by_phid.get)
        (reviews if fresh else stale_reviews).append(
            WaitingReview(
                revision=revision,
                reviewers=tuple(watched[p] for p in fresh or pending),
                since=since_by_phid[oldest],
                age=age_by_phid[oldest],
                author=author_kinds.get(revision.author_phid, "unknown"),
            )
        )
    reviews.sort(key=lambda r: (r.since, r.revision.id))
    stale_reviews.sort(key=lambda r: (r.since, r.revision.id))

    per_reviewer = [
        ReviewerLoad(
            label,
            overdue=sum(age >= settings.overdue_work_days for *_, age in items),
            revision_ids=tuple(rev_id for _, rev_id, _ in sorted(items)),
        )
        for label, items in waiting.items()
    ]
    per_reviewer.sort(key=lambda r: (-r.waiting, -r.overdue, r.label))
    return Report(now, settings, reviews, per_reviewer, stale_reviews)


def report_rows(report: Report) -> list[dict]:
    """Every waiting and stale review as plain data, for checking a run by hand."""
    states = {id(r): "new" for r in report.new}
    states |= {id(r): "overdue" for r in report.overdue}

    def row(review: WaitingReview, state: str) -> dict:
        return {
            "id": review.revision.id,
            "url": revision_url(review.revision.id),
            "title": review.revision.title,
            "bug_id": review.revision.bug_id,
            "author": review.author,
            "reviewers": list(review.reviewers),
            "waiting_since": review.since.isoformat(),
            "age_work_days": round(review.age, 1),
            "state": state,
        }

    return [row(r, states.get(id(r), "waiting")) for r in report.reviews] + [
        row(r, "stale") for r in report.stale_reviews
    ]
