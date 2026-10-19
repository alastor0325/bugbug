"""A Phabricator client that retries rate limits and server errors.

The broker answers 502 when Phabricator fails, including when it rate limits
(429), so both are retried.
"""

import httpx
import tenacity
from phabricator_client import PhabricatorClient

ATTEMPTS = 10
# One run a day can wait minutes for a rate limit to lift.
MAX_DELAY_SECONDS = 300


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


class RetryingPhabricatorClient(PhabricatorClient):
    @tenacity.retry(
        stop=tenacity.stop_after_attempt(ATTEMPTS)
        | tenacity.stop_after_delay(MAX_DELAY_SECONDS),
        wait=tenacity.wait_random_exponential(multiplier=2, max=60),
        retry=tenacity.retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def conduit_call(self, method: str, params: dict) -> dict:
        return await super().conduit_call(method, params)
