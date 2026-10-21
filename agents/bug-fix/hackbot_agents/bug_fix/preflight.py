"""Read the bug's product and component before the run, to pick its team."""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# `id` is required: `get_bugs` diffs requested against returned ids.
BUG_FIELDS = "id,product,component"
TIMEOUT_SECONDS = 60


async def _get_bug(url: str, bug: int) -> dict:
    async with (
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        res = await session.call_tool(
            "get_bugs", {"ids": [bug], "include_fields": BUG_FIELDS}
        )
        if res.isError:
            raise RuntimeError("".join(getattr(c, "text", "") for c in res.content))
        bugs = json.loads(res.content[0].text).get("bugs") or []
        return bugs[0] if bugs else {}


async def fetch_bug(url: str, bug: int) -> dict:
    """The bug's :data:`BUG_FIELDS`, read through the Bugzilla broker at ``url``.

    Never raises: ``{}`` on a broker failure, a timeout, or a bug this key cannot
    read. The run then has no team.
    """
    try:
        return await asyncio.wait_for(_get_bug(url, bug), TIMEOUT_SECONDS)
    except Exception as e:  # every failure falls back to no team
        print(
            f"[bug_fix] team lookup failed ({type(e).__name__}: {e}); "
            "running without a team",
            file=sys.stderr,
        )
        return {}
