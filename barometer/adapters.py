"""Ingestion taps. Every adapter takes an injected transport
(callable url -> parsed JSON) so tests never touch the network and the
suite never spends anyone's money — house law since the incident.

Live transports are provided but nothing here calls them by default."""
from __future__ import annotations
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import uuid
from .detect import Complaint
from .catalog import infer_variant
from .probes import CollectionRun
from .search_terms import SearchTermDefinition

USER_AGENT = "the-barometer/0.1 (fleet weather, not verdicts)"
X_POST_READ_USD = 0.005
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_QUERY_VERSION = 5


@dataclass(frozen=True)
class XQuerySpec:
    query_id: str
    family: str
    lane: str
    query: str
    term_ids: tuple[str, ...] = ()
    concept_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class XCollectionBatch:
    run: CollectionRun
    complaints: tuple[Complaint, ...]
    result_ranks: tuple[int, ...]


X_MODEL_IDENTITY_QUERIES = (
    XQuerySpec(
        "x.discovery.model_identity.claude", "claude", "discovery",
        '("Fable 5" OR "Opus 5" OR "Sonnet 5" OR "Opus 4.8" '
        'OR "Claude Opus" OR "Claude Sonnet" OR "Claude Fable") '
        'lang:en -is:retweet',
    ),
    XQuerySpec(
        "x.discovery.model_identity.gpt", "gpt", "discovery",
        '("GPT-5.5" OR "GPT 5.5" OR "GPT-5.6" OR "GPT 5.6" '
        'OR "ChatGPT Sol" OR "ChatGPT Luna" OR "ChatGPT Terra" '
        'OR "5.6 Sol" OR "Sol 5.6" OR "5.6 Luna" OR "Luna 5.6" '
        'OR "5.6 Terra" OR "Terra 5.6" OR "Codex 5.6") '
        'lang:en -is:retweet',
    ),
    XQuerySpec(
        "x.discovery.model_identity.gemini", "gemini", "discovery",
        '("Gemini 3.1 Pro" OR "Gemini Pro 3.1" OR "Gemini Flash 3.5" '
        'OR "Gemini 3.5 Flash" OR "Gemini Flash-Lite 3.7" '
        'OR "Gemini 3.7 Flash Lite") lang:en -is:retweet',
    ),
    XQuerySpec(
        "x.discovery.model_identity.grok", "grok", "discovery",
        '("Grok 4.5" OR "Grok-4.5" OR "Grok 4.6" OR "Grok-4.6") '
        'lang:en -is:retweet',
    ),
)

X_TEMPORAL_PHRASES = (
    "today", "this morning", "tonight", "recently", "lately", "suddenly",
    "first time", "this week", "has become", "used to", "anymore",
)


def _quoted_or(phrases) -> str:
    return "(" + " OR ".join(
        f'"{phrase}"' for phrase in dict.fromkeys(phrases)) + ")"


def x_targeted_query_specs(
        search_terms: tuple[SearchTermDefinition, ...] = (),
) -> tuple[XQuerySpec, ...]:
    """Build the overlapping temporal/LLT lane without making either a gate."""
    specs = []
    for identity in X_MODEL_IDENTITY_QUERIES:
        identity_clause = identity.query.removesuffix(" lang:en -is:retweet")
        phrases = list(X_TEMPORAL_PHRASES)
        included_terms = []
        for term in search_terms:
            candidate = (
                f"{identity_clause} {_quoted_or(phrases + [term.phrase])} "
                "lang:en -is:retweet"
            )
            if len(candidate) <= 512:
                phrases.append(term.phrase)
                included_terms.append(term)
        query = (
            f"{identity_clause} {_quoted_or(phrases)} lang:en -is:retweet"
        )
        term_signature = "|".join(
            f"{term.id}:v{term.definition_version}"
            for term in included_terms)
        digest = (
            hashlib.sha256(term_signature.encode()).hexdigest()[:10]
            if term_signature else "temporal"
        )
        specs.append(XQuerySpec(
            f"x.targeted.temporal_llt.{identity.family}.{digest}",
            identity.family,
            "targeted",
            query,
            tuple(term.id for term in included_terms),
            tuple(dict.fromkeys(
                term.concept_id for term in included_terms)),
        ))
    return tuple(specs)


def _rotating_specs(
        specs: tuple[XQuerySpec, ...], count: int, offset: int
) -> tuple[XQuerySpec, ...]:
    if count >= len(specs):
        return specs
    return tuple(specs[(offset + index) % len(specs)] for index in range(count))


def x_default_query_specs(
        day_utc: str, daily_read_limit: int = 60,
        search_terms: tuple[SearchTermDefinition, ...] = (),
) -> tuple[XQuerySpec, ...]:
    """Plan overlapping model-only discovery and temporal/LLT depth pulls.

    At 60 reads, all four broad family frames run and two depth families rotate.
    At 80 reads, both passes cover all four families. This makes temporal words
    high-priority retrieval language without requiring them for admission.
    """
    day_number = datetime.fromisoformat(day_utc).date().toordinal()
    query_slots = max(1, min(8, daily_read_limit // 10))
    if query_slots == 1:
        discovery_count, targeted_count = 1, 0
    else:
        discovery_count = min(4, max(1, query_slots - 1))
        targeted_count = min(4, query_slots - discovery_count)
    discovery = _rotating_specs(
        X_MODEL_IDENTITY_QUERIES, discovery_count, day_number % 4)
    targeted = _rotating_specs(
        x_targeted_query_specs(search_terms), targeted_count,
        day_number % 4)
    return discovery + targeted

def live_transport(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def x_live_transport(url: str, bearer_token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def reddit_token_transport(
        client_id: str, client_secret: str, user_agent: str) -> dict:
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=urllib.parse.urlencode(
            {"grant_type": "client_credentials"}).encode("ascii"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def reddit_listing_transport(
        url: str, access_token: str, user_agent: str) -> tuple[dict, dict]:
    request = urllib.request.Request(url, headers={
        "Authorization": f"bearer {access_token}",
        "User-Agent": user_agent,
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response), dict(response.headers.items())


# Which models do complaint texts concern? Keyword routing, MVP-grade.
MODEL_KEYWORDS = {
    "claude": ["claude", "anthropic", "sonnet", "opus", "haiku", "fable"],
    "gpt":    ["chatgpt", "gpt-", "gpt4", "gpt5", "openai", " 4o", "o3", "o4"],
    "gemini": ["gemini", "google ai", "bard"],
    "grok":   ["grok", "xai", "x.ai"],
}
COMPLAINT_HINTS = ["worse", "nerf", "dumb", "lazy", "slow", "degrad", "refus",
                   "shorter", "off today", "quality", "stupid", "broken",
                   "downgrade", "since the update", "anyone else"]

def route_model(text: str) -> str | None:
    if re.search(r"\b(?:Sol|Luna|Terra)\b", text):
        return "gpt"
    t = text.lower()
    for model, kws in MODEL_KEYWORDS.items():
        if any(k in t for k in kws):
            return model
    return None

def looks_like_complaint(text: str) -> bool:
    t = text.lower()
    return any(h in t for h in COMPLAINT_HINTS)


def _plain_text(value: str) -> str:
    """Remove simple source markup before classification and private storage."""
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value)).split())


class RedditAdapter:
    """Authenticated, read-only, and deliberately ephemeral Reddit tap."""
    source_name = "reddit"
    ephemeral = True

    def __init__(
            self, subreddits: list[str], client_id: str, client_secret: str,
            user_agent: str, token_transport=reddit_token_transport,
            listing_transport=reddit_listing_transport,
            clock=time.time):
        if not client_id or not client_secret:
            raise ValueError("Reddit OAuth client credentials are required")
        valid_contact = re.search(
            r"\(by /u/[A-Za-z0-9_-]+\)$", user_agent or "")
        if not valid_contact or any(c in user_agent for c in "\r\n"):
            raise ValueError(
                "Reddit User-Agent must identify the app and '(by /u/username)'")
        self.subreddits = subreddits
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.token_transport = token_transport
        self.listing_transport = listing_transport
        self.clock = clock
        self.errors: list[str] = []
        self._stats: dict = {}

    @staticmethod
    def _header(headers: dict, name: str) -> str | None:
        wanted = name.lower()
        for key, value in headers.items():
            if key.lower() == wanted:
                return str(value)
        return None

    def fetch(self, since: float) -> list[Complaint]:
        out: list[Complaint] = []
        seen: set[str] = set()
        self.errors = []
        self._stats = {
            "day_utc": datetime.fromtimestamp(
                self.clock(), tz=timezone.utc).date().isoformat(),
            "token_requests": 1,
            "listing_requests": 0,
            "candidate_posts": 0,
            "accepted_complaints": 0,
            "raw_persisted": False,
            "rate_limit_used": None,
            "rate_limit_remaining": None,
            "rate_limit_reset_seconds": None,
        }
        token_payload = self.token_transport(
            self.client_id, self.client_secret, self.user_agent)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise ValueError("Reddit OAuth response contained no access token")

        for sub in self.subreddits:
            safe_sub = urllib.parse.quote(sub, safe="")
            url = f"https://oauth.reddit.com/r/{safe_sub}/new?limit=100&raw_json=1"
            try:
                data, headers = self.listing_transport(
                    url, access_token, self.user_agent)
            except Exception as exc:
                self.errors.append(f"{sub}: {type(exc).__name__}: {exc}")
                continue
            self._stats["listing_requests"] += 1
            used = self._header(headers, "x-ratelimit-used")
            remaining = self._header(headers, "x-ratelimit-remaining")
            reset = self._header(headers, "x-ratelimit-reset")
            if used is not None:
                try:
                    used_value = float(used)
                    current = self._stats["rate_limit_used"]
                    self._stats["rate_limit_used"] = (
                        used_value if current is None else max(current, used_value))
                except ValueError:
                    self.errors.append(f"{sub}: invalid rate-limit used header")
            if remaining is not None:
                try:
                    remaining_value = float(remaining)
                    current = self._stats["rate_limit_remaining"]
                    self._stats["rate_limit_remaining"] = (
                        remaining_value if current is None
                        else min(current, remaining_value))
                except ValueError:
                    self.errors.append(f"{sub}: invalid rate-limit remaining header")
            if reset is not None:
                try:
                    self._stats["rate_limit_reset_seconds"] = float(reset)
                except ValueError:
                    self.errors.append(f"{sub}: invalid rate-limit reset header")

            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                post_id = str(d.get("id", ""))
                if not post_id or post_id in seen:
                    continue
                seen.add(post_id)
                self._stats["candidate_posts"] += 1
                ts = float(d.get("created_utc", 0))
                text = f"{d.get('title','')} {d.get('selftext','')}".strip()
                if ts < since or not text: continue
                model = route_model(text)
                if not model or not looks_like_complaint(text): continue
                permalink = str(d.get("permalink", ""))
                stored_text = text[:500]
                out.append(Complaint(
                    ts=ts, source=f"reddit/{sub}", model=model, text=stored_text,
                    url=f"https://www.reddit.com{permalink}",
                    seed_url=(d.get("url_overridden_by_dest") or None),
                    variant=infer_variant(model, stored_text)))
        self._stats["accepted_complaints"] = len(out)
        return out

    def usage_report(self) -> dict:
        return dict(self._stats)


class HNAdapter:
    """Hacker News via the Algolia search API. Free, clean, no auth."""
    def __init__(self, queries: list[str] | None = None, transport=live_transport):
        self.queries = queries or [
            "claude worse", "fable 5 worse", "chatgpt worse",
            "gpt-5.6 degraded", "gemini worse", "grok worse",
        ]
        self.transport = transport

    def fetch(self, since: float) -> list[Complaint]:
        out: list[Complaint] = []
        seen: set = set()
        for q in self.queries:
            data = self.transport(
                "https://hn.algolia.com/api/v1/search_by_date?"
                f"query={urllib.parse.quote(q)}&tags=(story,comment)"
                f"&numericFilters=created_at_i>{int(since)}")
            for hit in data.get("hits", []):
                hid = hit.get("objectID")
                if hid in seen: continue
                seen.add(hid)
                text = _plain_text(
                    hit.get("title") or hit.get("comment_text") or "")[:500]
                model = route_model(text)
                if not model or not looks_like_complaint(text): continue
                out.append(Complaint(
                    ts=float(hit.get("created_at_i", 0)), source="hn",
                    model=model, text=text,
                    url=f"https://news.ycombinator.com/item?id={hid}",
                    seed_url=hit.get("url") or None,
                    variant=infer_variant(model, text)))
        return out


def _x_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _x_seed_url(post: dict) -> str | None:
    for item in post.get("entities", {}).get("urls", []):
        candidate = item.get("unwound_url") or item.get("expanded_url")
        if candidate and "x.com/" not in candidate and "twitter.com/" not in candidate:
            return candidate
    return None


def _x_query_with_suppressed_handles(
        query: str, handles: tuple[str, ...]) -> str:
    """Apply as many reversible author exclusions as the X query cap permits."""
    result = query
    for handle in sorted(set(handles), key=str.casefold):
        candidate = f"{result} -from:{handle}"
        if len(candidate) <= 512:
            result = candidate
    return result


def _x_query_with_excluded_phrases(
        query: str, phrases: tuple[str, ...]) -> str:
    """Apply reversible content exclusions without exceeding X's 512 cap."""
    result = query
    for phrase in sorted(set(phrases), key=str.casefold):
        clause = f'-"{phrase}"' if " " in phrase else f"-{phrase}"
        candidate = f"{result} {clause}"
        if len(candidate) <= 512:
            result = candidate
    return result


class XAdapter:
    """Capped recent-search sampler for X's pay-per-use API.

    The store holds both per-query cursors and a conservative UTC-day usage
    ledger. Each request reserves its maximum result count before touching the
    network; an uncertain failure keeps the reservation spent.
    """
    source_name = "x"

    def __init__(
            self, store, bearer_token: str,
            queries: dict[str, str] | None = None,
            daily_read_limit: int = 60,
            per_query_limit: int = 20,
            retain_filter=looks_like_complaint,
            search_terms: tuple[SearchTermDefinition, ...] = (),
            excluded_phrases=(),
            suppressed_authors=(),
            transport=x_live_transport,
            clock=time.time):
        if not bearer_token:
            raise ValueError("an X bearer token is required")
        if daily_read_limit < 10:
            raise ValueError("X daily read limit must be at least 10")
        if not 10 <= per_query_limit <= 100:
            raise ValueError("X per-query limit must be between 10 and 100")
        self.store = store
        self.bearer_token = bearer_token
        self.clock = clock
        suppressed_authors = tuple(suppressed_authors)
        self.suppressed_author_ids = frozenset(
            str(item.author_id) for item in suppressed_authors)
        suppressed_handles = tuple(
            str(item.handle_snapshot)
            for item in suppressed_authors if item.handle_snapshot)
        excluded_phrases = tuple(str(item) for item in excluded_phrases)
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]{0,59}", phrase)
            for phrase in excluded_phrases
        ):
            raise ValueError("X query exclusions contain unsafe syntax")
        if queries is None:
            self.query_specs = x_default_query_specs(
                self._day(), daily_read_limit, search_terms)
        else:
            self.query_specs = tuple(
                XQuerySpec(
                    f"x.custom.{key}", key.split(":", 1)[0],
                    "legacy_unknown", query,
                )
                for key, query in queries.items()
            )
        self.query_specs = tuple(
            XQuerySpec(
                spec.query_id, spec.family, spec.lane,
                _x_query_with_suppressed_handles(
                    _x_query_with_excluded_phrases(
                        spec.query, excluded_phrases),
                    suppressed_handles),
                spec.term_ids, spec.concept_ids,
            )
            for spec in self.query_specs
        )
        self.queries = {
            spec.query_id: spec.query for spec in self.query_specs
        }
        self.daily_read_limit = daily_read_limit
        self.excluded_phrases = excluded_phrases
        fair_share = max(10, daily_read_limit // max(1, len(self.query_specs)))
        self.per_query_limit = min(per_query_limit, fair_share)
        self.retain_filter = retain_filter
        self.transport = transport
        self.errors: list[str] = []
        self._stats: dict = {}
        self.collection_batches: list[XCollectionBatch] = []

    def _day(self) -> str:
        return datetime.fromtimestamp(
            self.clock(), tz=timezone.utc).date().isoformat()

    @staticmethod
    def _cursor_key(spec: XQuerySpec) -> str:
        query_hash = hashlib.sha256(
            spec.query.encode("utf-8")).hexdigest()[:12]
        return (
            f"x:recent:v{X_QUERY_VERSION}:{spec.query_id}:"
            f"{query_hash}:since_id")

    def _daily_frame(self, since: float) -> tuple[float, float]:
        now = self.clock()
        current = datetime.fromtimestamp(now, tz=timezone.utc)
        day_start = datetime(
            current.year, current.month, current.day, tzinfo=timezone.utc,
        ).timestamp()
        # X documents end_time as exclusive. Staying ten seconds behind the
        # clock also avoids asking the recent-search index for its newest edge.
        return max(float(since), day_start), min(
            now - 10, day_start + 86400)

    def fetch(self, since: float) -> list[Complaint]:
        out: list[Complaint] = []
        seen: set[str] = set()
        day = self._day()
        frame_start, frame_end = self._daily_frame(since)
        self.errors = []
        self.collection_batches = []
        self._stats = {
            "day_utc": day,
            "queries_attempted": 0,
            "candidate_posts": 0,
            "accepted_complaints": 0,
            "suppressed_candidates": 0,
            "active_author_suppressions": len(self.suppressed_author_ids),
            "active_query_exclusions": len(self.excluded_phrases),
            "saturated_queries": 0,
            "budget_exhausted": False,
            "query_version": X_QUERY_VERSION,
            "planned_queries": len(self.query_specs),
            "planned_targeted_queries": sum(
                spec.lane == "targeted" for spec in self.query_specs),
            "planned_discovery_queries": sum(
                spec.lane == "discovery" for spec in self.query_specs),
            "frame_start_utc": datetime.fromtimestamp(
                frame_start, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "frame_end_utc": datetime.fromtimestamp(
                frame_end, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "query_runs": [],
        }

        if frame_end <= frame_start:
            self._stats["frame_not_open"] = True
            return []

        for spec in self.query_specs:
            query_started = self.clock()
            used = self.store.tap_usage(day, self.source_name)
            remaining = self.daily_read_limit - used
            if remaining < 10:
                self._stats["budget_exhausted"] = True
                break
            request_limit = min(self.per_query_limit, remaining)
            if not self.store.reserve_tap_usage(
                    day, self.source_name, request_limit,
                    self.daily_read_limit):
                self._stats["budget_exhausted"] = True
                break

            params = {
                "query": spec.query,
                "max_results": str(request_limit),
                "tweet.fields": "created_at,entities,author_id",
                "expansions": "author_id",
                "user.fields": "username",
                "start_time": datetime.fromtimestamp(
                    frame_start, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "end_time": datetime.fromtimestamp(
                    frame_end, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            }
            cursor = self.store.tap_state(self._cursor_key(spec))
            if cursor:
                params["since_id"] = cursor
            url = f"{X_RECENT_SEARCH_URL}?{urllib.parse.urlencode(params)}"

            self._stats["queries_attempted"] += 1
            try:
                payload = self.transport(url, self.bearer_token)
                posts = payload.get("data") or []
                if not isinstance(posts, list):
                    raise ValueError("X response data is not a list")
                if payload.get("errors"):
                    self.errors.append(
                        f"{spec.family}: X returned {len(payload['errors'])} API error(s)")
            except Exception as exc:
                # Keep the full reservation: a timeout may have delivered and
                # billed results even though Barometer never received them.
                failure_note = f"{type(exc).__name__}: {exc}"
                self.errors.append(f"{spec.family}: {failure_note}")
                run = CollectionRun(
                    run_id=f"x-{uuid.uuid4().hex}", source="x",
                    lane=spec.lane, query_id=spec.query_id,
                    query_version=X_QUERY_VERSION,
                    started_at=query_started, completed_at=self.clock(),
                    returned_candidates=0, retained_candidates=0,
                    item_cap=request_limit, saturated=False,
                    cost_units=request_limit,
                    cost_usd_upper_bound=request_limit * X_POST_READ_USD,
                    frame_note=(
                        "Ambiguous request failure; full reservation retained. "
                        + failure_note),
                )
                self.collection_batches.append(XCollectionBatch(run, (), ()))
                continue

            actual = len(posts)
            if actual > request_limit:
                self.errors.append(
                    f"{spec.family}: response exceeded reserved read allowance")
                continue
            self.store.adjust_tap_usage(
                day, self.source_name, actual - request_limit)
            self._stats["candidate_posts"] += actual
            if actual == request_limit:
                self._stats["saturated_queries"] += 1

            numeric_ids = [
                int(post["id"]) for post in posts
                if str(post.get("id", "")).isdigit()
            ]
            if numeric_ids:
                self.store.set_tap_state(
                    self._cursor_key(spec), str(max(numeric_ids)))

            includes = payload.get("includes") or {}
            usernames = {
                str(user.get("id")): str(user.get("username"))
                for user in (includes.get("users") or [])
                if user.get("id") and user.get("username")
            }

            query_complaints: list[Complaint] = []
            result_ranks: list[int] = []
            for rank, post in enumerate(posts, start=1):
                post_id = str(post.get("id", ""))
                text = _plain_text(str(post.get("text", "")))[:500]
                created_at = post.get("created_at")
                author_id = str(post.get("author_id") or "") or None
                author_handle = usernames.get(author_id) if author_id else None
                if not post_id or not text or not created_at:
                    continue
                if author_id in self.suppressed_author_ids:
                    self._stats["suppressed_candidates"] += 1
                    continue
                try:
                    ts = _x_timestamp(str(created_at))
                except ValueError:
                    self.errors.append(
                        f"{spec.family}: invalid timestamp for post {post_id}")
                    continue
                routed_model = route_model(text)
                if routed_model is None and infer_variant(spec.family, text):
                    routed_model = spec.family
                if (ts < frame_start or ts >= frame_end or not routed_model
                        or not self.retain_filter(text)):
                    continue
                complaint = Complaint(
                    ts=ts,
                    source="x",
                    model=routed_model,
                    text=text,
                    url=f"https://x.com/i/web/status/{post_id}",
                    seed_url=_x_seed_url(post),
                    variant=infer_variant(routed_model, text),
                    author_id=author_id,
                    author_handle=author_handle,
                )
                query_complaints.append(complaint)
                result_ranks.append(rank)
                if post_id not in seen:
                    seen.add(post_id)
                    out.append(complaint)

            run = CollectionRun(
                run_id=f"x-{uuid.uuid4().hex}", source="x",
                lane=spec.lane, query_id=spec.query_id,
                query_version=X_QUERY_VERSION,
                started_at=query_started, completed_at=self.clock(),
                returned_candidates=actual,
                retained_candidates=len(query_complaints),
                item_cap=request_limit, saturated=actual == request_limit,
                cost_units=actual,
                cost_usd_upper_bound=actual * X_POST_READ_USD,
                frame_note=(
                    f"Valence-neutral {spec.lane} query within one explicit "
                    f"UTC-day frame; {len(spec.term_ids)} governed LLT-like "
                    "term(s); X recent search is ranked, not a random platform "
                    "sample."),
            )
            self.collection_batches.append(XCollectionBatch(
                run, tuple(query_complaints), tuple(result_ranks)))
            self._stats["query_runs"].append({
                "query_id": spec.query_id,
                "lane": spec.lane,
                "term_ids": list(spec.term_ids),
                "concept_ids": list(spec.concept_ids),
                "returned_candidates": actual,
                "retained_candidates": len(query_complaints),
                "saturated": actual == request_limit,
            })

        self._stats["accepted_complaints"] = len(out)
        self._stats["budget_exhausted"] = (
            self._stats["budget_exhausted"]
            or self.store.tap_usage(day, self.source_name)
            >= self.daily_read_limit
        )
        return out

    def usage_report(self) -> dict:
        day = self._stats.get("day_utc") or self._day()
        used = self.store.tap_usage(day, self.source_name)
        return {
            **self._stats,
            "daily_read_limit": self.daily_read_limit,
            "read_units_upper_bound": used,
            "post_read_price_basis_usd": X_POST_READ_USD,
            "estimated_cost_usd_upper_bound": round(
                used * X_POST_READ_USD, 3),
        }
