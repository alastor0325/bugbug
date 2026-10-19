import httpx
from hackbot_agents.review_load import broker
from phabricator_client import PhabricatorSettings
from starlette.routing import Mount
from starlette.testclient import TestClient

VALID_TOKEN = "api-" + "a" * 28
BMO = "https://bugzilla.example.com/rest"


def _app(handler=None):
    transport = httpx.MockTransport(handler or (lambda request: httpx.Response(500)))
    return broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url=BMO,
            bugzilla_api_key="bz-key",
            phabricator=PhabricatorSettings(
                url="https://phab.example.com", api_key=VALID_TOKEN
            ),
        ),
        bugzilla_transport=transport,
    )


def test_proxy_allows_the_extra_read_methods_only():
    assert {
        "differential.revision.search",
        "project.search",
        "user.search",
        "transaction.search",
        "bugzilla.account.search",
    } <= broker.ALLOWED_METHODS
    assert not any(
        m.endswith((".edit", ".create", ".update")) for m in broker.ALLOWED_METHODS
    )


def test_writes_are_refused():
    resp = TestClient(_app()).post("/phabricator/api/differential.revision.edit")
    assert resp.status_code == 403


def test_routes():
    app = _app()
    assert {r.path for r in app.routes if isinstance(r, Mount)} == {"/phabricator/api"}


def test_emails_returns_only_id_and_login():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = request.url
        seen["key"] = request.headers.get("X-BUGZILLA-API-KEY")
        return httpx.Response(
            200,
            json={
                "users": [
                    {"id": 11, "name": "a@mozilla.com", "real_name": "A"},
                    {"id": 12, "name": "b@example.com"},
                ]
            },
        )

    resp = TestClient(_app(handler)).get("/bugzilla/emails?ids=11&ids=12")
    assert resp.status_code == 200
    assert resp.json() == {"emails": {"11": "a@mozilla.com", "12": "b@example.com"}}
    assert seen["key"] == "bz-key"
    assert seen["url"].params.get_list("ids") == ["11", "12"]
    assert seen["url"].params["include_fields"] == "id,name"


def test_emails_refuses_bad_ids():
    client = TestClient(_app())
    assert client.get("/bugzilla/emails?ids=abc").status_code == 400
    assert client.get("/bugzilla/emails").status_code == 400
    many = "&".join(f"ids={i}" for i in range(broker.MAX_IDS + 1))
    assert client.get(f"/bugzilla/emails?{many}").status_code == 400


def test_emails_upstream_error_is_502():
    resp = TestClient(_app()).get("/bugzilla/emails?ids=11")
    assert resp.status_code == 502
