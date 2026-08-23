# Barometer operations

## Active observation task

- Windows task name: `Barometer HN Observation`
- Schedule: daily at 09:00 local time
- Working directory: `C:\Users\ebevi\Documents\Dev\barometer`
- Command: `C:\Users\ebevi\miniconda3\python.exe run_barometer.py --observe-hn`
- Logon mode: interactive user; it does not store a Windows password
- Recovery: start when available, require network, allow battery power
- Limit: one instance, maximum runtime 15 minutes

The task contains no Reddit or model-canary flags. A manually triggered task run
on 23 August 2026 completed with Task Scheduler result `0` and a public health
status of `ok`.

It also contains no X flag or X credential. Adding the X adapter to the code did
not alter the running task.

## Runtime data

- `observation/private/barometer.db` — private raw reports, 30-day retention
- `observation/public/summary.json` — current aggregate-only snapshot
- `observation/public/history.json` — one aggregate sample per UTC day
- `observation/public/status.json` — last-run completion and error state
- `observation/public/barometer_*.html` — aggregate dashboards
- `observation/public/index.html` and `report.html` — public landing and form
- `observation/private/user_reports.db` — private pending moderation queue

The runtime directory is ignored by Git. Public artifacts contain no post text
or source URLs. Repeated runs on the same UTC day replace that day's history
sample rather than adding duplicate points.

User submissions are a separate lane and never enter the detector
automatically. See `USER_REPORTS.md` for local serving, review, retention, and
the production-deployment boundary.

## Useful commands

Manual observation tick:

`python run_barometer.py --observe-hn`

Inspect the scheduled task:

`Get-ScheduledTaskInfo -TaskName 'Barometer HN Observation'`

Pause or resume collection:

`Disable-ScheduledTask -TaskName 'Barometer HN Observation'`

`Enable-ScheduledTask -TaskName 'Barometer HN Observation'`

## Source boundary

HN is currently the sole scheduled social source. It can establish background
weather, but cannot independently earn a multi-community T1 claim. Anonymous
Bluesky search briefly responded during evaluation and then consistently
returned HTTP 403 across clients, so it is not part of the scheduled lane.

The Reddit lane now uses application-only OAuth and cannot fall back to anonymous
JSON. It requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and an identifying
`REDDIT_USER_AGENT`. Reddit reports are ephemeral: they can affect the current
aggregate dashboard and cross-source detector but are never inserted into
SQLite. The intended manual command after Reddit approves/registers the app is:

`python run_barometer.py --observe-hn --observe-reddit`

The adapter makes one token request and one listing request for each of five
model communities, reports Reddit's rate-limit headers, stores no author fields,
and publishes no raw Reddit content. It remains disabled pending app access and
a privacy policy for public deployment. See `REDDIT_SETUP.md`.

The X adapter is implemented for manual use only. It requires both `--x` (or
`--observe-x`) and `X_BEARER_TOKEN`, persists query cursors and a conservative
UTC-day usage ledger in SQLite, and defaults to a hard ceiling of 60 returned
posts per day. The combined trial command is:

`python run_barometer.py --observe-hn --observe-x`

At the published 23 August 2026 price of $0.005 per post read, the default code
ceiling represents at most $0.30/day in post reads. Check current X pricing and
set a separate Developer Console spending limit before the first live run. The
run's aggregate `status.json` reports the upper-bound read units and estimated
cost; it never publishes post text or credentials.

### First supervised X sample — 23 August 2026

The first authenticated combined run returned the full 60-candidate ceiling;
all three model queries saturated. Thirty-eight posts passed the local complaint
filter: 15 Claude, 13 Gemini, and 10 GPT. X's usage endpoint reported 56
billable Post reads after deduplication, corresponding to approximately $0.28 at
the published rate. Barometer recorded Tier 0 source-local clusters for all
three models but did not promote any claim because the burst windows lacked
independent sources.

The scheduled task remains HN-only. Do not add `--observe-x` until the sample
rate, budget, and desired observation period are explicitly approved.

The source catalogue now also recognises xAI/Grok and newer exact-model names.
This changed the future 60-read X allocation from three 20-post queries to four
15-post queries; it did not make another paid request or alter the scheduler.

Provider status feeds should be added later as a separate evidence class. A
provider acknowledgment of an outage must be matched to the affected surface
and complaint category before it can raise a claim tier.
