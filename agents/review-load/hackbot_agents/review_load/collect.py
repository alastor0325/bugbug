"""Read Phabricator and Bugzilla through the broker."""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable

import httpx
from hackbot_runtime import AgentError
from phabricator_client import PhabricatorClient

from .conduit import Revision, parse_revision, parse_transactions
from .config import ReviewLoadConfig
from .load import AuthorKind, classify_author, pending_reviewers

logger = logging.getLogger(__name__)

# Bugzilla user id -> login email.
EmailLookup = Callable[[list[int]], Awaitable[dict[int, str]]]
# The waiting revisions, the watched reviewers (PHID -> label) and each author's kind.
Inputs = tuple[list[Revision], dict[str, str], dict[str, AuthorKind]]

_CONCURRENCY = 8


def broker_email_lookup(
    broker_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> EmailLookup:
    """Read login emails from the broker's ``/bugzilla/emails`` route."""

    async def lookup(ids: list[int]) -> dict[int, str]:
        async with httpx.AsyncClient(transport=transport, timeout=60) as client:
            response = await client.get(
                f"{broker_url}/bugzilla/emails", params={"ids": ids}
            )
            response.raise_for_status()
        return {int(k): v for k, v in response.json()["emails"].items()}

    return lookup


class Collector:
    def __init__(self, phabricator: PhabricatorClient, emails: EmailLookup):
        self.phabricator = phabricator
        self.emails = emails

    async def group_phid(self, slug: str) -> str:
        found = await self.phabricator.search_all(
            "project.search", constraints={"slugs": [slug]}
        )
        if not found:
            raise AgentError(f"No Phabricator project with the slug {slug!r}")
        return found[0]["phid"]

    async def usernames(self, constraint: str, values: list[str]) -> dict[str, str]:
        """PHID -> username, for ``user.search`` on ``usernames`` or ``phids``."""
        if not values:
            return {}
        users = await self.phabricator.search_all(
            "user.search", constraints={constraint: values}
        )
        return {u["phid"]: u["fields"]["username"] for u in users}

    async def author_emails(self, user_phids: list[str]) -> dict[str, str]:
        """PHID -> Bugzilla login email, for users with a linked Bugzilla account."""
        if not user_phids:
            return {}
        accounts = await self.phabricator.conduit_request(
            "bugzilla.account.search", phids=user_phids
        )
        bugzilla_ids = {a["phid"]: int(a["id"]) for a in accounts or []}
        if not bugzilla_ids:
            return {}
        emails = await self.emails(sorted(set(bugzilla_ids.values())))
        return {
            phid: emails[bz_id]
            for phid, bz_id in bugzilla_ids.items()
            if bz_id in emails
        }


async def collect_inputs(config: ReviewLoadConfig, collector: Collector) -> Inputs:
    group, members = await asyncio.gather(
        collector.group_phid(config.review_group),
        collector.usernames("usernames", list(config.members)),
    )
    missing = set(config.members) - set(members.values())
    if missing:
        logger.warning("Not on Phabricator: %s", ", ".join(sorted(missing)))
    by_handle = {handle: phid for phid, handle in members.items()}
    watched = {group: config.group_label}
    watched |= {by_handle[h]: h for h in config.members if h in by_handle}

    raw = await collector.phabricator.search_all(
        "differential.revision.search",
        constraints={"reviewerPHIDs": list(watched), "statuses": ["needs-review"]},
        attachments={"reviewers": True},
    )
    # The reviewerPHIDs constraint matches any reviewer state, so drop the
    # revisions that wait on no watched reviewer before reading their history.
    candidates = [
        rev
        for rev in (parse_revision(item, []) for item in raw)
        if pending_reviewers(rev, watched)
    ]
    limit = asyncio.Semaphore(_CONCURRENCY)

    async def with_transactions(rev: Revision) -> Revision:
        async with limit:
            txns = await collector.phabricator.search_all(
                "transaction.search", objectIdentifier=rev.phid
            )
        return dataclasses.replace(rev, transactions=tuple(parse_transactions(txns)))

    revisions = list(await asyncio.gather(*map(with_transactions, candidates)))

    authors = sorted({r.author_phid for r in revisions})
    handles, emails = await asyncio.gather(
        collector.usernames("phids", authors), collector.author_emails(authors)
    )
    kinds = {
        phid: classify_author(handles.get(phid), emails.get(phid), config.authors)
        for phid in authors
    }
    return revisions, watched, kinds
