# Launch readiness — 23 August 2026

## Safe now

- The credential formerly embedded in `run_barometer.py` has been removed.
  The optional OpenAI transport uses `OPENAI_API_KEY` from the process
  environment.
- SQLite connections close deterministically on Windows.
- All 32 tests pass without network access or API spend.
- The folder is a Git repository on `main`; the initial commit preserves the
  July database and dashboards as provenance.
- Running `python run_barometer.py` is offline by default. Network taps and the
  paid canary require explicit flags.

## External action still required

Revoke the exposed OpenAI key in the provider dashboard. Removing it from the
file prevents future disclosure but cannot invalidate a credential that may
already have been copied.

## Do not schedule the full runner yet

### Canary claim validity

The current OpenAI canary is useful plumbing, but it is not yet valid T2
corroboration:

1. The API returns log probabilities for generated output tokens. The database
   stores only their probabilities, not the token identities. If the model emits
   different tokens, positional comparison can report false drift.
2. Social routing combines ChatGPT, OpenAI, and several GPT model names into one
   `gpt` bucket, while the canary probes one pinned API snapshot. Product-layer
   ChatGPT change and API-snapshot change are not the same measured system.
3. `system_fingerprint` identifies backend configuration. A changed fingerprint
   is useful context, but is not by itself provider acknowledgment of the cause
   of a user-visible change.

Keep `--openai-canary` off until readings retain token identities, comparisons
reject non-matching token sequences, and complaint/canary namespaces describe
the same surface (for example, `openai-api/gpt-5.5-2026-04-23`).

### Public-data access and retention

- Reddit's current Data API terms require OAuth, an identifying User-Agent,
  compliance with the approved use case, a privacy policy, and removal of
  deleted content. The CLI lane now uses application-only OAuth and never
  persists Reddit User Content, IDs, URLs, or author fields. Keep `--reddit` off
  until the app/use case is registered or approved and the public privacy policy
  exists.
- The Hacker News adapter currently uses the public Algolia search endpoint.
  For durable provenance, migrate it to the official HN Firebase API or document
  and monitor the Algolia dependency before scheduling `--hn`.
- The observation database prunes persisted HN/X raw reports after 30 days.
  Reddit raw content has zero persistent retention; only aggregate counts can
  survive the tick.

## HN observation trial — 23 August 2026

An isolated `--observe-hn` profile now writes to an ignored runtime directory,
publishes only aggregate HTML/JSON, and prunes private raw reports after 30
days. It may be combined with explicitly approved observation sources but not a
canary.

The first live tick collected 43 unique reports from the preceding 21 days:
25 Claude, 12 Gemini, and 6 GPT. Two immediate repeat ticks surfaced five more
unique older/indexed Algolia results, bringing the private store to 48 rows.
There were no duplicate URLs and no burst assessment. This is expected source
indexing behaviour: `new_complaints` means new to Barometer, not necessarily
newly posted since the previous tick.

HN alone is one source, so it can establish a background-rate baseline but
cannot earn multi-community corroboration.

### Restarted operating state

The HN observation lane is now scheduled daily at 09:00 local time through the
Windows task `Barometer HN Observation`. It starts when the logged-in machine
and network are available. A manual execution through Task Scheduler completed
successfully with result `0`; `status.json` recorded `ok`.

As of the latest manual verification run the private observation store held 51
unique HN reports (30 Claude, 12 Gemini, and 9 GPT), the daily aggregate history
contained one sample for 23 August, and there were still no burst assessments.
The July archive remained unchanged.

Bluesky anonymous search was evaluated as a possible second community source.
It briefly responded and then consistently returned HTTP 403 across clients,
so it is not enabled. Mastodon full-text status search requires authenticated,
instance-dependent access and is likewise not part of the live lane.

### Metered X trial completed manually; remains unscheduled

An X recent-search adapter now exists behind `--x` / `--observe-x`. It requires
`X_BEARER_TOKEN`, stores no credential, keeps per-query `since_id` cursors, and
reserves usage before each network request. The default hard ceiling is 60 post
reads per UTC day, with a reported cost upper bound based on the published
23 August 2026 price. Tests cover parsing, cursor reuse, complaint filtering,
missing credentials, and fail-closed accounting after an ambiguous timeout.

The first authenticated run filled all three 20-post samples. Sixty candidates
were returned, 38 passed the complaint filter (15 Claude, 13 Gemini, 10 GPT),
and X reported 56 billable reads after deduplication: approximately $0.28 at the
published price. The public source mix became Claude 30 HN / 15 X, Gemini 12 HN
/ 13 X, and GPT 9 HN / 10 X. Source-local clusters appeared for all three models
but remained Tier 0 because their burst windows lacked independent sources.

The live HN scheduled task was not changed. The X lane remains manual-only
pending a decision on sample rate, budget, and observation duration.

### Authenticated Reddit lane implemented; not activated

The old anonymous JSON route has been replaced with confidential-client OAuth.
The adapter obtains an application-only access token, makes one authenticated
listing request per configured model community, and reports Reddit's rate-limit
headers. Reddit reports participate ephemerally in current cross-source
detection but are never stored in SQLite; tests prove they appear in aggregate
output while absent from the private store.

Activation remains blocked on Reddit-side app registration/approval and a public
privacy policy. `REDDIT_SETUP.md` contains the exact data-flow description and
application checklist. No Reddit credentials are present and the scheduled task
remains HN-only.

### Missing operational evidence

- There are no provider events, so release-day threshold adjustment is inactive.
- There are no canary readings, so the July snapshot supports social-weather
  claims only.
- No health alert, log rotation, backup policy, or atomic HTML dashboard write
  exists yet.

## Recommended restart order

1. Revoke the exposed key.
2. Keep the retained July snapshot read-only and test the offline render path.
3. Define retention/deletion and replace or explicitly approve the HN transport.
4. Run a short HN-only observation period with canaries and Reddit disabled.
5. Review the first capped X sample and explicitly choose a budget and duration
   before considering any scheduler change.
6. Register the Reddit app/use case, publish the privacy policy, and perform one
   manually supervised ephemeral Reddit sample before any scheduler change.
7. Redesign and calibrate each surface-specific canary before allowing it to
   influence T2 or T3 claims.
