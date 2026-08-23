from __future__ import annotations
import math
from dataclasses import dataclass, field
from collections import defaultdict

HOUR = 3600

@dataclass
class Complaint:
    ts: float            # unix seconds
    source: str          # e.g. "reddit", "x", "hn", "discord"
    model: str           # e.g. "claude", "gpt", "gemini"
    text: str
    url: str | None = None
    seed_url: str | None = None   # link the post is sharing, if any

@dataclass
class CanaryReading:
    ts: float
    model: str
    logprobs: list[float]         # per-token logprobs of the FIXED canary text
    fingerprint: str | None = None

@dataclass
class ProviderEvent:
    ts: float
    model: str
    kind: str                     # "release" | "acknowledgment" | "incident"
    note: str = ""

@dataclass
class Burst:
    model: str
    start: float
    end: float
    observed: int
    expected: float
    zscore: float
    sources: dict = field(default_factory=dict)
    independent_sources: int = 0
    near_release: bool = False

@dataclass
class Assessment:
    burst: Burst
    tier: int                     # 0 none, 1 perceived, 2 corroborated, 3 attributed
    drift: float | None
    fingerprint_changed: bool
    acknowledged: bool
    summary: str

# ---------------- taxonomy (keyword MVP; swap for a classifier later) ----
TAXONOMY = {
    "sluggish":  ["slow", "sluggish", "lag", "taking forever", "latency"],
    "lazy":      ["lazy", "refuses to finish", "half the answer", "truncat"],
    "quality":   ["dumber", "worse", "stupid", "quality dropped", "nerfed",
                  "degraded", "off today"],
    "refusals":  ["refus", "won't answer", "lectur", "moraliz"],
    "length":    ["shorter", "brief", "cut off", "less detailed"],
}
def classify(text: str) -> list[str]:
    t = text.lower()
    return [k for k, kws in TAXONOMY.items() if any(w in t for w in kws)] or ["other"]

# ---------------- cascade dedup ----------------
def _shingles(text: str, k: int = 4) -> set:
    toks = text.lower().split()
    if len(toks) < k: return {tuple(toks)}
    return {tuple(toks[i:i+k]) for i in range(len(toks)-k+1)}

def _jaccard(a: set, b: set) -> float:
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

def cascade_clusters(complaints: list[Complaint], sim: float = 0.55) -> list[list[Complaint]]:
    """Group near-duplicate or same-seed complaints: one viral post quoted
    across four platforms is ONE datum, not four."""
    clusters: list[list] = []   # [shingle_set, seed_url, members]
    for c in complaints:
        sh = _shingles(c.text)
        placed = False
        for entry in clusters:
            csh, seed, members = entry
            if (c.seed_url and seed and c.seed_url == seed) or _jaccard(sh, csh) >= sim:
                members.append(c); entry[0] = csh | sh; placed = True; break
        if not placed:
            clusters.append([sh, c.seed_url, [c]])
    return [m for _, _, m in clusters]

def independence_score(complaints: list[Complaint]) -> dict:
    """Distinct sources contributing at least one INDEPENDENT
    (cluster-origin) complaint. Cross-community independence is worth
    50x raw volume."""
    per_source: dict = defaultdict(int)
    for cluster in cascade_clusters(complaints):
        per_source[cluster[0].source] += 1
    return dict(per_source)

# ---------------- baseline & bursts ----------------
def _expected_rate(history: list[float], now: float, window_hours: float) -> float:
    lo = now - window_hours * HOUR
    recent = [t for t in history if lo <= t < now]
    if not history: return 0.05
    span_h = max(1.0, min(window_hours, (now - min(history)) / HOUR))
    return max(0.05, len(recent) / span_h)

def detect_bursts(complaints: list[Complaint], events: list[ProviderEvent],
                  z: float = 3.0, min_events: int = 5,
                  bin_hours: int = 3, window_hours: float = 24*14) -> list[Burst]:
    by_model: dict = defaultdict(list)
    for c in complaints: by_model[c.model].append(c)
    bursts: list[Burst] = []
    for model, cs in by_model.items():
        cs.sort(key=lambda c: c.ts)
        clusters = cascade_clusters(cs)               # dedup BEFORE counting
        ctimes = sorted(cl[0].ts for cl in clusters)
        if not ctimes: continue
        t = ctimes[0]
        while t <= ctimes[-1]:
            hi = t + bin_hours * HOUR
            in_bin = [x for x in ctimes if t <= x < hi]
            history = [x for x in ctimes if x < t]
            if len(history) >= 10 and in_bin and len(in_bin) >= min_events:
                exp = _expected_rate(history, t, window_hours) * bin_hours
                zscore = (len(in_bin) - exp) / math.sqrt(max(exp, 0.05))
                near_rel = any(e.kind == "release" and e.model == model
                               and abs(e.ts - t) < 48*HOUR for e in events)
                threshold = z * (2.0 if near_rel else 1.0)  # releases ALWAYS spike
                if zscore >= threshold:
                    window_cs = [c for c in cs if t <= c.ts < hi]
                    srcs = independence_score(window_cs)
                    bursts.append(Burst(
                        model=model, start=t, end=hi,
                        observed=len(in_bin), expected=round(exp, 2),
                        zscore=round(zscore, 2), sources=srcs,
                        independent_sources=len(srcs),
                        near_release=near_rel))
            t = hi
    return bursts

# ---------------- canary drift (distribution shape, not content) --------
def canary_drift(before: CanaryReading, after: CanaryReading) -> float:
    """Symmetric mean absolute logprob shift on the fixed canary text.
    Quantization and serving changes measurably move this; the canary is
    never asked a question."""
    n = min(len(before.logprobs), len(after.logprobs))
    if n == 0: return 0.0
    return sum(abs(a - b) for a, b in zip(before.logprobs[:n], after.logprobs[:n])) / n

# ---------------- tiering ----------------
def classify_tier(burst: Burst, readings: list[CanaryReading],
                  events: list[ProviderEvent],
                  drift_threshold: float = 0.15) -> Assessment:
    rel = sorted((r for r in readings if r.model == burst.model), key=lambda r: r.ts)
    pre = [r for r in rel if r.ts < burst.start]
    post = [r for r in rel if burst.start <= r.ts < burst.end + 24*HOUR]
    drift = canary_drift(pre[-1], post[0]) if pre and post else None
    fp_changed = bool(pre and post and pre[-1].fingerprint and post[0].fingerprint
                      and pre[-1].fingerprint != post[0].fingerprint)
    ack = any(e.kind == "acknowledgment" and e.model == burst.model
              and burst.start - 24*HOUR <= e.ts <= burst.end + 7*24*HOUR
              for e in events)
    tier = 0
    if burst.independent_sources >= 2: tier = 1
    if tier >= 1 and drift is not None and drift >= drift_threshold: tier = 2
    if tier >= 1 and (fp_changed or ack): tier = 3
    labels = {0: "insufficient independence — possible cascade",
              1: "T1 PERCEIVED: independent multi-community burst",
              2: "T2 CORROBORATED: burst + objective canary drift",
              3: "T3 ATTRIBUTED: provider fingerprint change or acknowledgment"}
    note = " (near release: elevated complaints expected — comparison effect)" \
           if burst.near_release else ""
    return Assessment(burst=burst, tier=tier, drift=drift,
                      fingerprint_changed=fp_changed, acknowledged=ack,
                      summary=labels[tier] + note)
