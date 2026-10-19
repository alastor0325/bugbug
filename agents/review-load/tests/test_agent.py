from datetime import UTC, datetime

from hackbot_agents.review_load.agent import run_review_load
from hackbot_agents.review_load.conduit import parse_revision
from hackbot_agents.review_load.config import ReviewLoadConfig
from hackbot_runtime import ActionsRecorder
from testdata import GROUP, revision, ts

CONFIG = ReviewLoadConfig.model_validate(
    {
        "name": "example",
        "review_group": "example-reviewers",
        "report": {"slack_channel": "#example"},
    }
)
MONDAY = datetime(2026, 10, 19, 12, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 17, 12, tzinfo=UTC)


class Published(dict):
    def __call__(self, key, payload):
        self[key] = payload
        return key


async def _inputs():
    return (
        [parse_revision(revision(1, created=ts(2026, 10, 1)), [])],
        {GROUP: "#example-reviewers"},
        {},
    )


async def _run(now, dry_run=False):
    recorder, published = ActionsRecorder(), Published()
    result = await run_review_load(
        CONFIG,
        collect=_inputs,
        now=now,
        recorder=recorder,
        publish_json=published,
        dry_run=dry_run,
    )
    return result, recorder.actions, published


async def test_records_one_slack_message():
    result, actions, published = await _run(MONDAY)
    [action] = actions
    assert action["type"] == "slack.post_message"
    assert action["params"]["channel"] == "#example"
    assert action["params"]["blocks"] == published["message.json"]["blocks"]
    assert "unfurl_links" not in action["params"]
    assert result.waiting == 1
    assert result.overdue == 1
    assert result.posted is True
    assert result.skipped is None


async def test_dry_run_records_nothing():
    result, actions, published = await _run(MONDAY, dry_run=True)
    assert actions == []
    assert "message.json" in published
    assert result.posted is False


async def test_weekend_posts_nothing():
    result, actions, published = await _run(SATURDAY)
    assert actions == []
    assert published == {}
    assert result.skipped == "weekend"
