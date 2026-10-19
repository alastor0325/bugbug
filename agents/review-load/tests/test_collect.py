import httpx
import pytest
from hackbot_agents.review_load.collect import (
    Collector,
    broker_email_lookup,
    collect_inputs,
)
from hackbot_agents.review_load.config import ReviewLoadConfig
from hackbot_runtime import AgentError
from phabricator_client import PhabricatorClient, PhabricatorSettings
from testdata import ALICE, BOB, CAROL, GROUP, OUTSIDER, add_reviewers, revision, ts

CONFIG = ReviewLoadConfig.model_validate(
    {
        "name": "example",
        "review_group": "example-reviewers",
        "members": {"alice": "Alice", "bob": "Bob", "gone": "Gone"},
        "report": {"slack_channel": "#x"},
        "authors": {"external_accounts": ["outsider"]},
    }
)


class FakePhabricator(PhabricatorClient):
    """Answers Conduit calls from ``pages``: method -> list of result pages."""

    def __init__(self, pages: dict[str, list[dict]]):
        super().__init__(PhabricatorSettings(api_key="api-" + "a" * 28))
        self.pages = {m: list(p) for m, p in pages.items()}
        self.calls: list[tuple[str, dict]] = []

    async def conduit_request(self, method: str, **payload):
        self.calls.append((method, payload))
        return self.pages[method].pop(0)


def page(data, after=None):
    return {"data": data, "cursor": {"after": after}}


async def no_emails(ids):
    return {}


async def test_unknown_group_stops_the_run():
    phab = FakePhabricator({"project.search": [page([])]})
    with pytest.raises(AgentError, match="example-reviewers"):
        await Collector(phab, no_emails).group_phid("example-reviewers")


def _phab():
    revisions = [
        revision(1, author=CAROL, reviewers={GROUP: "added"}),
        revision(2, author=OUTSIDER, reviewers={ALICE: "added"}),
        revision(3, author=CAROL, reviewers={ALICE: "accepted"}),
    ]
    return FakePhabricator(
        {
            "project.search": [page([{"phid": GROUP}])],
            "user.search": [
                page(
                    [
                        {"phid": ALICE, "fields": {"username": "alice"}},
                        {"phid": BOB, "fields": {"username": "bob"}},
                    ]
                ),
                page(
                    [
                        {"phid": CAROL, "fields": {"username": "carol"}},
                        {"phid": OUTSIDER, "fields": {"username": "outsider"}},
                    ]
                ),
            ],
            "differential.revision.search": [page(revisions)],
            "transaction.search": [
                page([add_reviewers(ts(2026, 10, 2), GROUP)]),
                page([]),
            ],
            "bugzilla.account.search": [
                [{"phid": CAROL, "id": "11"}, {"phid": OUTSIDER, "id": "12"}]
            ],
        }
    )


async def test_collect_inputs():
    async def emails(ids):
        assert sorted(ids) == [11, 12]
        return {11: "carol@mozilla.com", 12: "o@mozilla.com"}

    phab = _phab()
    revisions, watched, kinds = await collect_inputs(CONFIG, Collector(phab, emails))

    assert watched == {GROUP: "#example-reviewers", ALICE: "alice", BOB: "bob"}
    assert [r.id for r in revisions] == [1, 2]
    assert {r.id: len(r.transactions) for r in revisions} == {1: 1, 2: 0}
    # outsider is in external_accounts, so the mozilla.com email is overridden.
    assert kinds == {CAROL: "staff", OUTSIDER: "external"}

    [search] = [p for m, p in phab.calls if m == "differential.revision.search"]
    assert search["constraints"] == {
        "reviewerPHIDs": [GROUP, ALICE, BOB],
        "statuses": ["needs-review"],
    }
    assert search["attachments"] == {"reviewers": True}
    # D3 waits on nobody watched, so its history is never read.
    assert [m for m, _ in phab.calls].count("transaction.search") == 2


async def test_an_author_without_a_bugzilla_account_is_unknown():
    phab = _phab()
    phab.pages["bugzilla.account.search"] = [[{"phid": CAROL, "id": "11"}]]

    async def emails(ids):
        return {11: "carol@example.com"}

    _, _, kinds = await collect_inputs(CONFIG, Collector(phab, emails))
    assert kinds == {CAROL: "external", OUTSIDER: "external"}
    phab = _phab()
    phab.pages["bugzilla.account.search"] = [[]]
    _, _, kinds = await collect_inputs(CONFIG, Collector(phab, no_emails))
    assert kinds[CAROL] == "unknown"


async def test_broker_email_lookup():
    def handler(request):
        assert request.url.path == "/bugzilla/emails"
        assert request.url.params.get_list("ids") == ["11", "12"]
        return httpx.Response(200, json={"emails": {"11": "a@example.com"}})

    lookup = broker_email_lookup("http://broker", httpx.MockTransport(handler))
    assert await lookup([11, 12]) == {11: "a@example.com"}
