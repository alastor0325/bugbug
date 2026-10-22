"""The test-evidence comment a team run puts on its revision.

Code builds the comment from the gates as the submit guard saw them when it let the
patch through: the evidence the agent recorded with ``record_gate``, and the review
rounds that a hook took from the reviewer's own results. It is recorded after the
patch action, and the patch action carries a ``ref``, so the comment reaches the new
revision through ``{{actions.<ref>.revision_id}}`` when hackbot-api applies the actions
in order.
"""

from hackbot_runtime import ActionsRecorder

from .gates import NOT_CHECKED, Gates

INTRO = (
    "Hackbot did these checks before it sent this patch. The results are as Hackbot "
    "recorded them. A separate reviewer agent did the review."
)
TRY_NOTE = "The try push is in the test plan."
PATCH_REF = "patch"
TRY_REF = "try"
_NOT_RECORDED = "Not recorded."


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def _count(n: int | None, word: str) -> str:
    if n is None:
        return f"unknown {word}s"
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _review(gates: Gates) -> str:
    if not gates.reviews:
        return _NOT_RECORDED
    rounds = " ".join(
        f"Round {i}: {_count(r.blocking, 'blocking finding')}."
        for i, r in enumerate(gates.reviews, 1)
    )
    text = f"{_count(len(gates.reviews), 'round')}. {rounds}"
    if nits := gates.reviews[-1].nits:
        text += " Small findings that Hackbot did not apply: " + " ".join(nits)
    return text


def render(gates: Gates, platform: str, *, try_pushed: bool) -> str:
    """The comment: an introduction and a table with one row per gate."""

    def evidence(step: str) -> str:
        return gates.evidence.get(step) or _NOT_RECORDED

    not_checked = gates.evidence.get(NOT_CHECKED) or "Nothing recorded."
    if try_pushed:
        not_checked += " " + TRY_NOTE
    rows = [
        ("Check", "Result"),
        ("Platform", f"Hackbot ran on {platform}."),
        ("Test fails first", evidence("test_fails")),
        ("Test passes", evidence("test_passes")),
        ("Review", _review(gates)),
        ("Verify", evidence("verify")),
        ("Associated tests", evidence("associated_tests")),
        ("Not checked", not_checked),
    ]
    rows = [(label, _cell(result)) for label, result in rows]
    w0 = max(len(label) for label, _ in rows)
    w1 = max(len(result) for _, result in rows)
    lines = [f"| {a.ljust(w0)} | {b.ljust(w1)} |" for a, b in rows]
    lines.insert(1, f"| {'-' * w0} | {'-' * w1} |")
    return INTRO + "\n\n" + "\n".join(lines)


def label_refs(recorder: ActionsRecorder) -> None:
    """Give the patch and try actions a ``ref`` when the agent sets none."""
    recorder.add_hook(
        "phabricator.submit_patch", lambda a: a.setdefault("ref", PATCH_REF)
    )
    recorder.add_hook("try_server.push", lambda a: a.setdefault("ref", TRY_REF))


def record_test_comment(recorder: ActionsRecorder, gates: Gates, platform: str) -> None:
    """Record the comment on the run's revision, if the guard let a patch through."""
    if gates.sent is None:
        return
    target = None
    try_pushed = False
    for action in recorder.actions:
        if action["type"] == "phabricator.submit_patch":
            target = f"D{{{{actions.{action['ref']}.revision_id}}}}"
        elif action["type"] == "phabricator.update_patch":
            target = f"D{action['params']['revision_id']}"
        elif action["type"] == "try_server.push":
            try_pushed = True
    if target is None:
        return
    recorder.record(
        "phabricator.add_comment",
        {
            "revision_id": target,
            "text": render(gates.sent, platform, try_pushed=try_pushed),
        },
        reasoning="The checks the team loop ran, from the gates at submit time.",
    )
