# bug-fix agent

The bug-fix agent investigates a Bugzilla bug, fixes it in the Firefox source tree, and
records the actions a person or hackbot-api then applies: a Phabricator patch, a try
push, a Bugzilla or Phabricator comment. It never posts anything itself.

A run starts from one of three modes: fix a bug, answer `@hackbot` comments on a
revision, or answer a Bugzilla needinfo. A run whose bug belongs to a team also follows
that team's rules and must pass a loop of checks before it may record a patch. See
[teams/README.md](hackbot_agents/bug_fix/teams/README.md) to add a team.

## Flow

```mermaid
flowchart TD
    start([Run starts: bug, mode, optional TEAM]) --> prep
    subgraph prep [Before the session]
        direction LR
        checkout[Check out the source tree<br/>or the revision to update]
        lookup[Read the bug's product and component<br/>through the broker]
    end
    prep --> pick{TEAM set, or a team<br/>lists the component?}

    pick -- no --> plain[General prompt only:<br/>system.md and the mode prompt]
    plain --> work[Investigate, reproduce, fix and test]
    work --> record_plain[Record actions:<br/>patch, try push or comment]
    record_plain --> done([Write summary.json<br/>with the recorded actions])

    pick -- yes --> teamprompt[General prompt plus the team section:<br/>team rules and the loop]
    teamprompt --> platform{Can the bug occur on<br/>the runner's platform?}
    platform -- no --> short[Record a short comment<br/>that names the platform the bug needs]
    short --> done
    platform -- yes --> tdd[1. Test first:<br/>test_fails, then fix, then test_passes]
    tdd --> verify[2. Verify: format, lint, build]
    verify --> review[3. Reviewer subagent reviews the diff<br/>against team rules and review_docs]
    review --> blocking{Blocking findings?}
    blocking -- yes, below the round limit --> fix[Fix the findings] --> verify
    blocking -- yes, at the round limit --> assoc
    blocking -- no --> assoc[4. Associated tests:<br/>mach test --auto and related gtests]
    assoc --> broke{Did the patch<br/>break a test?}
    broke -- yes --> fix
    broke -- no --> guard{Submit guard: every gate<br/>passed on the current diff?}
    guard -- no --> back[Refused: the agent is told<br/>which gate is missing] --> verify
    guard -- yes --> sent[Patch recorded,<br/>gates kept as they were]
    sent --> same{Tree unchanged<br/>at the end of the run?}
    same -- no --> fail([Run fails: nothing is applied])
    same -- yes --> evidence[Record the test comment: one row per check,<br/>plus the open findings if review did not finish]
    evidence --> done
```

A run that updates an existing revision skips step 1. Any change after step 2 repeats
steps 2 to 4. The round limit is 3 by default, and a team can change it with
`max_review_rounds`. At the limit the patch still needs its tests and checks to pass;
only the review may stay open, and the comment lists its findings for a person.
