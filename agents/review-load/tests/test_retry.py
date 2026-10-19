from unittest.mock import AsyncMock, patch

import httpx
import pytest
from hackbot_agents.review_load.retry import ATTEMPTS, RetryingPhabricatorClient
from phabricator_client import PhabricatorClient, PhabricatorSettings

SETTINGS = PhabricatorSettings(url="http://broker/phabricator", api_key="a" * 32)


def _error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://broker/phabricator/api/user.search")
    return httpx.HTTPStatusError(
        "error", request=request, response=httpx.Response(status, request=request)
    )


@pytest.fixture(autouse=True)
def no_sleep():
    with patch.object(
        RetryingPhabricatorClient.conduit_call.retry, "sleep", AsyncMock()
    ):
        yield


@pytest.mark.parametrize("status", [429, 502, 503])
async def test_retries_rate_limits_and_server_errors(status):
    upstream = AsyncMock(side_effect=[_error(status), {"result": {"data": []}}])
    with patch.object(PhabricatorClient, "conduit_call", upstream):
        result = await RetryingPhabricatorClient(SETTINGS).conduit_call(
            "user.search", {}
        )
    assert result == {"result": {"data": []}}
    assert upstream.await_count == 2


async def test_does_not_retry_a_refusal():
    upstream = AsyncMock(side_effect=_error(403))
    with (
        patch.object(PhabricatorClient, "conduit_call", upstream),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await RetryingPhabricatorClient(SETTINGS).conduit_call("x.edit", {})
    assert upstream.await_count == 1


async def test_gives_up_after_the_last_attempt():
    upstream = AsyncMock(side_effect=_error(429))
    with (
        patch.object(PhabricatorClient, "conduit_call", upstream),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await RetryingPhabricatorClient(SETTINGS).conduit_call("user.search", {})
    assert upstream.await_count == ATTEMPTS
