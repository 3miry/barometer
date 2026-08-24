# User report boundary

Barometer's user-report flow is a moderated input lane, not a direct detector
feed. A submitted report is written to a separate private SQLite database and
starts in `pending` state. Pending and rejected submissions are never counted.
Once a moderator approves a report, the next render may use only its structured
fields as a `user` observation. Its description and moderation notes remain in
the private queue and never cross into public or detector data.

## Collected fields

- model family;
- optional exact model name;
- problem category;
- product surface;
- broad timing bucket;
- optional description, limited to 600 characters.

The form does not request an account, name, email address, or location. For
anti-abuse throttling, the server canonicalises the connection address and
derives a keyed HMAC token. Only that opaque token and an attempt timestamp are
stored; the raw address is not written to SQLite or the access log. Attempt
records expire after 24 hours. The default limits are three attempts per 15
minutes and eight per 24 hours, and invalid or rejected requests consume the
allowance too.

This is deliberately a blunt anonymous boundary. People behind the same NAT may
share an address, while determined attackers can change addresses. It reduces
casual repetition without pretending to establish identity.

Raw reports have a 30-day retention target. The local server prunes expired
reports and anti-abuse attempts at startup and every five minutes while it is
running. The moderation CLI also supports an explicit report-retention pass for
offline stores.

## Local supervised evaluation

First render the observation site without making network requests:

`python run_barometer.py --db observation/private/barometer.db --out-dir observation/public --public-snapshot observation/public/summary.json --public-history observation/public/history.json`

Then run the local form/API server:

`python serve_barometer.py`

Open `http://127.0.0.1:8765/`. Submitted reports are stored in the ignored file
`observation/private/user_reports.db`.

For a stable limiter across server restarts, set a long random value in
`BAROMETER_RATE_LIMIT_SECRET`. A local run without it creates an ephemeral
process secret. A non-local bind refuses to start without the environment
variable. Do not put the secret in source control.

## Moderation

List pending metadata without descriptions:

`python manage_reports.py list`

Read one report deliberately:

`python manage_reports.py show REPORT_ID`

Record a decision:

`python manage_reports.py approve REPORT_ID --note "reason"`

`python manage_reports.py reject REPORT_ID --note "reason"`

Approval makes the report's structured observation eligible for the next
render. Run the normal offline render command again; it automatically reads
`observation/private/user_reports.db` when present. All user submissions share
one `user` source type, so user reports alone cannot masquerade as independent
cross-source corroboration.

Delete reports older than the retention window:

`python manage_reports.py prune --days 30`

## Production warning

`serve_barometer.py` is a standard-library server for local supervised
evaluation. Its durable application limit, same-origin check, and security
headers are useful safeguards but are not a production deployment architecture.
It intentionally uses the direct socket address and does not trust client-set
forwarding headers. Public launch still requires HTTPS, edge rate limiting with
correct trusted-proxy configuration, abuse handling, operator contact details,
monitoring, backups/erasure policy, and a final privacy policy matching the
chosen host.
