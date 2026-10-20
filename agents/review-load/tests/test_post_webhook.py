import json

import httpx
import pytest
from hackbot_agents.review_load.post_webhook import (
    WEBHOOK_ENV,
    main,
    post,
    slack_message,
)
from hackbot_runtime.actions.slack import ACTION_TYPE

BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
ACTION = {
    "type": ACTION_TYPE,
    "params": {"channel": "#x", "text": "fallback", "blocks": BLOCKS},
}


def _summary(*actions, status="ok"):
    return {"status": status, "error": None, "actions": list(actions)}


def test_slack_message_is_the_recorded_action_without_previews():
    assert slack_message(_summary(ACTION)) == {
        "text": "fallback",
        "blocks": BLOCKS,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def test_recorded_unfurl_is_kept():
    action = {**ACTION, "params": {**ACTION["params"], "unfurl_links": True}}
    assert slack_message(_summary(action))["unfurl_links"] is True


@pytest.mark.parametrize(
    "summary",
    [
        _summary(),
        _summary(ACTION, ACTION),
        _summary(ACTION, status="error"),
        _summary({"type": "email.send", "params": {}}),
    ],
)
def test_needs_one_message_from_a_good_run(summary):
    with pytest.raises(ValueError):
        slack_message(summary)


def test_post_sends_the_json():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    post("https://hooks.example/x", {"text": "t"}, httpx.MockTransport(handler))
    assert seen["body"] == {"text": "t"}


def test_post_raises_on_a_refusal():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(400, text="invalid_blocks")
    )
    with pytest.raises(httpx.HTTPStatusError):
        post("https://hooks.example/x", {"text": "t"}, transport)


def test_main_needs_the_webhook(tmp_path, monkeypatch):
    monkeypatch.delenv(WEBHOOK_ENV, raising=False)
    with pytest.raises(SystemExit, match=WEBHOOK_ENV):
        main([str(tmp_path)])
