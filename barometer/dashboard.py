from __future__ import annotations
from collections import Counter
import html
from datetime import datetime, timezone
from .detect import Assessment, Complaint, cascade_clusters, HOUR

TIER_STYLE = {
    0: ("#8a8f98", "CALM / CASCADE"),
    1: ("#e0b350", "T1 PERCEIVED"),
    2: ("#e07850", "T2 CORROBORATED"),
    3: ("#d94f4f", "T3 ATTRIBUTED"),
}

def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _sparkline(counts: list[int], w: int = 560, h: int = 60) -> str:
    if not counts: return ""
    mx = max(max(counts), 1)
    bw = w / len(counts)
    bars = "".join(
        f'<rect x="{i*bw:.1f}" y="{h - (c/mx)*h:.1f}" width="{max(bw-1,1):.1f}" '
        f'height="{(c/mx)*h:.1f}" fill="#5b7f95"/>'
        for i, c in enumerate(counts))
    return f'<svg width="{w}" height="{h}" style="background:#141821;border-radius:6px">{bars}</svg>'

def render_dashboard(model: str, complaints: list[Complaint],
                     assessments: list[Assessment], out_path: str,
                     bin_hours: int = 3) -> None:
    cs = sorted((c for c in complaints if c.model == model), key=lambda c: c.ts)
    clusters = cascade_clusters(cs)
    source_summary = " · ".join(
        f"{source} × {count}"
        for source, count in sorted(Counter(c.source for c in cs).items())
    ) or "—"
    ctimes = sorted(cl[0].ts for cl in clusters)
    counts = []
    if ctimes:
        t = ctimes[0]
        while t <= ctimes[-1]:
            counts.append(sum(1 for x in ctimes if t <= x < t + bin_hours*HOUR))
            t += bin_hours*HOUR
    cards = ""
    for a in sorted(assessments, key=lambda a: -a.burst.start):
        colour, label = TIER_STYLE[a.tier]
        srcs = ", ".join(f"{k}×{v}" for k, v in a.burst.sources.items()) or "—"
        drift = f"{a.drift:.3f}" if a.drift is not None else "no canary pair"
        cards += f"""
        <div class="card" style="border-left:5px solid {colour}">
          <div class="tier" style="color:{colour}">{label}</div>
          <div class="when">{_fmt(a.burst.start)} → {_fmt(a.burst.end)}</div>
          <div class="stats">observed <b>{a.burst.observed}</b> independent clusters
            vs expected <b>{a.burst.expected}</b> (z={a.burst.zscore})<br>
            sources: {html.escape(srcs)} · canary drift: {drift}
            · fingerprint changed: {a.fingerprint_changed} · provider ack: {a.acknowledged}</div>
          <div class="summary">{html.escape(a.summary)}</div>
        </div>"""
    page = f"""<!doctype html><meta charset="utf-8">
<title>The Barometer — {html.escape(model)}</title>
<style>
 body{{background:#0d1017;color:#cfd6e0;font:15px/1.5 system-ui;margin:0;padding:32px;max-width:760px;margin:auto}}
 h1{{font-weight:600;letter-spacing:.5px}} h1 small{{color:#69707c;font-weight:400}}
 .card{{background:#161b24;border-radius:8px;padding:14px 18px;margin:14px 0}}
 .tier{{font-weight:700;letter-spacing:1px;font-size:13px}}
 .when{{color:#8a92a0;font-size:13px;margin:2px 0 6px}}
 .stats{{font-size:14px}} .summary{{margin-top:8px;color:#aeb6c2;font-style:italic}}
 .sources{{color:#8a92a0;font-size:13px;margin-top:-8px}}
 .ethic{{margin-top:36px;color:#69707c;font-size:13px;border-top:1px solid #232a36;padding-top:14px}}
</style>
<h1>☂ The Barometer <small>· {html.escape(model)} · fleet weather, not verdicts</small></h1>
<p>{len(cs)} raw reports · {len(clusters)} independent after cascade dedup</p>
<p class="sources">source mix: {html.escape(source_summary)}</p>
{_sparkline(counts)}
{cards if cards else '<div class="card">No bursts above baseline. Ordinary weather.</div>'}
<div class="ethic">This instrument reports that <b>something changed</b> — it cannot
and does not claim what: weights, quantization, serving config, and product-layer
prompts are indistinguishable from outside. No model was quizzed in the taking of
these readings; canaries measure distribution shape on a fixed benign text only.
Complaints are public posts, aggregated, cascade-deduplicated.</div>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
