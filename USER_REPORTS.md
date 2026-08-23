# User report boundary

Barometer's user-report flow is a moderated input lane, not a direct detector
feed. A submitted report is written to a separate private SQLite database and
starts in `pending` state. No submission is inserted into `barometer.db`, shown
on a public page, or counted as weather automatically.

## Collected fields

- model family;
- optional exact model name;
- problem category;
- product surface;
- broad timing bucket;
- optional description, limited to 600 characters.

The form does not request an account, name, email address, or location. The
local evaluation server uses a transient in-memory IP rate limit but does not
write IP addresses to SQLite. Submitters are told not to include personal,
confidential, or sensitive information.

Raw reports have a 30-day retention target. The local server prunes expired
records at startup and every five minutes while it is running. The moderation
CLI also supports an explicit retention pass for offline stores.

## Local supervised evaluation

First render the observation site without making network requests:

`python run_barometer.py --db observation/private/barometer.db --out-dir observation/public --public-snapshot observation/public/summary.json --public-history observation/public/history.json`

Then run the local form/API server:

`python serve_barometer.py`

Open `http://127.0.0.1:8765/`. Submitted reports are stored in the ignored file
`observation/private/user_reports.db`.

## Moderation

List pending metadata without descriptions:

`python manage_reports.py list`

Read one report deliberately:

`python manage_reports.py show REPORT_ID`

Record a decision:

`python manage_reports.py approve REPORT_ID --note "reason"`

`python manage_reports.py reject REPORT_ID --note "reason"`

Approval records a moderation result only. It does not promote the report into
the detector. That later bridge requires separate aggregation, anti-abuse, and
evidence-weighting design.

Delete reports older than the retention window:

`python manage_reports.py prune --days 30`

## Production warning

`serve_barometer.py` is a standard-library server for local supervised
evaluation. Its in-memory rate limit, same-origin check, and security headers
are useful safeguards but are not a production deployment architecture.
Public launch still requires HTTPS, durable edge rate limiting, abuse handling,
operator contact details, monitoring, backups/erasure policy, and a final
privacy policy matching the chosen host.
