from datetime import UTC, datetime
from functools import partial

from hackbot_runtime import HackbotContext, run_async
from phabricator_client import PhabricatorSettings
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agent import ReviewLoadResult, run_review_load
from .collect import Collector, broker_email_lookup, collect_inputs
from .config import load_config
from .retry import RetryingPhabricatorClient

# The broker swaps in the real key; this only satisfies the client's length check.
PROXY_API_TOKEN = "hackbot-broker-proxy-placeholder"


class AgentInputs(BaseSettings):
    config: str
    dry_run: bool = False
    broker_url: str = "http://127.0.0.1:8765"

    # Compose passes unset per-run inputs as empty strings; treat those as absent.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)


async def main(ctx: HackbotContext) -> ReviewLoadResult:
    inputs = AgentInputs()
    broker_url = inputs.broker_url.rstrip("/")
    phabricator = RetryingPhabricatorClient(
        PhabricatorSettings(url=f"{broker_url}/phabricator", api_key=PROXY_API_TOKEN)
    )
    config = load_config(inputs.config)
    collector = Collector(phabricator, broker_email_lookup(broker_url))
    return await run_review_load(
        config,
        collect=partial(collect_inputs, config, collector),
        now=datetime.now(UTC),
        recorder=ctx.actions,
        publish_json=ctx.publish_json,
        dry_run=inputs.dry_run,
    )


run_async(main)
