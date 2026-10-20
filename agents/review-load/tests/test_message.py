import json
from datetime import UTC, datetime

from hackbot_agents.review_load.conduit import parse_revision, revisions_url
from hackbot_agents.review_load.config import ReportSettings
from hackbot_agents.review_load.load import build_report
from hackbot_agents.review_load.message import (
    MAX_LINK_IDS,
    bucket_labels,
    build_blocks,
    build_text,
    list_link,
)
from testdata import ALICE, GROUP, OUTSIDER, revision, ts

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
GROUP_LABEL = "#example-reviewers"
WATCHED = {GROUP: GROUP_LABEL, ALICE: "alice"}


def _report(revisions, max_listed=10, kinds=None):
    return build_report(
        [parse_revision(r, []) for r in revisions],
        watched=WATCHED,
        author_kinds=kinds or {},
        now=NOW,
        settings=ReportSettings(slack_channel="#x", max_listed=max_listed),
    )


def _all_text(blocks):
    return json.dumps(blocks)


def test_bucket_labels():
    assert bucket_labels([1, 3, 7]) == ["<1", "1-3", "3-7", "7+"]
    assert bucket_labels([2]) == ["<2", "2+"]


def test_header_names_the_group_and_day():
    blocks = build_blocks(_report([]), GROUP_LABEL)
    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"]["text"] == f"Review load for {GROUP_LABEL}, Mon 2026-10-19"


def test_empty_report_is_one_line():
    blocks = build_blocks(_report([]), GROUP_LABEL)
    assert len(blocks) == 2
    assert "No reviews are waiting" in blocks[1]["text"]["text"]
    assert "No reviews are waiting" in build_text(_report([]), GROUP_LABEL)


def test_summary_counts_new_and_split():
    report = _report(
        [
            revision(1, created=ts(2026, 10, 19, 6)),
            revision(2, created=ts(2026, 10, 19, 7), author=OUTSIDER),
            revision(3, created=ts(2026, 10, 1)),
        ],
        kinds={OUTSIDER: "external"},
    )
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert "*New in the last work day:* 2 (external 1, unknown 1)" in text
    assert "*Waiting:* 3" in text


def test_table_has_a_row_per_bucket():
    report = _report([revision(1, created=ts(2026, 10, 1))])
    text = _all_text(build_blocks(report, GROUP_LABEL))
    for label in ["<1", "1-3", "3-7", "7+"]:
        assert label in text


def test_overdue_lists_links_and_truncates():
    report = _report(
        [revision(i, created=ts(2026, 9, 1) + i) for i in range(1, 5)], max_listed=2
    )
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert "<https://phabricator.services.mozilla.com/D1|D1>" in text
    assert "<https://phabricator.services.mozilla.com/D2|D2>" in text
    assert "D3|" not in text
    assert "and 2 more" in text


def test_titles_are_escaped():
    raw = revision(1, created=ts(2026, 9, 1))
    raw["fields"]["title"] = "Fix <a> & <b>"
    text = _all_text(build_blocks(_report([raw]), GROUP_LABEL))
    assert "Fix &lt;a&gt; &amp; &lt;b&gt;" in text


def test_per_reviewer_line():
    report = _report([revision(1, reviewers={GROUP: "added", ALICE: "added"})])
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert f"{GROUP_LABEL} 1 (1 overdue), alice 1 (1 overdue)" in text


def test_text_fallback_summarises():
    report = _report([revision(1, created=ts(2026, 10, 1))])
    assert build_text(report, GROUP_LABEL) == (
        f"Review load for {GROUP_LABEL}: 1 waiting, 0 new, 1 overdue"
    )


def test_stale_line():
    report = _report(
        [revision(1, created=ts(2025, 1, 6)), revision(2, created=ts(2026, 10, 1))]
    )
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert "*Stale (60+ work days, not counted above):* 1 (unknown 1)" in text
    assert "D1|" not in text


def test_revisions_url():
    assert revisions_url([7, 3]) == (
        "https://phabricator.services.mozilla.com/differential/?ids=7,3"
    )


def test_only_stale_is_not_empty():
    report = _report([revision(1, created=ts(2025, 1, 6))])
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert "No reviews are waiting" not in text
    assert "*Stale" in text


def test_list_link_caps_the_ids():
    report = _report(
        [revision(i, created=ts(2026, 9, 1)) for i in range(1, MAX_LINK_IDS + 3)]
    )
    link = list_link(report.overdue, "overdue")
    assert f"overdue (oldest {MAX_LINK_IDS} of {MAX_LINK_IDS + 2})" in link
    assert f",{MAX_LINK_IDS}|" in link
    assert f",{MAX_LINK_IDS + 1}" not in link


def test_one_line_of_lists():
    report = _report(
        [
            revision(1, created=ts(2025, 1, 6)),
            revision(2, created=ts(2026, 9, 1)),
            revision(3, created=ts(2026, 9, 2)),
            revision(4, created=ts(2026, 10, 14)),
            revision(5, created=ts(2026, 10, 19, 6)),
        ],
        max_listed=1,
    )
    [line] = [
        b["text"]["text"]
        for b in build_blocks(report, GROUP_LABEL)
        if b["text"]["text"].startswith("*Open in Phabricator:*")
    ]
    url = "https://phabricator.services.mozilla.com/differential/?ids="
    assert line == (
        f"*Open in Phabricator:* <{url}5|new (1)> | <{url}4|1-7 days (1)> | "
        f"<{url}2,3|overdue (2)> | <{url}1|stale (1)>"
    )


def test_empty_lists_are_left_out():
    report = _report([revision(1, created=ts(2025, 1, 6))])
    text = _all_text(build_blocks(report, GROUP_LABEL))
    assert "|stale (1)>" in text
    assert "new (" not in text
    assert "overdue (" not in text
