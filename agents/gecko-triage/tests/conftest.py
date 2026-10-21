import copy
import datetime as dt
import functools
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

# The tests run as the media team unless they use `no_team`. Set before the agent's
# config is imported, which reads it.
os.environ.setdefault("TEAM", "media")

import hackbot_agents.gecko_triage.__main__  # noqa: E402, F401
from hackbot_agents.gecko_triage import broker_client, config  # noqa: E402
from hackbot_agents.gecko_triage.config import BOT_ACCOUNT  # noqa: E402
from hackbot_agents.gecko_triage.state import TriageState  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@functools.cache
def _read(name: str):
    return json.loads((FIXTURES / name).read_text())


def load(name: str):
    return copy.deepcopy(_read(name))


# A fixed clock: the needinfo ages in the tests are relative to this.
NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
# A month on: a needinfo set with the bot's comment of the 13th is stale by then.
LATER = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)
# The time of the bot's comment in the returning fixture. A needinfo set then is fresh
# at NOW and stale at LATER.
BOT_COMMENTED = "2026-10-13T09:00:00Z"


def with_needinfo(
    bug: dict, requestee: str, since: str = BOT_COMMENTED, setter: str = BOT_ACCOUNT
) -> dict:
    """``bug`` with one more open needinfo."""
    flag = {
        "name": "needinfo",
        "status": "?",
        "requestee": requestee,
        "setter": setter,
        "creation_date": since,
    }
    return dict(bug, flags=[*(bug.get("flags") or ()), flag])


def quiet(bug: dict, **times) -> dict:
    """``bug`` as it stands right after the bot's comment, unless ``times`` say more."""
    return {**bug, "last_change_time_non_bot": BOT_COMMENTED, **times}


def fake_broker(monkeypatch, responses: dict) -> list:
    """A broker that answers each tool from ``responses``; the calls it got, in order.

    An answer is a JSON body, an Exception for an error reply, or an async function of
    the args that gives the body.
    """
    calls = []

    @asynccontextmanager
    async def client(url):
        calls.append(("connect", url))
        yield "read", "write", None

    class Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            pass

        async def call_tool(self, tool, args):
            calls.append((tool, args))
            body = responses[tool]
            if callable(body):
                body = await body(args)
            if isinstance(body, Exception):
                return SimpleNamespace(
                    isError=True, content=[SimpleNamespace(text=str(body))]
                )
            return SimpleNamespace(
                isError=False, content=[SimpleNamespace(text=json.dumps(body))]
            )

    monkeypatch.setattr(broker_client, "streamablehttp_client", client)
    monkeypatch.setattr(broker_client, "ClientSession", Session)
    return calls


@pytest.fixture
def bug_new() -> dict:
    return load("bug_new.json")["bugs"][0]


@pytest.fixture
def comments_new() -> list[dict]:
    return load("comments_new.json")["bugs"]["9000001"]["comments"]


@pytest.fixture
def comments_returning() -> list[dict]:
    return load("comments_returning.json")["bugs"]["9000001"]["comments"]


@pytest.fixture
def new_state(bug_new, comments_new) -> TriageState:
    return TriageState(bug=bug_new, comments=comments_new, now=NOW)


@pytest.fixture
def returning_state(bug_new, comments_returning) -> TriageState:
    """The bot's tagged comment with an old footer, then the reporter's answer."""
    bug = dict(bug_new, last_change_time_non_bot="2026-10-14T10:00:00Z")
    return TriageState(bug=bug, comments=comments_returning, now=NOW)


# What `config.py` sets when `TEAM` is unset; `test_no_team.py` checks the import itself.
NO_TEAM = {
    "TEAM": None,
    "TEAM_DIR": None,
    "TEAM_RULES_DIR": None,
    "CAPTURE": None,
    "TRIAGE_SCOPE": (),
    "SCOPE_KEYS": frozenset(),
    "NEEDINFO_EXPERTS": frozenset(),
    "NEEDINFO_FALLBACK": frozenset(),
    "ISSUE_TYPES": frozenset(),
    "TEAM_META_BUGS": frozenset(),
}


@pytest.fixture
def no_team(monkeypatch):
    """A run with no team, in every agent module that holds a team value.

    A module that imports a team value from `config` holds the same object, so each
    attribute that is one of those objects is patched.
    """
    originals = {name: getattr(config, name) for name in NO_TEAM}
    patched = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("hackbot_agents.gecko_triage"):
            continue
        for attr, value in list(vars(module).items()):
            for name, original in originals.items():
                if value is original:
                    monkeypatch.setattr(module, attr, NO_TEAM[name])
                    patched.append((module_name, attr))
                    break
    for name in NO_TEAM:
        assert ("hackbot_agents.gecko_triage.config", name) in patched, name
    assert ("hackbot_agents.gecko_triage.state", "NEEDINFO_EXPERTS") in patched
    for module in ("agent", "dupes", "hooks", "preflight"):
        assert (f"hackbot_agents.gecko_triage.{module}", "TEAM") in patched, module


class FakeBugzilla:
    """A Bugzilla search over ``bugs``, for the boolean charts the duplicate search sends.

    Each bug carries its `comments` as plain strings. Only the operators the search uses
    are known, so a new operator fails the test instead of matching everything.
    """

    TEXT = {"short_desc": "summary", "cf_crash_signature": "cf_crash_signature"}

    def __init__(self, bugs: list[dict]):
        self.bugs = bugs
        self.calls: list[dict] = []

    def _texts(self, bug: dict, field: str) -> list[str]:
        if field == "longdesc":
            return list(bug.get("comments") or ())
        if field == "bug_id":
            return [str(bug["id"])]
        return [bug.get(self.TEXT[field]) or ""]

    def _matches(self, bug: dict, params: dict) -> bool:
        if bug["product"] not in params["product"]:
            return False
        if bug["component"] not in params["component"]:
            return False
        n = 1
        while f"f{n}" in params:
            field, op, value = params[f"f{n}"], params[f"o{n}"], params[f"v{n}"]
            texts = self._texts(bug, field)
            if op == "regexp":
                hit = any(re.search(value, t, re.IGNORECASE) for t in texts)
            elif op == "substring":
                hit = any(value.lower() in t.lower() for t in texts)
            elif op == "notequals":
                hit = str(value) not in texts
            else:
                raise AssertionError(f"unknown operator {op}")
            if not hit:
                return False
            n += 1
        return True

    async def search(self, params: dict) -> list[dict]:
        self.calls.append(params)
        assert params["order"] == "bug_id DESC"
        fields = params["include_fields"].split(",")
        found = sorted(
            (b for b in self.bugs if self._matches(b, params)), key=lambda b: -b["id"]
        )
        return [{f: b.get(f) for f in fields} for b in found[: params["limit"]]]


@pytest.fixture
def corpus() -> list[dict]:
    return load("dupes_corpus.json")["bugs"]
