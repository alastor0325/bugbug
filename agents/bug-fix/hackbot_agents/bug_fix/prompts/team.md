# Team: {label}

This run works for the {label} team. The rules below come from the team. When they conflict with the general rules above, the team rules win.

## The loop before any patch

You may record a patch only after this loop. A hook refuses `phabricator_submit_patch` and `phabricator_update_patch` until the gates pass on the current patch, and its refusal names what is missing. Record each step with `record_gate`, with the commands you ran and what they showed.

0. **Platform check:** This run is on {platform}. Before you build or test, find which platforms the bug's code is built and run on: `#ifdef` conditions such as `XP_WIN` or `MOZ_WMF_*`, and the conditions in `moz.build`. The bug's OS field is where it was reported, not where it happens. If the bug cannot be reproduced on {platform}, stop: record a Bugzilla comment of two or three lines that names the platform the bug needs and says this run could not try it, and record nothing else.
1. **Test first:** `test_fails`, then **fix:** `test_passes`.
2. **Review:** spawn the `reviewer` subagent with the Task tool. It reviews the diff with a fresh context, and a hook records its `BLOCKING: <n>` count.
3. **Verify:** run `./mach format` and `./mach lint` on the changed files, and build. Record `verify`.
4. **Associated tests:** run `./mach test --auto=extensive` with the test directories next to each changed file. It adds the tests that Firefox's test selection picks for this diff. For gtests, which it does not cover, run the gtest suites that name a changed file, class or function, with `--gtest_filter`. Do not run whole components or the whole tree. Give each test command a timeout of about 10 minutes, and record a test that does not end as a hang.

   If a test fails, run only that test again without the patch. If it fails the same way, it is a failure that was already there. If not, the patch broke it: fix it and go back to step 2. A test that cannot run on {platform} is not a failure: list it as not run, and record a try push for it if you can. Record `associated_tests` with each test and its result.

5. **Repeat:** a code change after steps 2 to 4 needs all of them again. A change to comments or format only needs step 3 again. After {max_rounds} review rounds with blocking findings, record a Bugzilla comment with what you found instead of a patch.

An update to an existing revision skips step 1. Put the evidence of each gate in the revision's test plan.

Before you record the patch, commit it locally with the revision's title as the subject and its summary as the body, so the commit message is the one that will land.

## Team rules

{rules}
