"""Bugzilla MCP broker.

Sidecar container that holds the Bugzilla API key and serves the
bugzilla MCP tools over HTTP. The agent process (in a sibling container
in the same Cloud Run Job task) reaches us at `127.0.0.1:<port>/mcp`.
The agent container itself binds no Bugzilla credentials.

The comment the agent writes is public, so the broker serves only what a
logged-out reader could see. It drops bugs with any group, refuses the comments
and attachments of such a bug, and drops private comments and attachments. An
item that does not say it is public is dropped too.

A bug read returns only the fields in `ALLOWED_FIELDS`. The broker cannot filter
nested comments, attachments or history, so it refuses those fields. It sets
`description` from comment 0 only when that comment is public. The history of a
public bug comes from its own tool, as field names with no values, and with no
changes to comments or attachments.

A search refuses the terms that match private comments or attachments. One risk
stays: `longdesc` searches comment text, and the duplicate hunter needs it. If
the API key can read private comments, a match on a public bug tells one bit
about them.
"""

import asyncio
import concurrent.futures
import dataclasses
import functools
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated, Any

import bugsy
import uvicorn
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolDefinition, ToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from requests.adapters import HTTPAdapter
from starlette.applications import Starlette
from starlette.routing import Mount

from .visibility import marked_public

log = logging.getLogger("bugzilla-broker")


GROUPS = "groups"
DESCRIPTION = "description"

# Fields a logged-out reader sees on a public bug. `_default`, `_all` and `_extra`
# expand to nested attachments, comments and history that the broker cannot filter.
ALLOWED_FIELDS = frozenset(
    {
        "alias",
        "assigned_to",
        "assigned_to_detail",
        "blocks",
        "cc",
        "cc_detail",
        "classification",
        "component",
        "creation_time",
        "creator",
        "creator_detail",
        "depends_on",
        DESCRIPTION,
        "dupe_of",
        "duplicates",
        "flags",
        GROUPS,
        "id",
        "is_confirmed",
        "is_open",
        "keywords",
        "last_change_time",
        "last_change_time_non_bot",
        "mentors",
        "mentors_detail",
        "op_sys",
        "platform",
        "priority",
        "product",
        "qa_contact",
        "qa_contact_detail",
        "regressed_by",
        "regressions",
        "resolution",
        "see_also",
        "severity",
        "status",
        "summary",
        "target_milestone",
        "triage_owner",
        "type",
        "url",
        "version",
        "votes",
        "whiteboard",
    }
)
CUSTOM_FIELD_PREFIX = "cf_"

# Search terms on private comments and attachments; `longdesc` stays (see above).
REFUSED_SEARCH_FIELDS = frozenset(
    {"commenter", "comment_tag", "content", "quicksearch"}
)
REFUSED_SEARCH_PREFIXES = ("attachments.", "attach_data.", "longdescs.")
_CHART_FIELD = re.compile(r"^(f\d+|field\d+-\d+-\d+)$")

ATTACHMENT_HINT = (
    "This broker cannot download attachments. Fetch one with the fetch_attachment "
    "tool. If you do not have that tool, give the attachment id to the agent that "
    "started you."
)


def upstream_fields(
    include_fields: str | list | None, default: str
) -> tuple[str, frozenset[str], bool]:
    """The field list to send, the fields it adds, and whether it asks for `description`.

    `id` and `groups` are always sent. `description` is never sent: it is filled
    from the public comment 0.
    """
    if isinstance(include_fields, list):
        include_fields = ",".join(str(f) for f in include_fields)
    names = [f.strip() for f in (include_fields or default).split(",") if f.strip()]
    refused = [
        f
        for f in names
        if f not in ALLOWED_FIELDS and not f.startswith(CUSTOM_FIELD_PREFIX)
    ]
    if refused:
        raise ToolError(
            f"fields {', '.join(refused)} are not served",
            payload={
                "error": "field_not_allowed",
                "fields": refused,
                "hint": (
                    "List the fields you need by name. Use get_bug_comments and "
                    "get_bug_attachments for comments and attachments."
                ),
            },
        )
    wants_description = DESCRIPTION in names
    sent = [f for f in names if f != DESCRIPTION]
    added = [f for f in (GROUPS, "id") if f not in sent]
    return ",".join(sent + added), frozenset(added), wants_description


def refused_search_fields(params: dict[str, Any]) -> list[str]:
    """The search params, or boolean chart fields, that read private content."""
    named = list(params)
    for key, value in params.items():
        if _CHART_FIELD.match(key):
            named += value if isinstance(value, list) else [value]
    return [
        str(n)
        for n in named
        if (low := str(n).lower()) in REFUSED_SEARCH_FIELDS
        or low.startswith(REFUSED_SEARCH_PREFIXES)
    ]


def is_public(bug: dict) -> bool:
    return bug.get(GROUPS) == []


def public_bugs(bugs: list[dict]) -> list[dict]:
    public = [b for b in bugs if is_public(b)]
    for b in public:
        if "comments" in b:
            b["comments"] = public_items(b["comments"])
    return public


def drop_fields(bugs: list[dict], fields: frozenset[str]) -> None:
    for b in bugs:
        for f in fields:
            b.pop(f, None)


def public_items(items: list[dict]) -> list[dict]:
    """The comments or attachments marked public."""
    return [i for i in items if marked_public(i)]


def first_comment_text(comments: list[dict]) -> str | None:
    zero = next((c for c in public_items(comments) if c.get("count") == 0), None)
    return zero.get("text") if zero else None


async def _add_descriptions(ctx: BugzillaContext, bugs: list[dict]) -> None:
    """Set `description` to the text of each bug's comment 0, when it is public."""
    unread = [b["id"] for b in bugs if "comments" not in b]
    fetched: dict[int, list[dict]] = {}
    if unread:
        res = await bugzilla.get_bugs(ctx, unread, "id", include_comments=True)
        fetched = {b["id"]: b.get("comments", []) for b in res["bugs"]}
    for b in bugs:
        text = first_comment_text(b.get("comments") or fetched.get(b["id"], []))
        if text is not None:
            b[DESCRIPTION] = text


async def _serve(
    ctx: BugzillaContext,
    bugs: list[dict],
    added: frozenset[str],
    wants_description: bool,
) -> list[dict]:
    """The public bugs, with `description` filled when asked and the added fields gone."""
    bugs = public_bugs(bugs)
    if wants_description:
        await _add_descriptions(ctx, bugs)
    drop_fields(bugs, added)
    return bugs


async def _require_public(ctx: BugzillaContext, bug_id: int) -> None:
    result = await bugzilla.get_bugs(ctx, [bug_id], f"id,{GROUPS}")
    if not public_bugs(result["bugs"]):
        raise ToolError(
            f"bug {bug_id} is not public",
            payload={
                "error": "access_denied",
                "bug_id": bug_id,
                "hint": "This broker serves public bugs only. Skip it.",
            },
        )


BUGZILLA_TIMEOUT = 60
MAX_WORKERS = 16
_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=MAX_WORKERS, thread_name_prefix="bugzilla"
)


class TimeoutAdapter(HTTPAdapter):
    """An adapter that sets a timeout on every request that has none.

    Without one, a hung Bugzilla call holds a worker thread for good.
    """

    def __init__(self, timeout: float, **kwargs):
        self.timeout = timeout
        super().__init__(**kwargs)

    def send(self, request, timeout=None, **kwargs):
        return super().send(request, timeout=timeout or self.timeout, **kwargs)


def bound_session(client: bugsy.Bugsy) -> None:
    """Give `client` a request timeout and a pool as large as the worker count."""
    adapter = TimeoutAdapter(
        BUGZILLA_TIMEOUT, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS
    )
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)


def off_loop(handler):
    """`handler` run in a worker thread with its own event loop.

    The agent-tools handlers call Bugzilla with blocking requests. On the server's
    loop, one slow call would stall every other client, subagents included.
    """

    @functools.wraps(handler)
    async def run(*args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            _POOL, lambda: asyncio.run(handler(*args, **kwargs))
        )

    run.off_loop = True
    return run


def _public_tool(
    original, handler, description: str | None = None, own_schema: bool = False
) -> ToolDefinition:
    """`original`'s tool, served by `handler`.

    With `own_schema`, the argument schema comes from `handler`'s signature.
    """
    defn = next(t for t in bugzilla.TOOLS if t.handler is original)
    wrapped = handler if own_schema else functools.wraps(original)(handler)
    return dataclasses.replace(
        defn, handler=off_loop(wrapped), description=description or defn.description
    )


async def _search_bugs(ctx: BugzillaContext, params: dict[str, Any]) -> dict:
    refused = refused_search_fields(params)
    if refused:
        raise ToolError(
            f"search terms {', '.join(refused)} are not served",
            payload={
                "error": "search_field_not_allowed",
                "fields": refused,
                "hint": (
                    "These terms match private comments or attachments. Search "
                    "summaries, or comment text with `longdesc`."
                ),
            },
        )
    params = dict(params)
    params["include_fields"], added, wants_description = upstream_fields(
        params.get("include_fields"), bugzilla.GET_BUGS_DEFAULT_FIELDS
    )
    result = await bugzilla.search_bugs(ctx, params)
    bugs = await _serve(ctx, result["bugs"], added, wants_description)
    return dict(result, count=len(bugs), bugs=bugs)


async def _get_bugs(
    ctx: BugzillaContext,
    ids: Annotated[list[int], Field(description="Bug IDs to fetch.")],
    include_fields: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated field names. `_default`, `_all` and `_extra` are "
                "refused, and so are comments, attachments and history. Defaults "
                "to a triage set."
            )
        ),
    ] = None,
    include_comments: Annotated[
        bool,
        Field(
            description=(
                "If true, also bulk-fetch the public comments (one extra request "
                "in total)."
            )
        ),
    ] = False,
) -> dict:
    fields, added, wants_description = upstream_fields(
        include_fields, bugzilla.GET_BUGS_DEFAULT_FIELDS
    )
    result = await bugzilla.get_bugs(ctx, ids, fields, include_comments)
    returned = {b["id"] for b in result["bugs"] if is_public(b)}
    bugs = await _serve(ctx, result["bugs"], added, wants_description)
    inaccessible = [i for i in ids if i not in returned]
    return dict(result, count=len(bugs), bugs=bugs, inaccessible=inaccessible)


async def _get_bug_comments(ctx: BugzillaContext, bug_id: int) -> dict:
    await _require_public(ctx, bug_id)
    result = await bugzilla.get_bug_comments(ctx, bug_id)
    comments = public_items(result["comments"])
    return dict(result, count=len(comments), comments=comments)


# A history change with one of these keys is about a comment or an attachment, which
# may be private.
PRIVATE_ITEM_KEYS = ("attachment_id", "comment_id")


def changed_fields(history: list[dict]) -> list[dict]:
    """Each history entry as its time, its author and the names of the fields it changed.

    The values are dropped, and so are changes to comments and attachments.
    """
    entries = []
    for e in history:
        fields = [
            c.get("field_name")
            for c in e.get("changes") or ()
            if not any(k in c for k in PRIVATE_ITEM_KEYS)
        ]
        if fields:
            entries.append(
                {"when": e.get("when"), "who": e.get("who"), "fields": fields}
            )
    return entries


async def _get_bug_history(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
    new_since: Annotated[
        str | None,
        Field(
            description="Only changes at or after this time (`2026-10-13T18:01:21Z`)."
        ),
    ] = None,
) -> dict:
    await _require_public(ctx, bug_id)
    params = {"new_since": new_since} if new_since else {}
    result = bugzilla._request(ctx, f"bug/{bug_id}/history", params)
    bugs = result.get("bugs") or [{}]
    return {"bug_id": bug_id, "history": changed_fields(bugs[0].get("history") or [])}


def point_to_fetch_attachment(note: str) -> str:
    """A `data_omitted` note with its download advice replaced."""
    cut = note.find("Use download_attachment")
    if cut == -1:
        return note
    return f"{note[:cut]}{ATTACHMENT_HINT}"


async def _get_bug_attachments(
    ctx: BugzillaContext,
    bug_id: Annotated[int, Field(description="Bug ID.")],
    include_data: Annotated[
        bool,
        Field(
            description=(
                "If true, inline base64 content for the public textual attachments "
                f"under {bugzilla.MAX_INLINE_BYTES // 1024}KiB. Images and PDFs are "
                "never inlined."
            )
        ),
    ] = False,
) -> dict:
    await _require_public(ctx, bug_id)
    result = await bugzilla.get_bug_attachments(ctx, bug_id, include_data)
    attachments = public_items(result["attachments"])
    for att in attachments:
        if "data_omitted" in att:
            att["data_omitted"] = point_to_fetch_attachment(att["data_omitted"])
    return dict(result, count=len(attachments), attachments=attachments)


GET_BUG_ATTACHMENTS_DESCRIPTION = f"""Fetch the public attachments of a public bug.

By default this returns metadata only. Set include_data=true to also inline the
content, base64-encoded in each attachment's 'data' field, for the textual types
only: text/*, JSON, XML, JavaScript and SVG, under {bugzilla.MAX_INLINE_BYTES // 1024}KiB, for the first
{bugzilla.MAX_INLINE_ATTACHMENTS}.

Any other attachment keeps its metadata and gets a 'data_omitted' note.
{ATTACHMENT_HINT}"""


# `download_attachment` is left out: it writes the file inside this container.
TOOLS = [
    _public_tool(bugzilla.search_bugs, _search_bugs),
    _public_tool(bugzilla.get_bugs, _get_bugs, own_schema=True),
    _public_tool(bugzilla.get_bug_comments, _get_bug_comments),
    _public_tool(
        bugzilla.get_bug_attachments,
        _get_bug_attachments,
        description=GET_BUG_ATTACHMENTS_DESCRIPTION,
        own_schema=True,
    ),
    ToolDefinition(
        name="get_bug_history",
        namespace="bugzilla",
        description=(
            "Who changed which fields of a public bug, and when. Gives the field names "
            "only, and no changes to comments or attachments."
        ),
        handler=off_loop(_get_bug_history),
    ),
]


class BrokerInputs(BaseSettings):
    bugzilla_api_url: str
    bugzilla_api_key: str
    host: str = "0.0.0.0"
    port: int = 8765

    model_config = SettingsConfigDict(extra="ignore")


def build_app(inputs: BrokerInputs) -> Starlette:
    client = bugsy.Bugsy(
        api_key=inputs.bugzilla_api_key, bugzilla_url=inputs.bugzilla_api_url
    )
    bound_session(client)
    ctx = BugzillaContext(client=client)
    sdk_config = build_sdk_server("bugzilla", ctx, TOOLS)
    mcp_server = sdk_config["instance"]

    manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            log.info(
                "bugzilla broker ready on %s:%d (read-only, public only)",
                inputs.host,
                inputs.port,
            )
            yield

    async def mcp_handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return Starlette(routes=[Mount("/mcp", app=mcp_handler)], lifespan=lifespan)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    inputs = BrokerInputs()
    app = build_app(inputs)
    uvicorn.run(app, host=inputs.host, port=inputs.port, log_config=None)


if __name__ == "__main__":
    main()
