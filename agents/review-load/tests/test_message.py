import json
from datetime import UTC, datetime

from hackbot_agents.review_load.conduit import parse_revision, revisions_url
from hackbot_agents.review_load.config import ReportSettings
from hackbot_agents.review_load.load import build_report
from hackbot_agents.review_load.message import (
    MAX_LINK_IDS,
    MAX_SECTION_CHARS,
    build_blocks,
    build_text,
    list_link,
)
from testdata import ALICE, BOB, GROUP, OUTSIDER, revision, ts

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
GROUP_LABEL = "#example-reviewers"
WATCHED = {GROUP: GROUP_LABEL, ALICE: "alice", BOB: "bob"}
URL = "https://phabricator.services.mozilla.com/differential/?ids="


def _report(revisions, kinds=None):
    return build_report(
        [parse_revision(r, []) for r in revisions],
        watched=WATCHED,
        author_kinds=kinds or {},
        now=NOW,
        settings=ReportSettings(slack_channel="#x"),
    )


def _full():
    return _report(
        [
            revision(1, created=ts(2025, 1, 6)),
            revision(
                2, created=ts(2026, 9, 1), reviewers={GROUP: "added", ALICE: "added"}
            ),
            revision(3, created=ts(2026, 9, 2), author=OUTSIDER),
            revision(4, created=ts(2026, 10, 14), reviewers={BOB: "added"}),
            revision(5, created=ts(2026, 10, 19, 6)),
        ],
        kinds={OUTSIDER: "external"},
    )


def _by_type(blocks, kind):
    return [b for b in blocks if b["type"] == kind]


def test_revisions_url():
    assert revisions_url([7, 3]) == f"{URL}7,3"


def test_list_link_caps_the_ids():
    report = _report(
        [revision(i, created=ts(2026, 9, 1)) for i in range(1, MAX_LINK_IDS + 3)]
    )
    link = list_link([r.revision.id for r in report.overdue], "Overdue")
    assert link.startswith(f"<{URL}1,2,")
    assert f",{MAX_LINK_IDS}|Overdue>" in link
    assert f",{MAX_LINK_IDS + 1}" not in link


def test_layout():
    blocks = build_blocks(_full(), GROUP_LABEL)
    assert [b["type"] for b in blocks] == [
        "header",
        "section",
        "section",
        "divider",
        "section",
        "context",
    ]


def test_header():
    [header] = _by_type(build_blocks(_full(), GROUP_LABEL), "header")
    assert header["text"] == {
        "type": "plain_text",
        "text": f"Review load: {GROUP_LABEL}, Mon 19 Oct",
    }


def test_headline():
    headline = build_blocks(_full(), GROUP_LABEL)[1]["text"]["text"]
    assert headline == "*2 overdue* of 4 waiting  ·  1 stale"


def test_boxes():
    fields = build_blocks(_full(), GROUP_LABEL)[2]["fields"]
    assert [f["text"] for f in fields] == [
        f"<{URL}5|New today>\n*1*  ·  unknown 1",
        f"<{URL}4|1-7 days>\n*1*  ·  unknown 1",
        f"*<{URL}2,3|Overdue>*\n*2*  ·  external 1, unknown 1",
        f"<{URL}1|Stale>\n*1*  ·  unknown 1",
    ]


def test_new_label_follows_the_config():
    report = build_report(
        [parse_revision(revision(1, created=ts(2026, 10, 1)), [])],
        watched=WATCHED,
        author_kinds={},
        now=NOW,
        settings=ReportSettings(slack_channel="#x", new_work_days=2),
    )
    fields = build_blocks(report, GROUP_LABEL)[2]["fields"]
    assert fields[0]["text"].startswith("Under 2 days\n")
    assert fields[1]["text"].startswith("2-7 days\n")


def test_an_empty_box_has_no_link():
    report = _report([revision(1, created=ts(2026, 10, 1))])
    fields = build_blocks(report, GROUP_LABEL)[2]["fields"]
    assert fields[0]["text"] == "New today\n*0*"
    assert fields[3]["text"] == "Stale\n*0*"


def test_by_reviewer():
    text = build_blocks(_full(), GROUP_LABEL)[4]["text"]["text"]
    assert text == (
        "*By reviewer*\n"
        f"<{URL}2,3,5|{GROUP_LABEL}>  3 waiting, *2 overdue*\n"
        f"<{URL}2|alice>  1 waiting, *1 overdue*\n"
        f"<{URL}4|bob>  1 waiting"
    )


def test_by_reviewer_splits_long_sections():
    many = {GROUP: GROUP_LABEL} | {f"PHID-USER-{i:020d}": f"user{i}" for i in range(40)}
    revisions = [
        revision(n, created=ts(2026, 9, 1), reviewers={p: "added" for p in many})
        for n in range(1000000, 1000000 + MAX_LINK_IDS)
    ]
    report = build_report(
        [parse_revision(r, []) for r in revisions],
        watched=many,
        author_kinds={},
        now=NOW,
        settings=ReportSettings(slack_channel="#x"),
    )
    blocks = build_blocks(report, GROUP_LABEL)
    sections = [b["text"]["text"] for b in blocks if "text" in b][2:]
    assert len(sections) > 1
    assert all(len(s) <= MAX_SECTION_CHARS for s in sections)
    assert sections[0].startswith("*By reviewer*\n")
    assert sum(s.count("  60 waiting") for s in sections) == len(many)


def test_context():
    [context] = _by_type(build_blocks(_full(), GROUP_LABEL), "context")
    assert context["elements"][0]["text"] == (
        "Days are work days, Mon to Fri. Overdue: waiting 7+ work days. "
        "Stale: 60+ work days, not counted in waiting. "
        "Waiting: external 1, unknown 3. "
        "Open a label or name to see the list in Phabricator."
    )


def test_quiet_day():
    blocks = build_blocks(_report([]), GROUP_LABEL)
    assert [b["type"] for b in blocks] == ["header", "section", "context"]
    assert blocks[1]["text"]["text"] == "*No reviews waiting.*"


def test_quiet_day_with_stale():
    report = _report([revision(1, created=ts(2025, 1, 6))])
    blocks = build_blocks(report, GROUP_LABEL)
    assert blocks[1]["text"]["text"] == f"*No reviews waiting.*  ·  <{URL}1|1 stale>"


def test_titles_never_reach_the_message():
    raw = revision(1, created=ts(2026, 9, 1))
    raw["fields"]["title"] = "Fix <a> & <b>"
    assert "Fix" not in json.dumps(build_blocks(_report([raw]), GROUP_LABEL))


def test_text_fallback():
    assert build_text(_full(), GROUP_LABEL) == (
        f"Review load for {GROUP_LABEL}: 4 waiting, 2 overdue, 1 new, 1 stale"
    )
    assert build_text(_report([]), GROUP_LABEL) == (
        f"Review load for {GROUP_LABEL}: no reviews waiting"
    )
