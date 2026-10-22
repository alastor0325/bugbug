## Find the cause

- Fix the cause, not the symptom. If an assertion breaks, find why the state became
  reachable. Do not add a guard that tolerates it.
- For web-observable behaviour, follow the spec steps. Fix the code where Firefox departs
  from the spec, and keep the assertion.
- If no real flow reaches a condition, assert it with `MOZ_ASSERT`. Do not add a silent
  clamp or a warning log.

## Test first

- If the new test does not fail on the unfixed tree, change the test.
- If the bug has a testcase or a media file attached, test with it.
- Drive the test through real user paths and assert the exact observable value. Do not
  call internal APIs, and do not use a tolerance or a mean.
- Run the whole test directory of the component, not only the test you changed.

## Comments

- Write few comments: only for logic that is not obvious and for the intent of a class or
  member. Never repeat what the code says.
- A comment says what the code means. It does not name callers, the call order or the
  history of the patch.
- Do not add, change or remove comments on lines the patch does not change.

## Scope of the diff

- Change only what the fix needs.
- Do not move an existing function. Change it where it is.
- Commit the test after the fix, as its own commit. The revision can hold both commits.

## Commit message

- Title: `Bug NNNNNN - <what the patch achieves>`, with no reviewer. Never "Address
  review comments".
- Body: a few short sentences of what and why, at the level of behaviour. Do not list
  variable or function names.
- Never use `--` or an em dash as an aside. Use a comma, a colon or two sentences.
