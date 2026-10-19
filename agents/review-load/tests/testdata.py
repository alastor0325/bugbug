"""Conduit-shaped responses with fake people and PHIDs."""

from datetime import UTC, datetime

GROUP = "PHID-PROJ-fakegroup0000000000"
ALICE = "PHID-USER-alice000000000000000"
BOB = "PHID-USER-bob00000000000000000"
CAROL = "PHID-USER-carol000000000000000"
OUTSIDER = "PHID-USER-outsider000000000000"


def ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def revision(
    rev_id: int,
    *,
    author: str = CAROL,
    reviewers: dict[str, str] | None = None,
    created: int | None = None,
    status: str = "needs-review",
    draft: bool = False,
    bug: str | None = "1900000",
) -> dict:
    return {
        "id": rev_id,
        "type": "DREV",
        "phid": f"PHID-DREV-{rev_id:020d}",
        "fields": {
            "title": f"Bug {bug} - Change {rev_id}",
            "authorPHID": author,
            "status": {"value": status, "name": status},
            "isDraft": draft,
            "dateCreated": created if created is not None else ts(2026, 10, 1),
            "bugzilla.bug-id": bug,
        },
        "attachments": {
            "reviewers": {
                "reviewers": [
                    {"reviewerPHID": phid, "status": st, "isBlocking": st == "blocking"}
                    for phid, st in (reviewers or {GROUP: "added"}).items()
                ]
            }
        },
    }


def add_reviewers(when: int, *phids: str) -> dict:
    return {
        "type": "reviewers",
        "dateCreated": when,
        "fields": {
            "operations": [
                {"operation": "add", "phid": p, "newStatus": "added"} for p in phids
            ]
        },
    }


def txn(kind: str | None, when: int) -> dict:
    return {"type": kind, "dateCreated": when, "fields": {}}
