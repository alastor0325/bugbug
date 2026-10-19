"""Count the waiting reviews. Pure: no network, the clock is an argument."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Literal

from .conduit import Revision
from .config import AuthorSettings, ReportSettings
from .workdays import bucket_index, work_days_between

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
class Bucket:
    by_author: dict[AuthorKind, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.by_author.values())


@dataclass(frozen=True)
class ReviewerLoad:
    label: str
    waiting: int
    overdue: int


@dataclass(frozen=True)
class Report:
    now: datetime
    settings: ReportSettings
    # Oldest first.
    reviews: list[WaitingReview]
    buckets: list[Bucket]
    per_reviewer: list[ReviewerLoad]

    @property
    def total(self) -> int:
        return len(self.reviews)

    @property
    def new(self) -> Bucket:
        return self.buckets[0]

    @cached_property
    def overdue(self) -> list[WaitingReview]:
        return [r for r in self.reviews if r.age >= self.settings.overdue_work_days]


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


def build_report(
    revisions: list[Revision],
    *,
    watched: dict[str, str],
    author_kinds: dict[str, AuthorKind],
    now: datetime,
    settings: ReportSettings,
) -> Report:
    """``watched`` maps a reviewer PHID (the group or a member) to its label."""
    edges = settings.age_buckets_work_days
    reviews = []
    waiting: Counter[str] = Counter()
    overdue: Counter[str] = Counter()
    for revision in revisions:
        pending = pending_reviewers(revision, watched)
        if not pending:
            continue
        since_by_phid = {phid: waiting_since(revision, phid) for phid in pending}
        for phid, since in since_by_phid.items():
            waiting[watched[phid]] += 1
            if work_days_between(since, now) >= settings.overdue_work_days:
                overdue[watched[phid]] += 1
        since = min(since_by_phid.values())
        reviews.append(
            WaitingReview(
                revision=revision,
                reviewers=tuple(watched[p] for p in pending),
                since=since,
                age=work_days_between(since, now),
                author=author_kinds.get(revision.author_phid, "unknown"),
            )
        )
    reviews.sort(key=lambda r: (r.since, r.revision.id))

    counts = [Counter() for _ in range(len(edges) + 1)]
    for review in reviews:
        counts[bucket_index(review.age, edges)][review.author] += 1
    buckets = [Bucket(dict(c)) for c in counts]

    per_reviewer = [
        ReviewerLoad(label, n, overdue[label])
        for label, n in sorted(waiting.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return Report(now, settings, reviews, buckets, per_reviewer)
