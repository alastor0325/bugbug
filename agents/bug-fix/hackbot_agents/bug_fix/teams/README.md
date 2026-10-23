# Teams

A team gives the bug-fix agent its own rules for bugs in the team's code. A run uses a
team when the bug's product and component are listed in that team's `team.toml`, or
when the `TEAM` input names the team. A run with no team works as it does without this
folder.

## Add a team

Copy `media/` to `<team>/`. The name is lower-case letters, digits and `-`.

```
<team>/
  team.toml                # who the team is and which bugs are its own
  rules/implementation.md  # how the team wants a patch written and tested
```

`team.toml`:

```toml
label = "Firefox Audio/Video"               # the team's name in the prompt
review_docs = ["dom/media/docs/review/*.md"] # extra checklists the reviewer reads (globs)
max_review_rounds = 3                       # optional; 3 is the default

[[components]]                              # one table per Bugzilla component
product = "Core"
component = "Audio/Video"
```

The reviewer always reads the rules in Firefox's `review-context.toml` that match the
changed files, the same rules that bugbug's patch review uses. List a checklist in
`review_docs` only while `review-context.toml` does not cover it yet.

The match on product and component is exact. A component that is not listed gets no
team.

`rules/implementation.md` is plain text that is added to the system prompt, after the
general rules. When the two conflict, the team rules win. Keep it short: list only what
a reviewer of the team would ask a patch to change.

## What a team run does

A team run must go through this loop before it may record a patch. Hooks enforce it,
and they refuse the patch actions until every step passes on the current diff:

1. Platform check: stop with a short comment if the bug cannot occur on the platform
   the run is on.
2. A new test fails without the fix, then passes with it. A run that updates an
   existing revision skips this step.
3. Verify: format, lint and build.
4. A read-only `reviewer` subagent reviews the diff against the team rules and
   `review_docs`, in a fresh context.
5. Associated tests: the tests that cover the changed code pass, or fail the same way
   without the patch.

Any change after step 3 repeats steps 3 to 5. After `max_review_rounds` review rounds
with blocking findings, the patch goes out as it is, and the checks table
lists the open findings for a person to decide. Its tests and checks must still pass. If the tree changes after the
guard lets the patch through, the run fails and nothing is sent.

A table of these checks goes into the revision's test plan, under the agent's own text.
For an update to an existing revision, it goes on the revision as a comment. The code
is in `gates.py` and `comment.py`, and the loop text is in `prompts/team.md`. The whole
run, with and without a team, is drawn in the [bug-fix README](../../../README.md).
