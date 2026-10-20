"""The Slack message: Block Kit blocks and a plain-text fallback. Pure."""

from .conduit import revision_url, revisions_url
from .load import AuthorKind, Bucket, Report, WaitingReview
from .workdays import bucket_index

AUTHOR_LABELS: dict[AuthorKind, str] = {
    "staff": "Mozilla",
    "external": "external",
    "unknown": "unknown",
}
_MAX_TITLE = 80


# Keeps the line of list links well under Slack's 3000-character section limit.
MAX_LINK_IDS = 60


def list_link(reviews: list[WaitingReview], label: str) -> str:
    """A link to one Phabricator search with the oldest ``MAX_LINK_IDS`` of ``reviews``."""
    url = revisions_url([r.revision.id for r in reviews[:MAX_LINK_IDS]])
    count = len(reviews)
    if count > MAX_LINK_IDS:
        return f"<{url}|{label} (oldest {MAX_LINK_IDS} of {count})>"
    return f"<{url}|{label} ({count})>"


def _lists(report: Report) -> str:
    edges = report.settings.age_buckets_work_days
    overdue_days = report.settings.overdue_work_days
    lists = [
        ("new", [r for r in report.reviews if bucket_index(r.age, edges) == 0]),
        (
            f"{edges[0]}-{overdue_days} days",
            [r for r in report.reviews if edges[0] <= r.age < overdue_days],
        ),
        ("overdue", report.overdue),
        ("stale", report.stale_reviews),
    ]
    links = [list_link(reviews, label) for label, reviews in lists if reviews]
    return "*Open in Phabricator:* " + " | ".join(links)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bucket_labels(edges: list[int]) -> list[str]:
    labels = [f"<{edges[0]}"]
    labels += [f"{a}-{b}" for a, b in zip(edges, edges[1:])]
    labels.append(f"{edges[-1]}+")
    return labels


def _split(bucket: Bucket) -> str:
    parts = [
        f"{label} {bucket.by_author[kind]}"
        for kind, label in AUTHOR_LABELS.items()
        if bucket.by_author.get(kind)
    ]
    return f" ({', '.join(parts)})" if parts else ""


def _table(report: Report) -> str:
    kinds = [
        kind
        for kind in AUTHOR_LABELS
        if kind != "unknown" or any(b.by_author.get(kind) for b in report.buckets)
    ]
    labels = bucket_labels(report.settings.age_buckets_work_days)
    head = ["work days", "total", *(AUTHOR_LABELS[k] for k in kinds)]
    rows = [
        [label, str(b.total), *(str(b.by_author.get(k, 0)) for k in kinds)]
        for label, b in zip(labels, report.buckets)
    ]
    widths = [max(len(row[i]) for row in [head, *rows]) for i in range(len(head))]
    lines = [
        f"{row[0]:<{widths[0]}}"
        + "".join(f"  {cell:>{w}}" for cell, w in zip(row[1:], widths[1:]))
        for row in [head, *rows]
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def _per_reviewer(report: Report) -> str:
    parts = [
        f"{r.label} {r.waiting}" + (f" ({r.overdue} overdue)" if r.overdue else "")
        for r in report.per_reviewer
    ]
    return "*Per reviewer:* " + ", ".join(parts)


def _overdue_line(review: WaitingReview) -> str:
    rev = review.revision
    title = (
        rev.title
        if len(rev.title) <= _MAX_TITLE
        else rev.title[: _MAX_TITLE - 3] + "..."
    )
    link = f"<{revision_url(rev.id)}|D{rev.id}>"
    return (
        f"{link} {int(review.age)} work days, {AUTHOR_LABELS[review.author]}, "
        f"{_escape(title)} ({', '.join(review.reviewers)})"
    )


def _overdue(report: Report) -> str:
    overdue = report.overdue
    limit = report.settings.max_listed
    lines = [_overdue_line(r) for r in overdue[:limit]]
    if len(overdue) > limit:
        lines.append(f"and {len(overdue) - limit} more")
    return f"*Overdue ({report.settings.overdue_work_days}+ work days):*\n" + "\n".join(
        lines
    )


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def build_blocks(report: Report, group_label: str) -> list[dict]:
    day = report.now.strftime("%a %Y-%m-%d")
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Review load for {group_label}, {day}",
            },
        }
    ]
    lines = []
    if report.total:
        lines.append(
            f"*New in the last work day:* {report.new.total}{_split(report.new)}"
        )
        lines.append(f"*Waiting:* {report.total}")
    if report.stale.total:
        lines.append(
            f"*Stale ({report.settings.stale_work_days}+ work days, not counted above):* "
            f"{report.stale.total}{_split(report.stale)}"
        )
    blocks.append(
        _section("\n".join(lines) or f"No reviews are waiting for {group_label}.")
    )
    if not report.total:
        if report.stale.total:
            blocks.append(_section(_lists(report)))
        return blocks
    blocks.append(_section(_table(report)))
    blocks.append(_section(_lists(report)))
    blocks.append(_section(_per_reviewer(report)))
    if report.overdue:
        blocks.append(_section(_overdue(report)))
    return blocks


def build_text(report: Report, group_label: str) -> str:
    if not report.total and not report.stale.total:
        return f"Review load for {group_label}: No reviews are waiting."
    return (
        f"Review load for {group_label}: {report.total} waiting, "
        f"{report.new.total} new, {len(report.overdue)} overdue"
    )
