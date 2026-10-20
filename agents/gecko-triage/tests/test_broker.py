"""Tests for the broker's filter that keeps restricted and private content from the agent."""

import copy
import json

import pytest
from agent_tools.bugzilla import GET_BUGS_DEFAULT_FIELDS, BugzillaContext
from agent_tools.registry import ToolError
from hackbot_agents.gecko_triage import broker

PUBLIC = {"id": 1, "summary": "public", "groups": []}
RESTRICTED = {"id": 2, "summary": "secret", "groups": ["core-security"]}
UNKNOWN = {"id": 3, "summary": "no groups field"}
COMMENTS = [
    {"id": 10, "is_private": False, "text": "public"},
    {"id": 11, "is_private": True, "text": "private"},
    {"id": 12, "text": "unknown"},
]
# BMO marks attachments 0 or 1, as `GET /rest/bug/<id>/attachment` does live; comments
# are marked false or true.
ATTACHMENTS = [
    {"id": 20, "is_private": 0, "file_name": "log.txt"},
    {"id": 21, "is_private": 1, "file_name": "crash.dmp"},
    {"id": 22, "file_name": "unknown.txt"},
    {"id": 23, "is_private": "0", "file_name": "string.txt"},
]


class FakeBugzilla:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def request(self, path, params=None):
        self.calls.append((path, params or {}))
        return copy.deepcopy(self.responses[path])


def _tools(responses: dict):
    client = FakeBugzilla(responses)
    tools = {t.name: t.handler for t in broker.TOOLS}
    return tools, BugzillaContext(client=client), client


@pytest.mark.parametrize(
    ("item", "public"),
    [
        ({"is_private": False}, True),
        ({"is_private": 0}, True),
        ({"is_private": True}, False),
        ({"is_private": 1}, False),
        ({"is_private": None}, False),
        ({"is_private": "0"}, False),
        ({"is_private": 0.0}, False),
        ({}, False),
    ],
)
def test_public_items_reads_both_bugzilla_markings(item, public):
    assert broker.public_items([item]) == ([item] if public else [])


def test_the_broker_serves_only_the_filtered_read_tools():
    assert {t.name for t in broker.TOOLS} == {
        "search_bugs",
        "get_bugs",
        "get_bug_comments",
        "get_bug_attachments",
        "get_bug_history",
    }


def test_the_tools_keep_their_schema():
    for t in broker.TOOLS:
        assert t.namespace == "bugzilla"
        assert t.description
        assert "ctx" not in t.input_schema["properties"]
    get_bugs = next(t for t in broker.TOOLS if t.name == "get_bugs")
    assert set(get_bugs.input_schema["properties"]) == {
        "ids",
        "include_fields",
        "include_comments",
    }


async def test_search_drops_restricted_bugs_and_hides_the_groups_it_added():
    tools, ctx, client = _tools(
        {"bug": {"bugs": [dict(PUBLIC), dict(RESTRICTED), dict(UNKNOWN)]}}
    )
    res = await tools["search_bugs"](ctx, {"include_fields": "id,summary"})
    assert res == {"count": 1, "bugs": [{"id": 1, "summary": "public"}]}
    assert client.calls[0][1]["include_fields"] == "id,summary,groups"

    await tools["search_bugs"](ctx, {"include_fields": "summary"})
    assert client.calls[1][1]["include_fields"] == "summary,groups,id"


async def test_search_keeps_groups_when_asked_and_adds_them_when_unset():
    tools, ctx, client = _tools({"bug": {"bugs": [dict(PUBLIC), dict(RESTRICTED)]}})
    res = await tools["search_bugs"](ctx, {"include_fields": ["id", "groups"]})
    assert res["bugs"] == [PUBLIC]
    assert client.calls[0][1]["include_fields"] == "id,groups"

    await tools["search_bugs"](ctx, {"product": "Core"})
    assert client.calls[1][1] == {
        "product": "Core",
        "include_fields": f"{GET_BUGS_DEFAULT_FIELDS},groups",
    }


async def test_get_bugs_reports_restricted_bugs_as_inaccessible():
    tools, ctx, client = _tools(
        {"bug": {"bugs": [dict(PUBLIC), dict(RESTRICTED), dict(UNKNOWN)]}}
    )
    res = await tools["get_bugs"](ctx, [1, 2, 3, 4], include_fields="id,summary")
    assert res == {
        "count": 1,
        "bugs": [{"id": 1, "summary": "public"}],
        "inaccessible": [2, 3, 4],
    }
    assert client.calls[0][1]["include_fields"] == "id,summary,groups"

    await tools["get_bugs"](ctx, [1])
    assert client.calls[1][1]["include_fields"] == f"{GET_BUGS_DEFAULT_FIELDS},groups"


async def test_get_bugs_drops_private_comments():
    tools, ctx, _ = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/comment": {"bugs": {"1": {"comments": COMMENTS}}},
        }
    )
    res = await tools["get_bugs"](ctx, [1], include_comments=True)
    assert [c["id"] for c in res["bugs"][0]["comments"]] == [10]


async def test_get_bug_comments_drops_private_comments():
    tools, ctx, client = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/comment": {"bugs": {"1": {"comments": COMMENTS}}},
        }
    )
    res = await tools["get_bug_comments"](ctx, 1)
    assert res == {"bug_id": 1, "count": 1, "comments": [COMMENTS[0]]}
    assert client.calls[0] == ("bug", {"id": "1", "include_fields": "id,groups"})


async def test_get_bug_attachments_drops_private_attachments():
    tools, ctx, _ = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/attachment": {"bugs": {"1": ATTACHMENTS}},
        }
    )
    res = await tools["get_bug_attachments"](ctx, 1)
    assert res == {"bug_id": 1, "count": 1, "attachments": [ATTACHMENTS[0]]}


async def test_get_bug_history_gives_field_names_only():
    history = [
        {
            "when": "2026-10-13T09:30:00Z",
            "who": "dev@example.com",
            "changes": [
                {"field_name": "cc", "added": "dev@example.com", "removed": ""},
                {"field_name": "priority", "added": "P2", "removed": "P3"},
            ],
        },
        {
            "when": "2026-10-13T10:00:00Z",
            "who": "dev@example.com",
            "changes": [
                {
                    "field_name": "attachments.isobsolete",
                    "attachment_id": 7,
                    "added": "1",
                },
                {"field_name": "comment_tag", "comment_id": 8, "added": "spam"},
            ],
        },
    ]
    tools, ctx, client = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/history": {"bugs": [{"id": 1, "history": history}]},
        }
    )
    res = await tools["get_bug_history"](ctx, 1, new_since="2026-10-13T09:00:00Z")
    assert res == {
        "bug_id": 1,
        "history": [
            {
                "when": "2026-10-13T09:30:00Z",
                "who": "dev@example.com",
                "fields": ["cc", "priority"],
            }
        ],
    }
    assert client.calls[1] == ("bug/1/history", {"new_since": "2026-10-13T09:00:00Z"})


@pytest.mark.parametrize("bug", [RESTRICTED, UNKNOWN])
@pytest.mark.parametrize(
    "tool", ["get_bug_comments", "get_bug_attachments", "get_bug_history"]
)
async def test_a_restricted_bug_gives_no_comments_or_attachments(tool, bug):
    tools, ctx, client = _tools({"bug": {"bugs": [dict(bug)]}})
    with pytest.raises(ToolError) as e:
        await tools[tool](ctx, bug["id"])
    assert e.value.payload["error"] == "access_denied"
    # Only the groups lookup went out; nothing of the bug was read.
    assert [path for path, _ in client.calls] == ["bug"]


@pytest.mark.parametrize(
    "fields",
    [
        "_all",
        "_default",
        "_extra",
        "id,_extra",
        "attachments",
        "comments",
        "history",
        "id,-groups",
        ["id", "attachments"],
    ],
)
@pytest.mark.parametrize("tool", ["search_bugs", "get_bugs"])
async def test_fields_the_broker_cannot_filter_are_refused(tool, fields):
    tools, ctx, client = _tools({"bug": {"bugs": [dict(PUBLIC)]}})
    with pytest.raises(ToolError) as e:
        if tool == "search_bugs":
            await tools[tool](ctx, {"include_fields": fields})
        else:
            await tools[tool](ctx, [1], include_fields=fields)
    assert e.value.payload["error"] == "field_not_allowed"
    assert client.calls == []


def test_the_fields_the_agent_asks_for_are_allowed():
    from hackbot_agents.gecko_triage import dupes, state, targets

    for fields in (
        GET_BUGS_DEFAULT_FIELDS,
        dupes.CANDIDATE_FIELDS,
        state.BUG_FIELDS,
        targets.TARGET_FIELDS,
        "id,summary,status,resolution,dupe_of,keywords",
        "id,summary,keywords,flags,cc,creator,severity,priority,blocks,see_also,"
        "regressed_by,whiteboard,description",
        " id , cf_crash_signature ",
    ):
        broker.upstream_fields(fields, "id")


DESCRIBED = [
    {"id": 30, "count": 0, "is_private": False, "text": "comment zero"},
    {"id": 31, "count": 1, "is_private": False, "text": "later"},
]
HIDDEN = [{"id": 40, "count": 0, "is_private": True, "text": "private zero"}]


@pytest.mark.parametrize("tool", ["search_bugs", "get_bugs"])
async def test_the_description_is_public_comment_zero(tool):
    bugs = [dict(PUBLIC), dict(PUBLIC, id=4), dict(PUBLIC, id=5), dict(RESTRICTED)]
    tools, ctx, client = _tools(
        {
            "bug": {"bugs": bugs},
            "bug/1/comment": {
                "bugs": {"1": {"comments": DESCRIBED}, "4": {"comments": HIDDEN}}
            },
        }
    )
    if tool == "search_bugs":
        res = await tools[tool](ctx, {"include_fields": "summary,description"})
    else:
        res = await tools[tool](ctx, [1, 4, 5, 2], include_fields="summary,description")
    assert res["bugs"] == [
        {"summary": "public", "description": "comment zero"},
        {"summary": "public"},
        {"summary": "public"},
    ]
    assert client.calls[0][1]["include_fields"] == "summary,groups,id"
    # Only the public bugs have their comments read.
    assert client.calls[1] == ("bug", {"id": "1,4,5", "include_fields": "id"})
    assert client.calls[2][0] == "bug/1/comment"


async def test_get_bugs_reuses_the_comments_it_fetched_for_the_description():
    tools, ctx, client = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/comment": {"bugs": {"1": {"comments": DESCRIBED}}},
        }
    )
    res = await tools["get_bugs"](
        ctx, [1], include_fields="id,description", include_comments=True
    )
    assert res["bugs"][0]["description"] == "comment zero"
    assert [path for path, _ in client.calls] == ["bug", "bug/1/comment"]


@pytest.mark.parametrize(
    "params",
    [
        {"attachments.description": "crash"},
        {"attachments.isprivate": "1"},
        {"attach_data.thedata": "secret"},
        {"longdescs.isprivate": "1"},
        {"commenter": "someone@example.com"},
        {"content": "word"},
        {"quicksearch": "attachments.description:crash"},
        {"comment_tag": "spam"},
        {"f1": "attachments.description", "o1": "substring", "v1": "x"},
        {"f2": "LONGDESCS.ISPRIVATE", "o2": "equals", "v2": "1"},
        {"f1": ["longdesc", "attachments.filename"]},
        {"field0-0-0": "attachments.mimetype", "type0-0-0": "equals"},
    ],
)
async def test_search_refuses_params_that_read_private_content(params):
    tools, ctx, client = _tools({"bug": {"bugs": []}})
    with pytest.raises(ToolError) as e:
        await tools["search_bugs"](ctx, params)
    assert e.value.payload["error"] == "search_field_not_allowed"
    assert client.calls == []


async def test_search_keeps_the_comment_text_search_the_duplicate_hunter_uses():
    tools, ctx, client = _tools({"bug": {"bugs": [dict(PUBLIC)]}})
    params = {
        "product": ["Core"],
        "summary": "fade",
        "f1": "longdesc",
        "o1": "anywordssubstr",
        "v1": "ramp",
        "longdesc": "fade",
        "include_fields": "id,summary,status,resolution,dupe_of,keywords",
        "limit": 50,
    }
    res = await tools["search_bugs"](ctx, params)
    assert res["count"] == 1
    assert client.calls[0][1]["f1"] == "longdesc"


def test_the_duplicate_search_queries_pass_the_search_check():
    from hackbot_agents.gecko_triage import dupes

    scope = (["Core"], ["Audio/Video"])
    queries = [
        dupes.word_query(*scope, "fade"),
        dupes.signature_query(*scope, "Foo"),
        *(q for _, _, q in dupes.concept_queries(*scope, 1, [["fade"], ["pause"]])),
    ]
    for params in queries:
        assert broker.refused_search_fields(params) == []


def test_no_tool_text_names_download_attachment():
    for t in broker.TOOLS:
        assert "download_attachment" not in t.description, t.name
        assert "download_attachment" not in json.dumps(t.input_schema), t.name
    attachments = next(t for t in broker.TOOLS if t.name == "get_bug_attachments")
    assert "fetch_attachment" in attachments.description
    assert set(attachments.input_schema["properties"]) == {"bug_id", "include_data"}


async def test_data_omitted_notes_point_to_fetch_attachment():
    big = {"id": 23, "is_private": 0, "file_name": "big.log", "size": 10**7}
    image = {"id": 24, "is_private": 0, "file_name": "shot.png"}
    tools, ctx, _ = _tools(
        {
            "bug": {"bugs": [dict(PUBLIC)]},
            "bug/1/attachment": {"bugs": {"1": [big, image]}},
        }
    )
    res = await tools["get_bug_attachments"](ctx, 1, include_data=True)
    notes = [a["data_omitted"] for a in res["attachments"]]
    assert len(notes) == 2
    for note in notes:
        assert "download_attachment" not in note
        assert "fetch_attachment" in note


def test_the_app_serves_the_filtered_tools(monkeypatch):
    served = {}

    def fake_build(name, ctx, tools):
        served[name] = tools
        return {"instance": object()}

    monkeypatch.setattr(broker, "build_sdk_server", fake_build)
    app = broker.build_app(
        broker.BrokerInputs(
            bugzilla_api_url="https://bugzilla.example.com/rest",
            bugzilla_api_key="bz-key",
        )
    )
    assert served == {"bugzilla": broker.TOOLS}
    assert [r.path for r in app.routes] == ["/mcp"]


async def test_a_slow_bugzilla_call_does_not_block_the_others():
    import asyncio
    import time

    async def blocking(ctx, bug_id):
        time.sleep(0.3)
        return {"bug_id": bug_id}

    run = broker.off_loop(blocking)
    start = time.monotonic()
    results = await asyncio.gather(*(run(None, i) for i in range(4)))
    assert time.monotonic() - start < 0.9
    assert [r["bug_id"] for r in results] == [0, 1, 2, 3]


def test_every_served_tool_runs_off_the_event_loop():
    for tool in broker.TOOLS:
        assert getattr(tool.handler, "off_loop", False), tool.name


def test_the_bugzilla_session_has_a_timeout_and_a_matching_pool():
    import bugsy

    client = bugsy.Bugsy(api_key="k", bugzilla_url="https://bugzilla.example.com/rest")
    broker.bound_session(client)
    adapter = client.session.get_adapter("https://bugzilla.example.com/rest/bug")
    assert adapter.timeout == broker.BUGZILLA_TIMEOUT
    assert adapter._pool_maxsize >= broker.MAX_WORKERS


def test_the_adapter_applies_its_timeout_when_none_is_given(monkeypatch):
    from requests.adapters import HTTPAdapter

    seen = {}
    monkeypatch.setattr(
        HTTPAdapter, "send", lambda self, req, **kw: seen.update(kw) or "sent"
    )
    adapter = broker.TimeoutAdapter(timeout=7)
    assert adapter.send(None) == "sent"
    assert seen["timeout"] == 7
    adapter.send(None, timeout=3)
    assert seen["timeout"] == 3
