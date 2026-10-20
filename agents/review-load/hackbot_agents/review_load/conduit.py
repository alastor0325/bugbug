"""Parse Conduit responses into the plain records the counting works on, and link back to Phabricator."""

from dataclasses import dataclass
from datetime import UTC, datetime

from phabricator_client import PhabricatorSettings

# The agent's own client points at the broker; links go to Phabricator itself.
PHABRICATOR_URL = PhabricatorSettings.model_fields["url"].default


def revision_url(revision_id: int) -> str:
    return f"{PHABRICATOR_URL}/D{revision_id}"


def revisions_url(revision_ids: list[int]) -> str:
    """One Phabricator search listing ``revision_ids``."""
    return f"{PHABRICATOR_URL}/differential/?ids={','.join(map(str, revision_ids))}"


@dataclass(frozen=True)
class Transaction:
    type: str
    created: datetime
    added_reviewers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Revision:
    id: int
    phid: str
    title: str
    bug_id: int | None
    author_phid: str
    status: str
    is_draft: bool
    created: datetime
    # Reviewer PHID -> status on this revision ("added", "accepted", ...).
    reviewers: dict[str, str]
    transactions: tuple[Transaction, ...] = ()


def _time(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def parse_transactions(raw: list[dict]) -> list[Transaction]:
    """``transaction.search`` data. Transactions that Conduit does not type are dropped."""
    parsed = []
    for item in raw:
        if not item.get("type"):
            continue
        added = frozenset(
            op["phid"]
            for op in (item.get("fields") or {}).get("operations") or []
            if op.get("operation") == "add"
        )
        parsed.append(Transaction(item["type"], _time(item["dateCreated"]), added))
    return parsed


def parse_revision(raw: dict, transactions: list[dict]) -> Revision:
    """One ``differential.revision.search`` result, with the ``reviewers`` attachment."""
    fields = raw["fields"]
    bug = fields.get("bugzilla.bug-id")
    reviewers = raw.get("attachments", {}).get("reviewers", {}).get("reviewers", [])
    return Revision(
        id=raw["id"],
        phid=raw["phid"],
        title=fields["title"],
        bug_id=int(bug) if bug else None,
        author_phid=fields["authorPHID"],
        status=fields["status"]["value"],
        is_draft=bool(fields.get("isDraft")),
        created=_time(fields["dateCreated"]),
        reviewers={r["reviewerPHID"]: r["status"] for r in reviewers},
        transactions=tuple(parse_transactions(transactions)),
    )
