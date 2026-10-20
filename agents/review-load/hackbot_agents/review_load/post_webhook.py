"""Post a run's recorded Slack message to an Incoming Webhook.

For the local test period only, before the agent runs on Hackbot. Remove it before
the upstream pull request. Usage:

    REVIEW_LOAD_SLACK_WEBHOOK=https://hooks.slack.com/... \
        python -m hackbot_agents.review_load.post_webhook <run artifacts dir>
"""

import json
import os
import sys
from pathlib import Path

import httpx
from hackbot_runtime.actions.slack import ACTION_TYPE

WEBHOOK_ENV = "REVIEW_LOAD_SLACK_WEBHOOK"


def slack_message(summary: dict) -> dict:
    """The webhook payload for the one Slack message a good run recorded."""
    if summary.get("status") != "ok":
        raise ValueError(f"The run did not succeed: {summary.get('error')}")
    messages = [a for a in summary.get("actions") or [] if a["type"] == ACTION_TYPE]
    if len(messages) != 1:
        raise ValueError(f"Expected one {ACTION_TYPE} action, found {len(messages)}")
    params = messages[0]["params"]
    return {
        "text": params["text"],
        "blocks": params.get("blocks") or [],
        "unfurl_links": params.get("unfurl_links", False),
        "unfurl_media": params.get("unfurl_media", False),
    }


def post(
    webhook: str, payload: dict, transport: httpx.BaseTransport | None = None
) -> None:
    with httpx.Client(transport=transport, timeout=30) as client:
        client.post(webhook, json=payload).raise_for_status()


def main(argv: list[str]) -> None:
    webhook = os.environ.get(WEBHOOK_ENV)
    if not webhook:
        raise SystemExit(f"Set {WEBHOOK_ENV} to the Incoming Webhook URL")
    if len(argv) != 1:
        raise SystemExit(__doc__)
    summary = json.loads((Path(argv[0]) / "summary.json").read_text())
    post(webhook, slack_message(summary))
    print("Posted")


if __name__ == "__main__":
    main(sys.argv[1:])
