# Reddit access setup

## Current boundary

The Reddit adapter is implemented but not activated. It uses application-only
OAuth with a confidential client and has no anonymous fallback. Do not run it
until Reddit has registered or approved the use case.

The adapter:

- reads the newest public submissions from `r/ClaudeAI`, `r/OpenAI`,
  `r/ChatGPT`, `r/GeminiAI`, and `r/Bard`;
- makes one OAuth token request and five listing requests per tick;
- requests no user-context scopes and never posts, votes, comments, reports,
  messages, moderates, or accesses private content;
- does not extract or store author fields;
- keeps post text, Reddit IDs, permalinks, and destination URLs only in process
  memory for the duration of one tick;
- uses those ephemeral reports for complaint filtering, cascade deduplication,
  and cross-source detection, then discards them;
- retains only aggregate model/source/category counts and non-identifying burst
  statistics in the public snapshot/history.

This data flow is intentionally narrower than Reddit's recommendation to delete
stored User Content routinely: Barometer does not persist it at all.

## Suggested application description

> Barometer is an informational service that measures aggregate public reports
> of apparent AI-model quality or availability changes. It reads a small number
> of public model-community listing endpoints once per observation tick. Reddit
> content is processed transiently for keyword classification and duplicate
> suppression and is never stored or displayed. The service retains only
> aggregate, non-identifying counts and anomaly statistics. It does not profile
> Redditors, train models, contact users, or perform any write action on Reddit.

Adjust that text if the actual deployment or business model changes. Reddit's
terms require a separate agreement for commercial use or use beyond what they
approve; do not silently broaden the declared purpose.

## Credentials and identification

Create/register a confidential server or web application that can protect a
client secret and supports the `client_credentials` grant. Store these as
Windows user environment variables, never in the repository:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`

The User-Agent must follow Reddit's identifying shape:

`windows:barometer:v0.2 (by /u/YOUR_REDDIT_USERNAME)`

The username must be real and associated with the application contact. The
runner refuses to enable Reddit when any value is missing or the User-Agent does
not contain the identifying `/u/` contact.

## Privacy policy before public deployment

The public privacy policy must accurately state:

- which public Reddit communities are read;
- that raw Reddit User Content and identifiers are processed only transiently
  and are not persisted or displayed;
- which aggregate statistics are retained and for how long;
- that no Reddit data is used to train an AI model or profile individual users;
- the operator identity/contact route;
- how deletion, privacy, and security requests can be made;
- any hosting, logging, analytics, cookie, or third-party processing that is
  actually introduced during deployment.

Do not publish a template containing guessed contact or hosting details. Those
facts must be supplied when deployment is chosen.

## Manual verification only

After approval, credential storage, and privacy review:

`python run_barometer.py --observe-hn --observe-reddit`

Inspect `observation/public/status.json` for token/listing errors, request count,
and rate-limit remaining. Then verify directly that SQLite contains no source
beginning with `reddit/`. Do not add Reddit to the Windows task until the manual
result and the public-policy boundary have been reviewed.

## Primary references

- [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms)
- [Reddit OAuth2 application-only flow](https://github.com/reddit-archive/reddit/wiki/OAuth2#application-only-oauth)
- [Reddit Developer Data Protection Addendum](https://redditinc.com/policies/developer-dpa)
