"""The Slack message: Block Kit blocks and a plain-text fallback. Pure."""

from collections.abc import Sequence

from .conduit import revisions_url
from .load import AuthorKind, Report, WaitingReview, author_split

AUTHOR_LABELS: dict[AuthorKind, str] = {
    "staff": "Mozilla",
    "external": "external",
    "unknown": "unknown",
}
# Keeps each link, and so each box, well under Slack's 2000-character field limit.
MAX_LINK_IDS = 60
# Slack's limit for a section's text.
MAX_SECTION_CHARS = 3000


def list_link(revision_ids: Sequence[int], label: str) -> str:
    """``label`` linked to one Phabricator search with the first ``MAX_LINK_IDS`` revisions."""
    return f"<{revisions_url(list(revision_ids[:MAX_LINK_IDS]))}|{label}>"


def _ids(reviews: list[WaitingReview]) -> list[int]:
    return [r.revision.id for r in reviews]


def _split(reviews: list[WaitingReview]) -> str:
    split = author_split(reviews)
    return ", ".join(
        f"{label} {split[kind]}"
        for kind, label in AUTHOR_LABELS.items()
        if kind in split
    )


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _box(label: str, reviews: list[WaitingReview], *, bold: bool = False) -> dict:
    title = list_link(_ids(reviews), label) if reviews else label
    if bold:
        title = f"*{title}*"
    count = f"*{len(reviews)}*"
    if reviews:
        count += f"  ·  {_split(reviews)}"
    return {"type": "mrkdwn", "text": f"{title}\n{count}"}


def _boxes(report: Report) -> dict:
    s = report.settings
    return {
        "type": "section",
        "fields": [
            _box(
                "New today"
                if s.new_work_days == 1
                else f"Under {s.new_work_days} days",
                report.new,
            ),
            _box(f"{s.new_work_days}-{s.overdue_work_days} days", report.middle),
            _box("Overdue", report.overdue, bold=True),
            _box("Stale", report.stale_reviews),
        ],
    }


def _by_reviewer(report: Report) -> list[dict]:
    """``By reviewer`` lines, split into as many sections as Slack's limit needs."""
    lines = [
        f"{list_link(r.revision_ids, r.label)}  {r.waiting} waiting"
        + (f", *{r.overdue} overdue*" if r.overdue else "")
        for r in report.per_reviewer
    ]
    sections, current = [], "*By reviewer*"
    for line in lines:
        if len(current) + 1 + len(line) > MAX_SECTION_CHARS:
            sections.append(current)
            current = line
        else:
            current += "\n" + line
    sections.append(current)
    return [_section(text) for text in sections]


def _context(report: Report) -> dict:
    s = report.settings
    text = (
        "Days are work days, Mon to Fri. "
        f"Overdue: waiting {s.overdue_work_days}+ work days. "
        f"Stale: {s.stale_work_days}+ work days, not counted in waiting. "
    )
    if report.reviews:
        text += f"Waiting: {_split(report.reviews)}. "
    text += "Open a label or name to see the list in Phabricator."
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def build_blocks(report: Report, group_label: str) -> list[dict]:
    header = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"Review load: {group_label}, {report.now.strftime('%a %d %b')}",
        },
    }
    stale = len(report.stale_reviews)
    if not report.total:
        headline = "*No reviews waiting.*"
        if stale:
            headline += (
                f"  ·  {list_link(_ids(report.stale_reviews), f'{stale} stale')}"
            )
        return [header, _section(headline), _context(report)]

    headline = f"*{len(report.overdue)} overdue* of {report.total} waiting"
    if stale:
        headline += f"  ·  {stale} stale"
    return [
        header,
        _section(headline),
        _boxes(report),
        {"type": "divider"},
        *_by_reviewer(report),
        _context(report),
    ]


def build_text(report: Report, group_label: str) -> str:
    if not report.total:
        return f"Review load for {group_label}: no reviews waiting"
    return (
        f"Review load for {group_label}: {report.total} waiting, "
        f"{len(report.overdue)} overdue, {len(report.new)} new, "
        f"{len(report.stale_reviews)} stale"
    )
