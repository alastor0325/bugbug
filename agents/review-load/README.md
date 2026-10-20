# review-load agent

Posts one Slack message each work day with the review load of one Phabricator review
group: how many reviews are new, how long the others wait, which patches come from
Mozilla staff or from external contributors, and which reviews are overdue.

The agent uses no LLM. It reads Phabricator and Bugzilla through its broker, counts in
plain code, and records one `slack.post_message` action. hackbot-api posts it after the
run.

## Configuration

Each watched group has one file, `hackbot_agents/review_load/configs/<name>.toml`. A run
names it with the `config` input. An unknown name stops the run with an error.

| Key                            | Meaning                                                                                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `review_group`                 | The Phabricator project slug whose review requests count                                                                                  |
| `paths`, `excludes`            | A direct request to a member counts only for a patch that changes a file under `paths` and not under `excludes`. Empty `paths`: all count |
| `[members]`                    | Phabricator handle and display name. Direct requests to them count too                                                                    |
| `report.slack_channel`         | Where the message goes                                                                                                                    |
| `report.age_buckets_work_days` | Bucket edges in work days. `[1, 3, 7]` gives <1, 1-3, 3-7 and 7+                                                                          |
| `report.overdue_work_days`     | Older than this, a review is overdue and listed by name                                                                                   |
| `report.stale_work_days`       | Older than this, a review is only counted on one "stale" line                                                                             |
| `report.max_listed`            | The longest overdue list; the rest shows as "and N more"                                                                                  |
| `authors.staff_email_domains`  | A Bugzilla email in one of these domains marks Mozilla staff                                                                              |
| `authors.staff_accounts`       | Phabricator handles to count as staff                                                                                                     |
| `authors.external_accounts`    | Phabricator handles to count as external                                                                                                  |

Add a group: copy `configs/media-playback.toml`, change the keys, and run the tests.
`test_every_shipped_config_loads` checks the new file.

## Inputs

| Env          | Default                     | Meaning                                          |
| ------------ | --------------------------- | ------------------------------------------------ |
| `CONFIG`     | required                    | The config name                                  |
| `DRY_RUN`    | `false` (`true` in compose) | Write `message.json`, but record no Slack action |
| `BROKER_URL` | `http://127.0.0.1:8765`     | The broker                                       |

## What counts

- A review waits when the revision is `needs-review` and not a draft, and the group or
  a member is a reviewer in the `added` or `blocking` state. The author's own review
  does not count.
- A review waits since the later of two times: when that reviewer was last added, and
  when the revision last went back to review (`request-review`, `reopen`, `reclaim` or
  a new diff).
- Ages are in work days in UTC. Saturday and Sunday do not count; holidays do.
- "New" is the first bucket: waiting reviews requested less than one work day ago. On
  Monday that includes the weekend. A review that was requested and done on the same
  day is not counted.
- A request to a member, not the group, counts only if the revision changes a file
  under `paths` and not under `excludes` (`differential.getcommitpaths`). A patch that
  only changes tests outside `paths` is not counted.
- A reviewer that waits `stale_work_days` or more is left out of the buckets, the
  per-reviewer list and the overdue list. A revision with only such reviewers is
  counted on the "stale" line.
- Totals count each revision once, by its longest wait. "Per reviewer" counts the
  revision once for each watched reviewer it waits on.
- An author is staff or external from the Bugzilla login email
  (`bugzilla.account.search`, then the Bugzilla user record). An author with no linked
  Bugzilla account is "unknown".
- The overdue list and the stale line link to one Phabricator search
  (`/differential/?ids=...`) with all of their revisions, to work through them.
- On Saturday and Sunday the run ends with no message.

## Broker

`broker.py` holds `PHABRICATOR_API_KEY` and `BUGZILLA_API_KEY`. The agent container
holds no key.

- `/phabricator/api`: the read-only Conduit proxy, with `project.search`,
  `user.search`, `transaction.search`, `bugzilla.account.search` and
  `differential.getcommitpaths` added for this
  agent only.
- `/bugzilla/emails?ids=...`: the login email of each Bugzilla user id, nothing else.

## Run locally

A local run has no hackbot-api apply step, so it never posts to Slack.

1. Get a Phabricator Conduit API token (Phabricator, Settings, Conduit API Tokens) and
   a Bugzilla API key.
2. In `.env` at the repo root, set `PHABRICATOR_API_KEY` and `BUGZILLA_API_KEY`.
3. Run it with Docker:

   ```sh
   CONFIG=media-playback docker compose -f agents/review-load/compose.yml \
     run --rm review-load-agent
   ```

   Or without Docker, in two shells from the repo root:

   ```sh
   set -a; source .env; set +a
   uv run --package hackbot-agent-review-load python -m hackbot_agents.review_load.broker
   ```

   ```sh
   CONFIG=media-playback DRY_RUN=true \
     uv run --package hackbot-agent-review-load python -m hackbot_agents.review_load
   ```

4. Read `message.json`, `reviews.json` (every waiting and stale review, with its age and
   state) and `summary.json` under `~/hackbot/artifacts/<run_id>/` (Docker)
   or `artifacts/<run_id>/` (no Docker). To see the message as Slack shows it, paste
   `blocks` into the [Block Kit Builder](https://app.slack.com/block-kit-builder).

## Tests

```sh
uv run --package hackbot-agent-review-load --extra dev pytest agents/review-load/tests
```

The fixtures in `tests/testdata.py` are Conduit-shaped responses with fake people and
PHIDs.
