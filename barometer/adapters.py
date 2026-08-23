"""Ingestion taps. Every adapter takes an injected transport
(callable url -> parsed JSON) so tests never touch the network and the
suite never spends anyone's money — house law since the incident.

Live transports are provided but nothing here calls them by default."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import html
import json
import re
import time
import urllib.parse
import urllib.request
from .detect import Complaint
from .catalog import infer_variant

USER_AGENT = "the-barometer/0.1 (fleet weather, not verdicts)"
X_POST_READ_USD = 0.005
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_QUERIES = {
    "claude": (
        '(Claude OR Anthropic OR Sonnet OR Opus) '
        '(worse OR degraded OR broken OR slow OR nerfed OR lazy OR dumb '
        'OR "off today" OR "anyone else") lang:en -is:retweet'
    ),
    "gpt": (
        '(ChatGPT OR "GPT-5" OR "GPT-4" OR OpenAI) '
        '(worse OR degraded OR broken OR slow OR nerfed OR lazy OR dumb '
        'OR "off today" OR "anyone else") lang:en -is:retweet'
    ),
    "gemini": (
        '(Gemini OR "Google AI" OR Bard) '
        '(worse OR degraded OR broken OR slow OR nerfed OR lazy OR dumb '
        'OR "off today" OR "anyone else") lang:en -is:retweet'
    ),
}

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
}
COMPLAINT_HINTS = ["worse", "nerf", "dumb", "lazy", "slow", "degrad", "refus",
                   "shorter", "off today", "quality", "stupid", "broken",
                   "downgrade", "since the update", "anyone else"]

def route_model(text: str) -> str | None:
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
        self.queries = queries or ["claude worse", "chatgpt worse",
                                   "gpt nerfed", "claude degraded",
                                   "gemini worse"]
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
        self.queries = queries or X_QUERIES
        self.daily_read_limit = daily_read_limit
        fair_share = max(10, daily_read_limit // max(1, len(self.queries)))
        self.per_query_limit = min(per_query_limit, fair_share)
        self.transport = transport
        self.clock = clock
        self.errors: list[str] = []
        self._stats: dict = {}

    def _day(self) -> str:
        return datetime.fromtimestamp(
            self.clock(), tz=timezone.utc).date().isoformat()

    @staticmethod
    def _cursor_key(model: str) -> str:
        return f"x:recent:v1:{model}:since_id"

    def fetch(self, since: float) -> list[Complaint]:
        out: list[Complaint] = []
        seen: set[str] = set()
        day = self._day()
        self.errors = []
        self._stats = {
            "day_utc": day,
            "queries_attempted": 0,
            "candidate_posts": 0,
            "accepted_complaints": 0,
            "saturated_queries": 0,
            "budget_exhausted": False,
        }

        for model, query in self.queries.items():
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
                "query": query,
                "max_results": str(request_limit),
                "tweet.fields": "created_at,entities",
            }
            cursor = self.store.tap_state(self._cursor_key(model))
            if cursor:
                params["since_id"] = cursor
            else:
                # Recent search only covers seven days; stay just inside its
                # boundary even when the detector's window is longer.
                earliest = max(since, self.clock() - (7 * 86400 - 300))
                params["start_time"] = datetime.fromtimestamp(
                    earliest, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            url = f"{X_RECENT_SEARCH_URL}?{urllib.parse.urlencode(params)}"

            self._stats["queries_attempted"] += 1
            try:
                payload = self.transport(url, self.bearer_token)
                posts = payload.get("data") or []
                if not isinstance(posts, list):
                    raise ValueError("X response data is not a list")
                if payload.get("errors"):
                    self.errors.append(
                        f"{model}: X returned {len(payload['errors'])} API error(s)")
            except Exception as exc:
                # Keep the full reservation: a timeout may have delivered and
                # billed results even though Barometer never received them.
                self.errors.append(f"{model}: {type(exc).__name__}: {exc}")
                continue

            actual = len(posts)
            if actual > request_limit:
                self.errors.append(
                    f"{model}: response exceeded reserved read allowance")
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
                    self._cursor_key(model), str(max(numeric_ids)))

            for post in posts:
                post_id = str(post.get("id", ""))
                text = _plain_text(str(post.get("text", "")))[:500]
                created_at = post.get("created_at")
                if not post_id or post_id in seen or not text or not created_at:
                    continue
                seen.add(post_id)
                try:
                    ts = _x_timestamp(str(created_at))
                except ValueError:
                    self.errors.append(f"{model}: invalid timestamp for post {post_id}")
                    continue
                routed_model = route_model(text)
                if ts < since or not routed_model or not looks_like_complaint(text):
                    continue
                out.append(Complaint(
                    ts=ts,
                    source="x",
                    model=routed_model,
                    text=text,
                    url=f"https://x.com/i/web/status/{post_id}",
                    seed_url=_x_seed_url(post),
                    variant=infer_variant(routed_model, text),
                ))

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
