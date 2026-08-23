# The Barometer ☂
DownDetector for model *health*: separates corroborated anomaly from
perceived weather. Core doctrine: says "something changed", never "nerfed".

## What's here (MVP core, stdlib-only, tests green)
- `barometer/detect.py` — complaint taxonomy; cascade dedup (shingle
  Jaccard + shared-seed collapse: one viral post = ONE datum);
  cross-community independence scoring; trailing Poisson-ish baselines;
  burst detection with release-day threshold doubling (releases ALWAYS
  spike — comparison effect); canary logprob drift (distribution shape on a
  fixed benign text — the no-specimens rule, honoured); tier logic
  T1 perceived / T2 corroborated / T3 attributed.
- `barometer/dashboard.py` — self-contained ranked landing page plus model
  weather reports, with the ethics boundary baked in.
- `barometer/catalog.py` — public lab/family metadata and recognised model
  terms used by search and filtering.
- `barometer/submissions.py` — separately stored, moderated user reports;
  nothing enters detection automatically.
- `serve_barometer.py` and `manage_reports.py` — local form/API evaluation and
  deliberate queue review. See `USER_REPORTS.md`.
- `tests/` — torture suite: quiet weather doesn't alarm; cascades can't
  manufacture bursts; genuine multi-source bursts tier correctly; release
  days need double evidence; stable canaries hold T1; fingerprint change
  or provider ack → T3.
- `demo.py` — synthetic fortnight + real event → `barometer_demo.html`.

## Current state
- Hacker News observation is active. Reddit and capped X ingestion adapters also
  exist, but neither is active; provider-event ingestion, opt-in telemetry, and
  public deployment do not.
- The optional OpenAI canary transport reads `OPENAI_API_KEY` from the
  environment. Never put provider credentials in source files.
- `run_barometer.py` is import-safe. With no flags it only re-renders retained
  local data; every network tap and paid canary is explicit opt-in.
- Runtime databases and private observation artifacts are deliberately excluded
  from the repository. The checked-in HTML files are aggregate demonstration
  outputs, not evidence that collection is live.

## Data and credentials
- The repository contains no API credentials. Live transports read credentials
  from process environment variables; `.env` files are ignored.
- SQLite databases, write-ahead logs, and the complete `observation/` workspace
  are ignored. Do not commit raw observation data.
- Test values such as `test-client`, `test-secret`, and `test-token` are inert
  fixtures and are never accepted as live defaults.
- Generated public artifacts must remain aggregate-only. Reddit content and
  identifiers are processed transiently and are never written to SQLite or
  rendered into public output.

## Tracked model families

| Public family | Lab | Recognised mentions |
| --- | --- | --- |
| Claude | Anthropic | Claude, Sonnet, Opus, Haiku |
| GPT / ChatGPT | OpenAI | ChatGPT, GPT-5, GPT-4, GPT-4o, o3, o4 |
| Gemini | Google | Gemini, Google AI, Bard |

Detection still establishes its signal baseline at family level, but the public
landing page breaks report volume down by exact model whenever a post names one
explicitly. Ambiguous posts remain in a visible unspecified bucket rather than
being guessed into a variant.

Future presentation direction: an optional, reduced-motion-aware ambient
weather layer driven by signal and complaint-category tags. It remains
decorative and must never make the evidence look stronger than it is.

## Pipeline (added overnight)
- `store.py` — SQLite state: complaint dedup on stable id, readings, events.
- `adapters.py` — authenticated ephemeral Reddit, HN Algolia, and metered X
  recent-search taps; injected transports; keyword model-routing + complaint
  heuristics. Nothing calls the network in tests, ever.
- `canary.py` — budget-guarded runner: ONE reading per model per day,
  refuses more ("the wrist is not a keyboard"). Provider transports live
  outside, with the keys.
- `cli.py` — `tick(store, adapters, runner)`: ingest → canary-if-due →
  detect → tier → render, idempotent, dead taps fail loudly but singly.

## Verify locally (no network, no API spend)
`python -m unittest discover -v`

## Isolated Hacker News observation trial
`python run_barometer.py --observe-hn`

This preset uses `observation/private/barometer.db`, writes aggregate-only
public artifacts under `observation/public/`, keeps private raw reports for at
most 30 days, keeps one aggregate history sample per UTC day, publishes a
last-run health record, and cannot be combined with an OpenAI canary. Other
approved observation sources may be added explicitly.

See `OPERATIONS.md` for the active daily Windows task and recovery commands.

## Authenticated Reddit observation (not activated)

The anonymous Reddit JSON prototype has been removed from the CLI path. Reddit
now requires registered application-only OAuth and these process environment
variables:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`, in Reddit's identifying format, for example
  `windows:barometer:v0.2 (by /u/example_username)`

Once Reddit has approved/registered the use case, a manual combined run is:

`python run_barometer.py --observe-hn --observe-reddit`

Reddit post text, URLs, IDs, and authors are never inserted into SQLite. Reports
exist in memory only for the current tick so they can participate in cascade
deduplication and cross-source detection. The process then discards them and
publishes only aggregate counts. Rate-limit usage is included in `status.json`.

The intended workload is one OAuth token request plus five subreddit listing
requests per tick, far below Reddit's published eligible-free limit. Activation
still requires a registered/approved Reddit app and a privacy policy for the
eventual public site. See `REDDIT_SETUP.md`.

## Capped X observation trial (manual only)

X collection requires both an explicit flag and `X_BEARER_TOKEN` in the process
environment:

`python run_barometer.py --observe-hn --observe-x`

The default allowance is at most 60 returned posts per UTC day: 20 for each of
Claude, GPT, and Gemini. At X's published price on 23 August 2026 of $0.005 per
post read, that is an estimated upper bound of $0.30/day. Set a lower or higher
hard allowance with `--x-daily-read-limit`; the X Developer Console spending
limit remains the authoritative outer guard.

Barometer persists a `since_id` cursor per query and reports candidate posts,
accepted complaints, saturated queries, and an upper-bound cost in
`status.json`. A saturated query filled its sample and may have had more matches
available. Before every request Barometer reserves the maximum possible returned
posts. A successful response refunds unused allowance; an ambiguous failure
keeps the reservation spent so automatic retries cannot leak past the daily
ceiling. The estimate is deliberately conservative because X may deduplicate
billing within a UTC day.

The X trial is not part of the active scheduled task and no credential is stored
in this repository. Confirm current rates in the
[official X API pricing documentation](https://docs.x.com/x-api/getting-started/pricing)
before enabling it.

The first manually supervised run on 23 August 2026 filled all three 20-post
samples. Of 60 returned candidates, 38 passed the local complaint filter:
15 Claude, 13 Gemini, and 10 GPT. X's usage endpoint reported 56 billable Post
reads after daily deduplication, approximately $0.28 at the published rate.
Every detected cluster remained Tier 0 because it lacked source independence.
The HN-only scheduler was not changed.

## Before going live
1. Review the canary methodology and claim boundaries; do not schedule the
   current runner merely because the plumbing works.
2. Install the optional live dependency with
   `python -m pip install -r requirements-live.txt`.
3. Set `OPENAI_API_KEY` in the process environment if the OpenAI canary is
   approved. The official SDK reads it automatically.
4. Run `python run_barometer.py` to verify the retained data offline. After each
   source is approved, enable it explicitly with `--hn`, `--x`, `--reddit`, or
   `--openai-canary`, inspect the returned errors and generated dashboards, and
   only then add a once-daily scheduler using the approved flags.
