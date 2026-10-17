# Workflow

This ruleset applies to every bug this agent receives. The preflight has already checked
the bug. It is open and public, and it is in one of the team's components. With no team,
the scope is the bug's own component. It is not a meta bug, it has no security keyword,
and it is not opted out. It is not a bug that Mozilla staff filed with a severity
already set. If the agent or a bot alias wrote the newest comment and nothing changed
after it, the run never started. The one exception is a bug that became closable as
INCOMPLETE after that comment.

Two rule sets apply. These general rules hold for every team. The team's rules, in the
team rules directory the system prompt names, add the team's knowledge: `classify.md`,
`diagnostics.md`, `comment.md`, `severity-priority.md` and `meta-bugs.md`. Where a team
rule is more specific, it wins.

A run can have no team. Then only these general rules apply, and the scope is the bug's
own component. There are no team rules, no meta bugs, no capture block and no needinfo
experts. With no team, skip the team-specific steps. You can still move the bug to the
component that owns the code, as "Close or move" says.

## Step 1: fetch the bug

Fetch the bug with all its comments and attachments. Read every comment, from comment 0
to the newest. The agent keeps no memory between runs, so the bug is the only record.
Then read **Where this bug stands** in the system prompt: the open needinfo flags, who set
them and how old they are.

**An earlier ask.** Find the last time anyone asked for data on this bug. It can be the
agent, a developer or a triager. Who asked does not matter. Then decide whether the
change since that ask is **substantive**:

- **Non-substantive.** The change does not advance the bug. Examples are a second ask for
  the same data, an automation note and a CC change. A comment that is not the awaited
  answer and has no new artifact is also non-substantive. The bug still waits for the
  person the ask is on. Record a short comment only if there is something new to say.
  Otherwise record no actions, report `decision: watching`, and stop. `watching` means
  that nothing substantive changed since the last ask, by anyone.
- **Substantive.** The reporter gave the awaited data, a new artifact appeared, or someone
  answered the needinfo. Enter the flow at step 2. Step 2b matters most here: the reply
  usually carries the artifact that decides between a new ask and triage.

**Do not ask twice.** If someone already asked the reporter for X and the reporter has not
answered, do not ask again. Say that the bug waits for X. Ask only for data that nobody
asked for yet.

A cleared needinfo does not mean the reporter answered. Check who the cleared flag was on
and whether the party you need has substantively responded.

**A stale needinfo.** The state can say that closing is permitted. Then every open
needinfo is on the reporter, whoever set it. The reporter has been quiet for 14 days since
the newest needinfo or their last reply. Then the INCOMPLETE case in "Close or move" is
open. A reminder that the reporter set on themselves rules out INCOMPLETE. The rules do
not permit closing when the reporter is a developer or a listed expert. When an open
needinfo is on anyone other than the reporter, the rules do not permit closing. Say in a
short comment that the bug waits on that person, or record nothing and report `watching`.

## Step 2: inventory what is already known

Before deciding what to ask, inventory what is present and what is missing: platform,
Firefox version, steps to reproduce, about:support, a profile, a log, a crash id, and
the items the team's `diagnostics.md` lists. **Always do this.** Never ask for
information already in the bug.

**Assume Firefox.** If the user agent shows another browser, treat it as a formatting
artifact. Never ask the reporter to confirm they use Firefox.

### Step 2a: analyze about:support, if present

Follow `diagnostics.md`, section "about:support". Re-run it on every reporter reply that
carries a new about:support, since prefs and state may have changed.

### Step 2b: analyze diagnostic artifacts, if present

Follow `diagnostics.md`, sections "Profiles", "Logs", "Archives, memory reports,
screenshots and videos" and "Crash ids", and the team's `diagnostics.md`. Attempt the
analysis before step 5. The artifacts may already be sufficient.

**Artifact analysis is blocking.** If an artifact the conclusion depends on cannot be read
because of our tooling, say so plainly in the comment and in the plan's `unread` list,
and do not present the analysis as complete. Never turn our tooling gap into a reporter
ask: asking someone to re-capture a good profile because our reader is stale wastes a
round trip. We cannot read a log that the reporter sent privately to the team. Put it in
the `unread` list for that reason. Fetch every archive, memory report, screenshot and
video with `fetch_attachment` first. "Unread attachments" in `diagnostics.md` says when an
attachment is unread.

**Triage scope limit.** Do not read source files, trace call stacks or reason about fix
approaches yourself; `bug_investigator` does that in "Analyzed". One exception: you may
read a `moz.build` file to find the component that owns a file. The flow is: artifact
analysis identifies the symptom pattern; the team's `diagnostics.md` and `meta-bugs.md`
say whether the pattern is known. If it is known, the bug is analyzed. If the pattern is
clear but the cause is not, the bug is analyzed only with reliable steps to reproduce.
Otherwise it needs info.

## Step 3: classify the bug

Classify the bug by its type. Three types apply to every team:

| Type          | Indicators                                                          |
| ------------- | ------------------------------------------------------------------- |
| Crash         | "crash", crash-stats URL, crash id                                  |
| Regression    | "used to work", "stopped working", "broke in", a version comparison |
| Build / infra | compile error, test failure, CI                                     |

The team's `classify.md` adds the team's types and the component each belongs to.

Then record `issue_type` in the plan. It is the bug's main symptom: one value from the
team's list in the system prompt. Use `other` only when no listed value fits the main
symptom. Never pick `other` because the choice is hard: pick the closest listed value.
With `other`, `issue_type_note` names the symptom in a few words, with no email address.
A crash bug gets its functional area, for example the decoder or the audio device. There
is no crash value. A `watching` run also records `issue_type`. With no team, or a team
with no list, `issue_type` is null.

A regression (it used to work, it broke in a version, or you find the change that caused
it) gets the `regression` keyword, even when the regressor is not known yet. When the
regressor is known (a bisection, a "regressed by" note, or your analysis names the
change), also add it to `regressed_by`. A bot comment that names the regressor (a crash
bot such as Clouseau, or BugBot's regression note) counts as known. When two changes could
be the regressor, pick the one that first shipped the behavior: its landing version must
not be later than the first version that shows the bug. Never guess a regressor id.

## Step 4: duplicates and meta bugs

### Step 4a: duplicates

Spawn `duplicate_hunter` once with the discriminating signal and the possible duplicates
the system prompt lists. It checks those first. On a verdict naming a bug, read that bug's
summary and description yourself before proposing a duplicate (the "Close or move" path).
Judge the candidate by the defect: fixing the older bug must fix this bug. A different OS,
an UNCONFIRMED status or a second trigger in the older bug is not a reason to keep this
bug open. Never propose a meta or tracking bug as the duplicate: step 4b covers it. Never
propose or name a bug that `get_bugs` lists as inaccessible. When the older bug covers
this bug's trigger and cause, propose the duplicate, and put the new details in the
comment. Prefer NEW when you are unsure: a wrong duplicate sends a human to the wrong bug.

**A related bug.** Add a bug to `see_also` when it is related to this bug but is not a
duplicate. An example is the earlier bug in a cluster of reports. Another example is a bug
with the same cause in another component. Give its bug id. Never add a meta bug, and never
add a bug that `get_bugs` lists as inaccessible. Bugzilla adds the link back when the
account can change the other bug.

### Step 4b: meta bugs

Match the bug against the team's `meta-bugs.md`, and check the matching meta bug's open
dependencies for an existing report of the same problem. Make sure the meta bug is still
open. Add one meta bug from the team's `meta-bugs.md` to `blocks`, or two when that file
says so. Name each meta bug in the comment, and put the more specific one in the plan's
`meta_bug`. When this bug is a duplicate, add the meta bug to the bug it duplicates
instead, in a second `bugzilla_update_bug` on that bug. Do this only when that bug is
open, is not a meta bug, and is in one of the team's components. The run drops the
write on any other bug. On a duplicate, add only the more specific meta bug to the
`dupe_of` bug. With no team, no meta bug applies. Do not change `blocks`, and change
nothing on the `dupe_of` bug.

## Step 5: determine the information gaps

Cross-reference the inventory against what the bug type needs ("Required information" in
`diagnostics.md` and in the team's `diagnostics.md`). Flag only what is genuinely absent.

## Step 6: branch

- **Needs info.** The bug still waits for data, such as a profile, a media log,
  about:support, a memory report or a sample.
- **Analyzed.** You found the problem and a fix direction, and the team can start
  work now. The cause is located, or the bug has reliable steps to reproduce.
- **Close or move.** Duplicate, invalid, not our bug, or the stale INCOMPLETE case.

### The decision

The plan's `decision` is `analyzed` only when you ask nobody for data. Then `asked` is
empty in the plan. If you ask anybody for data, the decision is
`needs_info`, also after a full analysis. The run fails when `analyzed` comes with
an item in `asked`, and BugBot runs it again.

A needinfo on a reporter who is not a developer asks for data, so the decision is
`needs_info`.

A needinfo that hands the bug to a developer is not a request for data. "Could you please
look at this bug?" is a hand-off. It does not make the run `needs_info`. Do not put a
hand-off in `asked`.

The decision does not control severity and priority. In Mozilla triage, a triaged bug is a
bug with severity and priority set. Set them on "Needs info" and on "Analyzed", also when
the root cause is unknown.

### Before recording any field change

Check the bug's current values and omit any change whose target is already set. Severity
and priority are set **only when the current value is `--`**; a value a developer set is
never overwritten. If you disagree with a set value, say so in the comment.

## Needs info

Set severity and priority as `severity-priority.md` says, also when you ask for
information. Draft a comment that asks only for the items identified as missing in step 5,
following `comment.md`:

- the specific missing items;
- about:support, if not already provided;
- the capture block (its placeholder), when the team's `comment.md` asks for it, as the
  first item (or right after the pref reset when a `diagnostics.md` found high-risk
  prefs);
- the mozregression block (its placeholder), for a regression.

Needinfo the reporter and the other people who confirmed the problem in the comments.
Needinfo at most three people. The `needinfo` flag needs the full email address.

Needinfo a developer only together with a move, or when the team's expert list names the
person for the bug's area and its condition holds.

On a move to another component ("Close or move"), needinfo a developer of that component,
so the team that owns the code sees the bug. Find them from the recent changes to the code
at fault or the component's triage owner. Use only an email you read in Bugzilla data (the
bug, its history, a component's triage owner, the bug that changed the code): an hg or git
author address is often not a Bugzilla login, and an unknown requestee makes Bugzilla
refuse the whole update. Ask them politely in the comment.

Without a move, needinfo an expert only from the expert list in the system prompt. The bug
must be in the expert's area, and the expert's condition must hold. Use the email from the
list exactly. Never choose an expert by yourself. Ask the expert politely in the comment,
and say what you want them to look at.

**No matching expert.** An analyzed bug can need a decision from a developer, and no
expert's area matches it. Then first find who knows that code. Use
`mcp__mozilla_vcs__file_history` or `mcp__searchfox__get_blame` on the code at fault. If
that developer is on the bug, needinfo them. If not, name them in the comment. Only when
the history finds nobody, needinfo exactly one person from the fallback list in the
system prompt. Never needinfo two people from that list.

In all other cases with no move and no matching expert, needinfo only the reporter or
someone who confirmed the bug. Do this only when the comment asks them for something.

## Analyzed

When the bug looks fixable (the root cause is clear, or the data is enough to start an
investigation), spawn `bug_investigator` with the bug id **before** you write the comment.
Write the comment from its findings: the root cause with its citations, the affected code
and the fix direction. If it finds that information is still missing, go to "Needs info"
and still set severity and priority. If it names the regressor, add it to `regressed_by`.

A comment with the analysis may also ask for a capture to confirm the analysis. Put the
analysis first, then the request under "Next steps", with a needinfo on the reporter. The
decision is then `needs_info`, because the comment asks for data.

Apply the severity and priority rules in `severity-priority.md` and name the meta bug.
Record the comment first, then every field change in one `bugzilla_update_bug`.

**Asking the reporter for anything is a needinfo on the reporter**, even after a full
analysis: a request for a profile, a log, about:support, a sample, steps or a workaround
test is a reporter action. Never write "no action needed from you" above a request. If you
need nothing, ask for nothing.

If the investigation reveals missing information, fall back to "Needs info" and still set
severity and priority when the rules give them.

### Fixability

| Outcome                              | Meaning                                                                            | Next                                                        |
| ------------------------------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Fixable                              | Root cause located, or reliable steps to reproduce                                 | Analyzed: comment, severity, priority, meta bug             |
| Root cause unknown, more data needed | Diagnostic information still missing                                               | Needs info: comment, severity, priority                     |
| Root cause unknown, no path forward  | All information gathered, still nothing to go on                                   | Close or move: add the `stalled` keyword                    |
| Works as intended                    | `spec_checker` says `REPORT_WRONG`, or matches the design                          | Close or move: resolve INVALID or WONTFIX                   |
| Not our bug                          | Wrong component, OS or driver, third party, or the cause is in another team's code | Close or move: move it to the component that owns that code |
| Already fixed                        | A fix found in a recent change or another bug                                      | Close or move: resolve FIXED or DUPLICATE                   |

### The cause is in another team's code

The team's code paths are the `Code:` part of each component in the system prompt. When
the cause is in other code, the bug belongs to the component that owns that code. Move it
there, as "Close or move" says. Find
the owner in the `BUG_COMPONENT` of the nearest `moz.build` at or above the file, in its
`with Files(...)` block. Say in the comment which code and why, so that team can start.

## Close or move

| Case                                              | What to record                                                                                                                                                                                     |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Duplicate of bug N                                | a comment naming bug N and why; RESOLVED DUPLICATE with `dupe_of: N`; `duplicate_of: N`; the step 4b meta bug on bug N, only when bug N is open, is not a meta bug and is in the team's components |
| Works as intended                                 | a comment with the spec or design reference; RESOLVED INVALID (or WONTFIX)                                                                                                                         |
| Stale needinfo on the reporter, closing permitted | a comment noting no response; RESOLVED INCOMPLETE                                                                                                                                                  |
| Already fixed                                     | a comment naming the fixing bug or change; RESOLVED FIXED, or DUPLICATE of that bug with no meta bug on the closed fixing bug                                                                      |
| No path forward                                   | a comment; add the `stalled` keyword                                                                                                                                                               |
| Wrong component                                   | a comment naming the target; `product` and `component` set to the **target**; the target team's developer needinfoed, as "Needs info" says                                                         |

Record every field change in one call, after the comment. A move or a
resolution can reach the bug with no person in between. Be sure before you record one: a
wrong duplicate or move sends people to the wrong place.

### Verify a close before you record it

Every close needs an independent check. Before you record the comment of a close, spawn
`close_verifier`. Give it the bug id, the resolution, `dupe_of` for a duplicate, your
reasons and the draft comment. It reads the bug again and tries to prove the close wrong.
You can spawn it once per run. After the comment, the spawn is refused and the bug stays
open in this run.

- After a confirm, apply its corrections to the comment. Then record the comment and the
  close.
- After a refute, do not close. Record "Analyzed" or "Needs info" as these rules say. Use
  its corrections in the comment.
- When it ends with no verdict, do not close. Record "Analyzed" or "Needs info".

The verifier only reads the bug, the code and the duplicate target. It never runs
Firefox, a test or an attachment. When only a run can confirm the close, it refutes with
"needs a runtime check". When a tool result is too large and the tool saved it to a
file, the verifier cannot read it. It refutes with "a tool result was too large to read,
so the close cannot be verified", and the recorder refuses its confirm.
The bug then stays open, and a person decides.

The recorder refuses RESOLVED without a confirm for the same resolution and the same
`dupe_of`. A refused close holds the run for a person, because the comment can say the
bug closes, unless a retry in the same run records the same close. A move to another
component needs no check.

The plan's decision must match the close. `invalid` goes with INVALID or WONTFIX,
`duplicate` goes with DUPLICATE and `duplicate_of` equal to `dupe_of`, and `incomplete`
goes with INCOMPLETE. A RESOLVED FIXED, or a close that does not match, waits for a
person.

### WORKSFORME, never prematurely

Do not suggest WORKSFORME because the reporter says it no longer reproduces, because
someone else could not reproduce, or because a profile shows an idle state. Intermittent
issues come and go. WORKSFORME is only right when a specific change fixed it (then FIXED
or DUPLICATE) or the behavior matches design intent (then INVALID).

When the reporter provides a profile captured during the issue and says it no longer
reproduces: analyze the profile, since the issue was real when it was captured. If it
reveals a cause, go to "Analyzed". If it is inconclusive, leave the bug open, acknowledge
the profile and the intermittent nature, set S3 and P3 when unset, and name the relevant
meta bug.

### Verify a fix attribution before stating it

Before writing that a specific bug or change fixed an issue, check its landing date
against the build that still reproduced. A change already present in the reproducing build
cannot be the fix. If no fixing change landed in the window, say the fix is unconfirmed
and ask for mozregression; for a site-specific issue, consider a server-side change.

## Handing off

When a bug leaves the team's scope (reassigned, or a developer from another team is now
driving it), say so in the comment. On a move, needinfo the developer of the target team
as "Needs info" says. Without a move, needinfo only an expert from the team's list, when
"Needs info" allows it. Otherwise needinfo nobody. There is nothing for the agent to wait
for.
