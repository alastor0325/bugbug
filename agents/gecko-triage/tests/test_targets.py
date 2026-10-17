"""Tests for the post-run check on the other bugs a run's writes name."""

import asyncio

import pytest
from hackbot_agents.gecko_triage import targets
from hackbot_agents.gecko_triage.config import TEAM_META_BUGS, TRIAGE_SCOPE
from hackbot_agents.gecko_triage.targets import (
    TARGET_FIELDS,
    Dropped,
    DupeTargetIsMeta,
    DupeTargetRestricted,
    bugs_to_read,
    drop_writes,
    dupe_of,
    fetch_bugs,
    screen_writes,
)
from hackbot_runtime.actions import ActionsRecorder

BUG = 9000001
DUPE = 9000002
REGRESSOR = 9000003
META = sorted(TEAM_META_BUGS)[0]
SCOPE = TRIAGE_SCOPE[0]


def _target(**fields):
    bug = {
        "id": DUPE,
        "groups": [],
        "product": SCOPE.product,
        "component": SCOPE.component,
        "status": "NEW",
        "resolution": "",
        "keywords": [],
        "summary": "Video stalls",
    }
    return {**bug, **fields}


def _actions(dupe_of=DUPE, meta_on=DUPE, regressed_by=None):
    changes = {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": dupe_of}
    if regressed_by:
        changes["regressed_by"] = {"add": regressed_by}
    actions = [
        {
            "action_id": "c",
            "type": "bugzilla.add_comment",
            "params": {"bug_id": BUG, "text": "x"},
        },
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {"bug_id": BUG, "changes": changes},
        },
    ]
    if meta_on is not None:
        actions.append(
            {
                "action_id": "m",
                "type": "bugzilla.update_bug",
                "params": {"bug_id": meta_on, "changes": {"blocks": {"add": [META]}}},
            }
        )
    return actions


def _regression(regressed_by, **changes):
    return [
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {
                "bug_id": BUG,
                "changes": {**changes, "regressed_by": {"add": regressed_by}},
            },
        }
    ]


def test_the_bug_to_check_is_dupe_of():
    assert dupe_of(_actions(), BUG) == DUPE
    assert dupe_of(_actions()[:1], BUG) is None


def test_one_fetch_reads_the_dupe_target_and_the_regressors():
    assert bugs_to_read(_actions(regressed_by=[REGRESSOR, DUPE]), BUG) == [
        DUPE,
        REGRESSOR,
    ]
    assert bugs_to_read(_regression([REGRESSOR, "junk"]), BUG) == [REGRESSOR]
    assert bugs_to_read(_actions()[:1], BUG) == []


def test_the_fetch_asks_for_the_fields_the_check_reads():
    for name in ("groups", "component", "product", "status", "resolution", "keywords"):
        assert name in TARGET_FIELDS.split(","), name


def test_a_public_open_team_target_keeps_the_meta_write():
    assert screen_writes(_actions(), BUG, {DUPE: _target()}) == []


@pytest.mark.parametrize(
    ("target", "why"),
    [
        (_target(status="RESOLVED", resolution="FIXED"), "closed"),
        (_target(product="Firefox", component="General"), "out of the team's scope"),
        (_target(keywords=["sec-high"]), "security"),
    ],
)
def test_the_meta_write_is_dropped_on_an_unsuitable_target(target, why):
    dropped = screen_writes(_actions(), BUG, {DUPE: target})
    assert [(d.action_id, d.field) for d in dropped] == [("m", None)]
    assert why in dropped[0].reason


@pytest.mark.parametrize(
    "target", [_target(keywords=["meta"]), _target(summary="[meta] Media tracking")]
)
def test_a_duplicate_of_a_meta_bug_fails_the_run(target):
    for actions in (_actions(), _actions(meta_on=None)):
        with pytest.raises(DupeTargetIsMeta, match=f"bug {DUPE}"):
            screen_writes(actions, BUG, {DUPE: target})


def test_a_write_on_a_bug_other_than_dupe_of_is_dropped():
    dropped = screen_writes(_actions(meta_on=DUPE + 1), BUG, {DUPE: _target()})
    assert [d.action_id for d in dropped] == ["m"]
    assert "duplicate" in dropped[0].reason


def test_a_write_on_another_bug_without_its_duplicate_change_is_dropped():
    # The model can remove the DUPLICATE change after the meta write passed its hook.
    actions = [a for a in _actions() if a["action_id"] != "u"]
    dropped = screen_writes(actions, BUG, {})
    assert [d.action_id for d in dropped] == ["m"]
    assert "duplicate" in dropped[0].reason


@pytest.mark.parametrize("found", [{DUPE: _target(groups=["core-security"])}, {}])
def test_a_restricted_or_unreadable_dupe_of_fails_the_run(found):
    with pytest.raises(DupeTargetRestricted):
        screen_writes(_actions(meta_on=None), BUG, found)


def test_a_readable_regressor_keeps_the_regressed_by_write():
    closed = _target(id=REGRESSOR, status="RESOLVED", resolution="FIXED")
    assert screen_writes(_regression([REGRESSOR]), BUG, {REGRESSOR: closed}) == []


@pytest.mark.parametrize(
    "found", [{}, {REGRESSOR: _target(id=REGRESSOR, groups=["media-core-security"])}]
)
def test_an_unreadable_or_restricted_regressor_drops_the_regressed_by_write(found):
    dropped = screen_writes(_regression([REGRESSOR]), BUG, found)
    assert [(d.action_id, d.field) for d in dropped] == [("u", "regressed_by")]
    assert f"bug {REGRESSOR}" in dropped[0].reason


RELATED = 9000004


def _related(see_also, **changes):
    return [
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {
                "bug_id": BUG,
                "changes": {**changes, "see_also": {"add": see_also}},
            },
        }
    ]


def test_one_fetch_also_reads_the_see_also_bugs():
    actions = _related([RELATED, "junk"], regressed_by={"add": [REGRESSOR]})
    assert bugs_to_read(actions, BUG) == [REGRESSOR, RELATED]


def test_a_readable_see_also_bug_keeps_the_see_also_write():
    closed = _target(id=RELATED, status="RESOLVED", resolution="FIXED")
    assert screen_writes(_related([RELATED]), BUG, {RELATED: closed}) == []


@pytest.mark.parametrize(
    "found", [{}, {RELATED: _target(id=RELATED, groups=["media-core-security"])}]
)
def test_an_unreadable_or_restricted_see_also_bug_drops_the_see_also_write(found):
    dropped = screen_writes(_related([RELATED], severity="S3"), BUG, found)
    assert [(d.action_id, d.field) for d in dropped] == [("u", "see_also")]
    assert f"bug {RELATED}" in dropped[0].reason


def test_a_meta_bug_in_see_also_drops_the_see_also_write():
    meta = _target(id=RELATED, keywords=["meta"])
    dropped = screen_writes(_related([RELATED], severity="S3"), BUG, {RELATED: meta})
    assert [(d.action_id, d.field) for d in dropped] == [("u", "see_also")]
    assert "meta bug" in dropped[0].reason


def test_a_meta_regressor_keeps_the_regressed_by_write():
    meta = _target(id=REGRESSOR, keywords=["meta"])
    assert screen_writes(_regression([REGRESSOR]), BUG, {REGRESSOR: meta}) == []


def test_each_unreadable_field_is_dropped_on_its_own():
    actions = _related([RELATED], regressed_by={"add": [REGRESSOR]})
    dropped = screen_writes(actions, BUG, {})
    assert sorted(d.field for d in dropped) == ["regressed_by", "see_also"]


def test_dropping_a_field_keeps_the_rest_of_the_change():
    recorder = ActionsRecorder()
    kept = recorder.record(
        "bugzilla.update_bug",
        {
            "bug_id": BUG,
            "changes": {"severity": "S3", "regressed_by": {"add": [REGRESSOR]}},
        },
        reasoning="r",
    )
    alone = recorder.record(
        "bugzilla.update_bug",
        {"bug_id": DUPE, "changes": {"regressed_by": {"add": [REGRESSOR]}}},
        reasoning="r",
    )
    drop_writes(
        recorder,
        [
            Dropped(kept["action_id"], "x", "regressed_by"),
            Dropped(alone["action_id"], "x", "regressed_by"),
        ],
    )
    assert [a["params"]["changes"] for a in recorder.actions] == [{"severity": "S3"}]


def test_dropping_an_action_removes_it():
    recorder = ActionsRecorder()
    action = recorder.record(
        "bugzilla.update_bug", {"bug_id": DUPE, "changes": {}}, reasoning="r"
    )
    drop_writes(recorder, [Dropped(action["action_id"], "x")])
    assert recorder.actions == []


async def test_the_fetch_never_raises(monkeypatch):
    async def fail(url, ids):
        raise OSError("connection refused")

    monkeypatch.setattr(targets, "_get_bugs", fail)
    assert await fetch_bugs({"type": "http", "url": ""}, [1]) == {}
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [1]) == {}


async def test_the_fetch_gives_up_after_the_timeout(monkeypatch):
    async def hang(url, ids):
        await asyncio.sleep(10)

    monkeypatch.setattr(targets, "_get_bugs", hang)
    monkeypatch.setattr(targets, "FETCH_TIMEOUT", 0.01)
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [1]) == {}


async def test_the_fetch_reads_the_bugs(monkeypatch):
    calls = []

    async def fake_get(url, ids):
        calls.append((url, ids))
        return [_target(), {"id": "junk"}, "junk"]

    monkeypatch.setattr(targets, "_get_bugs", fake_get)
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [DUPE, 1]) == {
        DUPE: _target()
    }
    assert calls == [("http://b/mcp", [DUPE, 1])]
