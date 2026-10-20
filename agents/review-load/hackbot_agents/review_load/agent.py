"""One review-load run: collect, count, build the message, record it."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from hackbot_runtime import ActionsRecorder, HackbotAgentResult
from hackbot_runtime.actions.slack import record_message

from .collect import Inputs
from .config import ReviewLoadConfig
from .load import build_report, report_rows
from .message import build_blocks, build_text
from .workdays import is_weekend

logger = logging.getLogger(__name__)

MESSAGE_ARTIFACT = "message.json"
REVIEWS_ARTIFACT = "reviews.json"


class ReviewLoadResult(HackbotAgentResult):
    config: str
    skipped: str | None = None
    waiting: int = 0
    new: int = 0
    overdue: int = 0
    posted: bool = False


async def run_review_load(
    config: ReviewLoadConfig,
    *,
    collect: Callable[[], Awaitable[Inputs]],
    now: datetime,
    recorder: ActionsRecorder,
    publish_json: Callable[[str, dict], str],
    dry_run: bool,
) -> ReviewLoadResult:
    if is_weekend(now):
        logger.info("Saturday or Sunday: no recap")
        return ReviewLoadResult(num_turns=0, config=config.name, skipped="weekend")

    revisions, watched, kinds = await collect()
    report = build_report(
        revisions, watched=watched, author_kinds=kinds, now=now, settings=config.report
    )
    message = {
        "channel": config.report.slack_channel,
        "text": build_text(report, config.group_label),
        "blocks": build_blocks(report, config.group_label),
    }
    publish_json(MESSAGE_ARTIFACT, message)
    publish_json(REVIEWS_ARTIFACT, {"reviews": report_rows(report)})
    logger.info("%s", message["text"])

    if dry_run:
        logger.info("Dry run: no Slack message recorded")
    else:
        record_message(recorder, message["channel"], message["text"], message["blocks"])

    return ReviewLoadResult(
        num_turns=0,
        config=config.name,
        waiting=report.total,
        new=report.new.total,
        overdue=len(report.overdue),
        posted=not dry_run,
    )
