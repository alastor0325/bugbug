"""The other bugs a run's writes name, checked after the session ends.

The hooks see one action at a time and cannot read Bugzilla, so a write may name any
bug. After the session, one broker read gets the `dupe_of` bug, the `regressed_by`
bugs and the `see_also` bugs. The check drops a write on any other bug. It drops the meta bug write on a
`dupe_of` bug that is closed, a security bug or outside the team's scope. It
drops a `regressed_by` or `see_also` change that names an unreadable or restricted bug,
and a `see_also` change that names a meta bug. A duplicate of
a restricted or unreadable bug fails the run, since a public bug must not point at it. A
duplicate of a meta bug also fails the run: the hooks know only the team's meta bugs and
the meta bugs among the duplicate candidates.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from hackbot_runtime import ActionsRecorder, AgentError
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import SCOPE_KEYS
from .hooks import _is_bug_id, _params
from .preflight import CLOSED_STATUSES, is_meta, is_security
from .state import _call

LINK_FIELDS = ("regressed_by", "see_also")
TARGET_FIELDS = "id,summary,groups,component,product,status,resolution,keywords"
FETCH_TIMEOUT = 60


class DupeTargetRestricted(AgentError):
    pass


class DupeTargetIsMeta(AgentError):
    pass


@dataclass(frozen=True)
class Dropped:
    """A write the check drops: one field of the action, or the whole action."""

    action_id: str
    reason: str
    field: str | None = None


def _updates(actions: list[dict]) -> list[tuple[str, object, dict]]:
    found = []
    for action in actions:
        if action.get("type") != "bugzilla.update_bug":
            continue
        params = _params(action)
        changes = params.get("changes")
        found.append(
            (
                action.get("action_id", ""),
                params.get("bug_id"),
                changes if isinstance(changes, dict) else {},
            )
        )
    return found


def dupe_of(actions: list[dict], bug: int) -> int | None:
    """The bug the triaged bug is recorded as a duplicate of, if any."""
    for _, bug_id, changes in _updates(actions):
        if bug_id == bug and changes.get("resolution") == "DUPLICATE":
            return changes.get("dupe_of")
    return None


def _linked(changes: dict, field: str) -> list[int]:
    value = changes.get(field)
    items = value.get("add") if isinstance(value, dict) else None
    return [i for i in items or () if _is_bug_id(i)]


def bugs_to_read(actions: list[dict], bug: int) -> list[int]:
    """The `dupe_of` bug and the `LINK_FIELDS` bugs of the triaged bug's change."""
    ids = set()
    for _, bug_id, changes in _updates(actions):
        if bug_id == bug:
            for field in LINK_FIELDS:
                ids.update(_linked(changes, field))
    duplicate = dupe_of(actions, bug)
    if _is_bug_id(duplicate):
        ids.add(duplicate)
    return sorted(ids)


def _unsuitable(target: dict | None) -> str | None:
    if not target:
        return "it could not be read"
    if target.get("status") in CLOSED_STATUSES:
        return "it is closed"
    if is_security(target):
        return "it is a security bug"
    key = f"{target.get('product') or ''} :: {target.get('component') or ''}"
    if key not in SCOPE_KEYS:
        return "it is out of the team's scope"
    return None


def _readable(found: Mapping[int, dict], bug: int) -> bool:
    return bug in found and not found[bug].get("groups")


def screen_writes(
    actions: list[dict], bug: int, found: Mapping[int, dict]
) -> list[Dropped]:
    """The writes to drop, given ``found``: the ``bugs_to_read`` bugs that were read.

    Raises :class:`DupeTargetRestricted` when the `dupe_of` bug is restricted or
    unreadable, and :class:`DupeTargetIsMeta` when it is a meta bug.
    """
    duplicate = dupe_of(actions, bug)
    if duplicate is not None and not _readable(found, duplicate):
        raise DupeTargetRestricted(
            f"bug {bug}: the duplicate target bug {duplicate} is restricted or "
            "could not be read; a public bug must not point at it"
        )
    if duplicate is not None and is_meta(found[duplicate]):
        raise DupeTargetIsMeta(
            f"bug {bug}: the duplicate target bug {duplicate} is a meta bug; a bug "
            "blocks a meta bug, it does not duplicate one"
        )
    dropped = []
    for action_id, bug_id, changes in _updates(actions):
        if bug_id == bug:
            for field in LINK_FIELDS:
                linked = _linked(changes, field)
                unread = [i for i in linked if not _readable(found, i)]
                if unread:
                    names = ", ".join(f"bug {i}" for i in unread)
                    reason = f"{names} could not be read or is restricted"
                elif field == "see_also" and (
                    metas := [i for i in linked if is_meta(found[i])]
                ):
                    names = ", ".join(f"bug {i}" for i in metas)
                    reason = f"{names} is a meta bug"
                else:
                    continue
                dropped.append(
                    Dropped(
                        action_id,
                        f"the {field} change on bug {bug}, since {reason}",
                        field,
                    )
                )
            continue
        # The model can remove the DUPLICATE change after this write passed its hook.
        if bug_id != duplicate:
            reason = "no recorded duplicate change names it"
        else:
            reason = _unsuitable(found.get(bug_id))
        if reason:
            dropped.append(
                Dropped(action_id, f"the change on bug {bug_id}, since {reason}")
            )
    return dropped


def drop_writes(recorder: ActionsRecorder, dropped: list[Dropped]) -> None:
    """Remove each dropped field, or the whole action when nothing else is left."""
    if not dropped:
        return
    live = {
        copy["action_id"]: action
        for copy, action in zip(recorder.list_actions(), recorder.actions)
    }
    for d in dropped:
        changes = live[d.action_id].get("params", {}).get("changes")
        if d.field is not None and isinstance(changes, dict):
            changes.pop(d.field, None)
            if changes:
                continue
        recorder.remove_action(d.action_id)


async def fetch_bugs(bugzilla_mcp_server: Mapping, ids: list[int]) -> dict[int, dict]:
    """The bugs the broker returns, by id.

    Never raises: empty when nothing could be read, on any failure, or after
    FETCH_TIMEOUT seconds.
    """
    url = bugzilla_mcp_server.get("url")
    if not url or not ids:
        return {}
    try:
        bugs = await asyncio.wait_for(_get_bugs(url, ids), FETCH_TIMEOUT)
    except Exception as e:  # see docstring
        print(
            f"[gecko_triage] reading bugs {ids} failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return {}
    wanted = set(ids)
    return {b["id"]: b for b in bugs if isinstance(b, dict) and b.get("id") in wanted}


async def _get_bugs(url: str, ids: list[int]) -> list[dict]:
    async with (
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        res = await _call(
            session, "get_bugs", {"ids": ids, "include_fields": TARGET_FIELDS}
        )
        return res.get("bugs") or []
