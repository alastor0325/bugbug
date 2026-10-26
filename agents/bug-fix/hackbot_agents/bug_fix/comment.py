"""The checks a team run did, as a table on its revision.

Code builds the table from the gates as the submit guard saw them when it let the
patch through: the evidence the agent recorded with ``record_gate``, and the review
rounds that a hook took from the reviewer's own results. For a new revision, a recorder
hook adds the table to the patch's test plan, under what the agent wrote there. An
update cannot change the test plan, so the table goes on the revision as a comment.
"""

from hackbot_runtime import ActionsRecorder

from .gates import NOT_CHECKED, Gates

INTRO = (
    "Hackbot did these checks before it sent this patch. The results are as Hackbot "
    "recorded them. A separate reviewer agent did the review."
)
PATCH_REF = "patch"
TRY_REF = "try"
_NOT_RECORDED = "Not recorded."
MAX_NITS = 3
MAX_NIT_LENGTH = 160


def _cell(text: str) -> str:
    # A pipe ends a Remarkup table cell, so a broken bar stands in for it.
    return " ".join(text.replace("|", "\u00a6").split())


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
    if gates.review_open():
        text += (
            f" Review did not finish after {_count(gates.max_review_rounds, 'round')}."
        )
    if nits := gates.reviews[-1].nits:
        text += " Small findings that Hackbot did not apply: " + _short(nits)
    return text


def _short(findings: tuple[str, ...]) -> str:
    shown = [
        f if len(f) <= MAX_NIT_LENGTH else f[: MAX_NIT_LENGTH - 3] + "..."
        for f in findings[:MAX_NITS]
    ]
    text = " ".join(shown)
    if len(findings) > MAX_NITS:
        text += f" And {len(findings) - MAX_NITS} more."
    return text


def render(gates: Gates, platform: str, *, try_url: str | None) -> str:
    """The comment: an introduction and a table with one row per gate."""

    def evidence(step: str) -> str:
        return gates.evidence.get(step) or _NOT_RECORDED

    not_checked = gates.evidence.get(NOT_CHECKED) or "Nothing recorded."
    if try_url:
        not_checked += f" Try push: {try_url}"
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
    if gates.review_open():
        open_findings = _short(gates.reviews[-1].blockers) or "Not recorded."
        rows.insert(
            5,
            (
                "Open findings",
                f"{open_findings} These need a person's decision before this patch "
                "can land.",
            ),
        )
    root = f"{gates.repo}/" if gates.repo else None
    rows = [
        (label, _cell(result.replace(root, "") if root else result))
        for label, result in rows
    ]
    w0 = max(len(label) for label, _ in rows)
    w1 = max(len(result) for _, result in rows)
    lines = [f"| {a.ljust(w0)} | {b.ljust(w1)} |" for a, b in rows]
    lines.insert(1, f"| {'-' * w0} | {'-' * w1} |")
    return INTRO + "\n\n" + "\n".join(lines)


def _numbered_ref(base: str):
    """A recorder hook that gives each action without a ref a unique one."""
    count = 0

    def hook(action: dict) -> None:
        nonlocal count
        if "ref" not in action:
            count += 1
            action["ref"] = base if count == 1 else f"{base}-{count}"

    return hook


def _try_url(recorder: ActionsRecorder) -> str | None:
    """The link to the last try push the run recorded, as an action placeholder."""
    url = None
    for action in recorder.actions:
        if action["type"] == "try_server.push":
            url = f"{{{{actions.{action['ref']}.url}}}}"
    return url


def attach(recorder: ActionsRecorder, gates: Gates, platform: str) -> None:
    """Label the patch and try actions, and add the checks to a new patch's test plan.

    Each patch and try action without a ``ref`` gets a unique one, so later actions can
    point at it.
    """
    recorder.add_hook("phabricator.submit_patch", _numbered_ref(PATCH_REF))
    recorder.add_hook("try_server.push", _numbered_ref(TRY_REF))

    def add_checks(action: dict) -> None:
        if gates.sent is None:
            return
        checks = render(gates.sent, platform, try_url=_try_url(recorder))
        test_plan = (action["params"].get("test_plan") or "").rstrip()
        action["params"]["test_plan"] = (
            f"{test_plan}\n\n{checks}" if test_plan else checks
        )

    recorder.add_hook("phabricator.submit_patch", add_checks)


def record_test_comment(recorder: ActionsRecorder, gates: Gates, platform: str) -> None:
    """Record the checks as a comment on a revision the run updated.

    A new revision already has them in its test plan. The comment goes on the last
    revision the run updated, if the guard let the update through.
    """
    if gates.sent is None:
        return
    target = None
    for action in recorder.actions:
        if action["type"] == "phabricator.update_patch":
            target = action["params"]["revision_id"]
    if target is None:
        return
    recorder.record(
        "phabricator.add_comment",
        {
            "revision_id": target,
            "text": render(gates.sent, platform, try_url=_try_url(recorder)),
        },
        reasoning="The checks the team loop ran, from the gates at submit time.",
    )
