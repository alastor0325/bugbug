"""Phabricator and Bugzilla read broker.

Sidecar container that holds the API keys. The agent, in a sibling container of
the same Cloud Run Job task, reaches it at ``127.0.0.1:<port>``:

- ``/phabricator/api``: the read-only Conduit proxy (``phabricator_proxy``), with
  the extra read methods this agent needs.
- ``/bugzilla/emails?ids=...``: the login email of each Bugzilla user id, and
  nothing else from the user record.
"""

import logging

import httpx
import uvicorn
from phabricator_client import PhabricatorClient, PhabricatorSettings
from phabricator_proxy import READ_ONLY_METHODS
from phabricator_proxy import create_app as conduit_proxy
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

log = logging.getLogger("review-load-broker")

ALLOWED_METHODS = READ_ONLY_METHODS | {
    "project.search",
    "user.search",
    "transaction.search",
    "bugzilla.account.search",
    "differential.getcommitpaths",
}
MAX_IDS = 500


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    phabricator: PhabricatorSettings
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(
        extra="ignore",
        env_nested_delimiter="_",
        env_nested_max_split=1,
    )


def build_app(
    inputs: BrokerInputs, *, bugzilla_transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    phabricator = PhabricatorClient(inputs.phabricator)
    bugzilla_url = inputs.bugzilla_api_url.rstrip("/")

    async def emails(request: Request) -> JSONResponse:
        raw = request.query_params.getlist("ids")
        if not raw or len(raw) > MAX_IDS or not all(i.isdigit() for i in raw):
            return JSONResponse({"error": f"ids: 1 to {MAX_IDS} integers"}, 400)
        async with httpx.AsyncClient(
            transport=bugzilla_transport, timeout=60
        ) as client:
            try:
                response = await client.get(
                    f"{bugzilla_url}/user",
                    params={"ids": raw, "include_fields": "id,name"},
                    headers={"X-BUGZILLA-API-KEY": inputs.bugzilla_api_key},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Bugzilla user lookup failed: %s", exc)
                return JSONResponse({"error": "Bugzilla user lookup failed"}, 502)
        users = response.json().get("users") or []
        return JSONResponse({"emails": {str(u["id"]): u["name"] for u in users}})

    return Starlette(
        routes=[
            Mount(
                "/phabricator/api",
                app=conduit_proxy(phabricator, allowed_methods=ALLOWED_METHODS),
            ),
            Route("/bugzilla/emails", emails),
        ]
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    inputs = BrokerInputs()
    uvicorn.run(build_app(inputs), host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
