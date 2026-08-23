from __future__ import annotations
from collections import Counter
import html
from datetime import datetime, timezone
from .catalog import PREVIEW_DATA_NOTE, model_catalog_entry, variant_breakdown
from .detect import Assessment, Complaint, cascade_clusters, classify, HOUR

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


def _activity_status(assessments: list[Assessment]) -> tuple[str, str, str]:
    highest = max((assessment.tier for assessment in assessments), default=0)
    if highest >= 3:
        return "attributed", "Provider-attributed", "#ef6a6a"
    if highest >= 2:
        return "corroborated", "Corroborated anomaly", "#ef8f63"
    if highest >= 1:
        return "attention", "Perceived shift", "#e8b85b"
    if assessments:
        return "uncorroborated", "Uncorroborated burst", "#9db0bf"
    return "quiet", "No burst detected", "#7aa78d"


def _weather_style(status: str, categories: Counter) -> tuple[str, str]:
    """Choose illustrative weather without changing the underlying signal tier."""
    if status in {"corroborated", "attributed"}:
        return "storm", "electrical storm"
    if not categories:
        return "clear", "clear night"
    dominant = max(
        categories.items(), key=lambda item: (item[0] != "other", item[1])
    )[0]
    weather = {
        "sluggish": ("fog", "fog bank"),
        "lazy": ("heat", "heat haze"),
        "length": ("heat", "heat haze"),
        "refusals": ("storm", "electrical storm"),
        "quality": ("rain", "rain front"),
        "other": ("overcast", "overcast"),
    }.get(dominant, ("overcast", "overcast"))
    if status == "attention" and weather[0] == "overcast":
        return "rain", "rain front"
    return weather


CLOUD_FILLER_WORDS = (
    "the", "and", "it", "this", "that", "was", "is", "seems",
    "today", "again", "response", "answer", "model", "prompt",
    "output", "context", "maybe", "usually",
)


def render_landing(
        models: dict[str, tuple[list[Complaint], list[Assessment]]],
        out_path: str, generated_at: float, window_days: int) -> None:
    """Render the public, aggregate-only entry point across model families."""
    ranked = sorted(
        models.items(),
        key=lambda item: (-len(item[1][0]), item[0]),
    )
    all_complaints = [
        complaint
        for complaints, _ in models.values()
        for complaint in complaints
    ]
    total_reports = len(all_complaints)
    total_independent = sum(
        len(cascade_clusters(complaints))
        for complaints, _ in models.values()
    )
    all_sources = Counter(c.source.split("/", 1)[0] for c in all_complaints)
    corroborated = sum(
        1 for _, assessments in models.values()
        if max((a.tier for a in assessments), default=0) >= 2
    )
    labs = sorted({model_catalog_entry(model)["lab"] for model in models})

    cards = []
    for rank, (model, (complaints, assessments)) in enumerate(ranked, start=1):
        meta = model_catalog_entry(model)
        clusters = cascade_clusters(complaints)
        status_key, status_label, status_colour = _activity_status(assessments)
        sources = Counter(c.source.split("/", 1)[0] for c in complaints)
        categories = Counter(
            category
            for complaint in complaints
            for category in classify(complaint.text)
        )
        category_items = sorted(
            categories.items(), key=lambda item: (item[0] == "other", -item[1], item[0])
        )[:4]
        weather_key, weather_label = _weather_style(status_key, categories)
        source_text = " · ".join(
            f"{html.escape(source.upper())} {count}"
            for source, count in sorted(sources.items())
        ) or "No source data"
        category_label = ", ".join(
            f"{category} {count}" for category, count in category_items
        ) or "No report themes yet"
        max_category_count = max((count for _, count in category_items), default=1)
        category_words = "".join(
            f'<span class="cloud-word" style="--word-size:'
            f'{12 + round((count / max_category_count) * 9)}px">'
            f'{html.escape(category)} <b>{count}</b></span>'
            for category, count in category_items
        ) or '<span class="cloud-word clear">clear skies</span>'
        filler_words = "".join(
            f"<span>{html.escape(word)}</span>"
            for _ in range(8)
            for word in CLOUD_FILLER_WORDS
        )
        category_html = f"""
          <div class="cloud-heading">
            <span class="breakdown-label">Report weather · {html.escape(weather_label)}</span>
            <span class="cloud-key">word size = report frequency</span>
          </div>
          <div class="category-cloud" role="img"
            aria-label="Reported themes: {html.escape(category_label, quote=True)}">
            <div class="cloud-filler" aria-hidden="true">{filler_words}</div>
            <div class="cloud-words" aria-hidden="true">{category_words}</div>
          </div>"""
        terms = tuple(meta["recognised_terms"])
        breakdown = variant_breakdown(model, complaints)
        breakdown_parts = []
        family_total = max(len(complaints), 1)
        for item in breakdown:
            tone = " monitored" if item["monitored"] else " residual"
            bar_width = round((item["reports"] / family_total) * 100)
            breakdown_parts.append(f"""
              <div class="model-row{tone}">
                <div class="model-row-head"><span>{html.escape(item['label'])}</span><b>{item['reports']}</b></div>
                <div class="model-volume"><span style="width:{bar_width}%"></span></div>
              </div>""")
        breakdown_html = "".join(breakdown_parts)
        variant_terms = tuple(item["label"] for item in breakdown)
        search_terms = " ".join(
            (model, meta["label"], meta["lab"], *terms, *variant_terms)
        ).lower()
        latest = max((c.ts for c in complaints), default=0)
        cards.append(f"""
        <article class="model-card weather-{weather_key}"
          style="--status-colour:{status_colour}"
          data-model="{html.escape(model)}"
          data-lab="{html.escape(meta['lab'].lower())}"
          data-status="{status_key}" data-reports="{len(complaints)}"
          data-independent="{len(clusters)}" data-latest="{latest:.0f}"
          data-search="{html.escape(search_terms, quote=True)}">
          <div class="weather-scene" aria-hidden="true"><span class="weather-motion"></span></div>
          <div class="rank" aria-label="Rank {rank}">{rank:02d}</div>
          <div class="card-main">
            <div class="card-heading">
              <div>
                <div class="lab">{html.escape(meta['lab'])}</div>
                <h3>{html.escape(meta['label'])}</h3>
              </div>
              <span class="status">
                <i></i>{html.escape(status_label)}
              </span>
            </div>
            <div class="report-line">
              <strong>{len(complaints)}</strong>
              <span>reports in the last {window_days} days</span>
            </div>
            <div class="meta-line">
              <span>{len(clusters)} independent after deduplication</span>
              <span>{source_text}</span>
            </div>
            <div class="category-weather">{category_html}</div>
            <div class="breakdown">
              <span class="breakdown-label">Reports by monitored model</span>
              <div class="model-bars">{breakdown_html}</div>
            </div>
          </div>
          <a class="detail-link" href="barometer_{html.escape(model)}.html"
             aria-label="Open {html.escape(meta['label'])} detail">View detail <span>→</span></a>
        </article>""")

    lab_buttons = "".join(
        f'<button type="button" class="filter-chip" data-lab-filter="{html.escape(lab.lower())}">{html.escape(lab)}</button>'
        for lab in labs
    )
    updated = _fmt(generated_at)
    source_summary = " · ".join(
        f"{html.escape(source.upper())} {count}"
        for source, count in sorted(all_sources.items())
    ) or "No source data yet"
    fleet_reading = (
        f"{corroborated} corroborated signal{'s' if corroborated != 1 else ''}"
        if corroborated else "No corroborated signals"
    )
    empty_message = "" if cards else "Nothing has been observed yet."
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Barometer — AI model weather</title>
<style>
:root{{--ink:#e7ecf1;--muted:#8d99a6;--faint:#5f6a76;--paper:#0b0f14;
  --panel:#121821;--panel-2:#171f29;--line:#26313d;--blue:#7eb2c8;
  --amber:#e8b85b;--green:#7aa78d;--shadow:0 18px 44px rgba(0,0,0,.22)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
a{{color:inherit}} .shell{{width:min(1180px,calc(100% - 40px));margin:auto}}
header{{padding:30px 0 12px;border-bottom:1px solid rgba(255,255,255,.06)}}
.nav{{display:flex;align-items:center;justify-content:space-between;gap:20px}}
.brand{{font-size:19px;font-weight:750;letter-spacing:.01em;text-decoration:none}}
.brand span{{color:var(--blue)}} .nav-note{{color:var(--muted);font-size:13px}}
.report-button{{border:1px solid var(--line);border-radius:999px;padding:8px 13px;
  color:#c4d1da;font-size:13px;text-decoration:none}} .report-button:hover{{border-color:var(--blue);color:#fff}}
.hero{{padding:72px 0 44px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(270px,.7fr);gap:56px;align-items:end}}
.eyebrow,.section-kicker,.lab{{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}}
h1{{font-size:clamp(42px,7vw,78px);line-height:.96;letter-spacing:-.055em;margin:14px 0 24px;max-width:820px}}
.hero p{{font-size:18px;color:#aab4bf;max-width:700px;margin:0}}
.weather-box{{background:linear-gradient(145deg,#151d27,#10161e);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:var(--shadow)}}
.weather-box strong{{display:block;font-size:42px;line-height:1;margin:8px 0}}
.weather-box span{{color:var(--muted);font-size:13px}}
.stats-row{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:14px}}
.stat{{padding:19px 21px;background:rgba(18,24,33,.72);border-right:1px solid var(--line)}} .stat:last-child{{border:0}}
.stat b{{font-size:24px;display:block}} .stat span{{color:var(--muted);font-size:12px}}
.data-note{{margin:0 0 66px;padding:11px 14px;border:1px solid #4a4028;background:#17150e;border-radius:10px;color:#aa9d7f;font-size:12px}} .data-note b{{color:#e8c879}}
.section-head{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:20px}}
h2{{font-size:32px;letter-spacing:-.035em;margin:6px 0 0}} .updated{{color:var(--muted);font-size:12px;text-align:right}}
.controls{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:18px}}
.control-row{{display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:12px}}
input,select{{width:100%;color:var(--ink);background:#0e141b;border:1px solid #303c48;border-radius:10px;padding:11px 13px;font:inherit;outline:none}}
input:focus,select:focus{{border-color:var(--blue);box-shadow:0 0 0 3px rgba(126,178,200,.12)}}
select{{min-width:170px}} .lab-filters{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px}}
.filter-chip{{color:var(--muted);background:transparent;border:1px solid var(--line);border-radius:999px;padding:6px 10px;cursor:pointer}}
.filter-chip.active{{color:var(--paper);background:var(--blue);border-color:var(--blue);font-weight:750}}
.results-meta{{color:var(--muted);font-size:12px;margin-left:auto}}
.model-list{{display:grid;gap:14px}}
.model-card{{position:relative;isolation:isolate;overflow:hidden;display:grid;grid-template-columns:46px minmax(0,1fr) auto;gap:18px;align-items:stretch;background:#101720;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}} .model-card>:not(.weather-scene){{position:relative;z-index:2}}
.weather-scene{{position:absolute;inset:0;z-index:0;overflow:hidden;pointer-events:none}} .weather-scene::after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(9,14,20,.38),rgba(9,14,20,.74) 74%,rgba(9,14,20,.9)),linear-gradient(0deg,rgba(8,12,17,.34),transparent 58%)}} .weather-motion{{position:absolute;display:block;pointer-events:none}}
.weather-rain .weather-scene{{background:radial-gradient(ellipse at 14% 4%,rgba(105,139,158,.42),transparent 42%),radial-gradient(ellipse at 70% 120%,rgba(47,76,94,.4),transparent 62%),linear-gradient(145deg,#1b2a35,#0c131b 72%)}} .weather-rain .weather-motion{{inset:-60% -20%;opacity:.28;background:repeating-linear-gradient(111deg,transparent 0 17px,rgba(187,220,234,.52) 18px 19px,transparent 20px 34px);animation:rain-fall 1.8s linear infinite}}
.weather-fog .weather-scene{{background:linear-gradient(150deg,#26323a,#101820 70%)}} .weather-fog .weather-motion{{inset:5% -28%;opacity:.34;filter:blur(14px);background:radial-gradient(ellipse at 28% 34%,rgba(213,225,226,.52),transparent 32%),radial-gradient(ellipse at 66% 72%,rgba(163,181,187,.44),transparent 38%);animation:fog-bank 14s ease-in-out infinite alternate}}
.weather-storm .weather-scene{{background:radial-gradient(circle at 76% 12%,rgba(125,123,171,.32),transparent 24%),radial-gradient(ellipse at 12% 115%,rgba(41,72,91,.4),transparent 60%),linear-gradient(142deg,#202638,#090e17 74%)}} .weather-storm .weather-motion{{right:15%;top:-12px;width:92px;height:175px;background:linear-gradient(180deg,#f6f0bf,#b4d8e8 58%,transparent);clip-path:polygon(52% 0,22% 48%,46% 46%,29% 100%,76% 37%,54% 40%);filter:drop-shadow(0 0 18px rgba(218,235,255,.8));animation:lightning 7s steps(1,end) infinite}}
.weather-heat .weather-scene{{background:radial-gradient(circle at 16% 18%,rgba(235,154,81,.43),transparent 29%),radial-gradient(ellipse at 76% 110%,rgba(156,70,52,.38),transparent 54%),linear-gradient(145deg,#38271e,#171218 72%)}} .weather-heat .weather-motion{{inset:-30% -10%;opacity:.2;filter:blur(12px);background:repeating-linear-gradient(92deg,transparent 0 42px,rgba(255,196,124,.6) 48px,transparent 58px);animation:heat-rise 8s ease-in-out infinite alternate}}
.weather-overcast .weather-scene{{background:radial-gradient(ellipse at 12% 12%,rgba(123,141,151,.36),transparent 33%),radial-gradient(ellipse at 68% 2%,rgba(80,99,111,.32),transparent 38%),linear-gradient(145deg,#202b33,#10171e 72%)}} .weather-overcast .weather-motion{{inset:0;opacity:.2;background:radial-gradient(ellipse at 22% 22%,#c4d0d4,transparent 19%),radial-gradient(ellipse at 55% 8%,#8499a3,transparent 23%);animation:fog-bank 18s ease-in-out infinite alternate}}
.weather-clear .weather-scene{{background:radial-gradient(circle at 14% 110%,rgba(48,105,117,.38),transparent 45%),linear-gradient(145deg,#111d2d,#090d16 76%)}} .weather-clear .weather-motion{{inset:0;opacity:.55;background:radial-gradient(circle at 12% 22%,#d8edf2 0 1px,transparent 1.5px),radial-gradient(circle at 28% 10%,#a8d1db 0 1px,transparent 1.5px),radial-gradient(circle at 44% 31%,#e7f4f6 0 1px,transparent 1.5px),radial-gradient(circle at 65% 16%,#a8c7d5 0 1px,transparent 1.5px),radial-gradient(circle at 82% 35%,#d5e6ec 0 1px,transparent 1.5px);background-size:190px 150px}}
.model-card[hidden]{{display:none}} .rank{{color:#52606d;font:700 15px/1 ui-monospace,monospace;padding-top:6px}}
.card-heading{{display:flex;justify-content:space-between;align-items:start;gap:18px}} h3{{font-size:26px;letter-spacing:-.025em;margin:2px 0 0}}
.status{{display:inline-flex;align-items:center;gap:7px;color:#c4ccd3;font-size:12px;white-space:nowrap}}
.status i{{width:8px;height:8px;border-radius:50%;background:var(--status-colour);box-shadow:0 0 0 4px color-mix(in srgb,var(--status-colour) 14%,transparent)}}
.report-line{{display:flex;align-items:baseline;gap:9px;margin:19px 0 8px}} .report-line strong{{font-size:34px;line-height:1}} .report-line span{{color:var(--muted)}}
.meta-line{{display:flex;justify-content:space-between;gap:16px;color:var(--muted);font-size:12px;margin:9px 0 14px}}
.category-weather{{width:min(500px,100%);margin:15px 0 2px}} .cloud-heading{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:-2px}} .cloud-key{{color:#71808d;font-size:10px}}
.category-cloud{{position:relative;width:100%;aspect-ratio:3.5/1;isolation:isolate;filter:drop-shadow(0 12px 15px rgba(0,0,0,.2));animation:cloud-drift 11s ease-in-out infinite alternate}}
.cloud-filler{{position:absolute;inset:0;display:flex;align-content:center;justify-content:center;flex-wrap:wrap;gap:1px 5px;padding:3px 9px;overflow:hidden;color:color-mix(in srgb,var(--status-colour) 48%,#c3d1d7);font-size:9.5px;font-weight:560;line-height:1.05;letter-spacing:.01em;opacity:.62;mix-blend-mode:screen;text-shadow:0 1px 7px rgba(0,0,0,.8);-webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 410 112'%3E%3Cpath fill='white' d='M72%20104C36%20104%2012%2087%2012%2064c0-21%2021-39%2051-42C76%207%2096%200%20120%200c32%200%2059%2017%2070%2043%2014-12%2032-19%2053-19%2036%200%2066%2023%2073%2055%208-5%2019-7%2030-7%2029%200%2052%2014%2052%2032H72Z'/%3E%3C/svg%3E") center/100% 100% no-repeat;mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 410 112'%3E%3Cpath fill='white' d='M72%20104C36%20104%2012%2087%2012%2064c0-21%2021-39%2051-42C76%207%2096%200%20120%200c32%200%2059%2017%2070%2043%2014-12%2032-19%2053-19%2036%200%2066%2023%2073%2055%208-5%2019-7%2030-7%2029%200%2052%2014%2052%2032H72Z'/%3E%3C/svg%3E") center/100% 100% no-repeat}} .cloud-filler span:nth-child(3n){{opacity:.6}} .cloud-filler span:nth-child(5n){{font-size:1.12em}}
.cloud-words{{position:absolute;inset:0;color:#edf3f5;text-shadow:0 1px 8px rgba(0,0,0,.72)}} .cloud-word{{position:absolute;font-size:var(--word-size,12px);line-height:1;font-weight:700;letter-spacing:-.015em;white-space:nowrap;transform:translate(-50%,-50%)}} .cloud-word b{{font-size:.52em;color:color-mix(in srgb,var(--status-colour) 72%,#fff);vertical-align:super;margin-left:2px}} .cloud-word:nth-child(1){{left:47%;top:40%}} .cloud-word:nth-child(2){{left:27%;top:64%}} .cloud-word:nth-child(3){{left:70%;top:62%}} .cloud-word:nth-child(4){{left:49%;top:78%}} .cloud-word.clear{{left:50%;top:62%;color:#b8c6cc;font-weight:560;font-style:italic}}
@keyframes cloud-drift{{from{{transform:translate3d(-2px,1px,0)}}to{{transform:translate3d(3px,-1px,0)}}}}
@keyframes rain-fall{{from{{transform:translate3d(0,-8%,0)}}to{{transform:translate3d(-5%,16%,0)}}}}
@keyframes fog-bank{{from{{transform:translate3d(-4%,0,0)}}to{{transform:translate3d(5%,1%,0)}}}}
@keyframes heat-rise{{from{{transform:translate3d(0,4%,0) scaleX(1)}}to{{transform:translate3d(2%,-4%,0) scaleX(1.04)}}}}
@keyframes lightning{{0%,88%,92%,100%{{opacity:0}}89%{{opacity:.9}}90%{{opacity:.18}}91%{{opacity:.75}}}}
.breakdown{{margin-top:14px}} .breakdown-label{{display:block;color:var(--faint);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin-bottom:7px}}
.model-bars{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 16px}} .model-row-head{{display:flex;justify-content:space-between;gap:12px;color:#bdd0da;font-size:11px;margin-bottom:4px}} .model-row-head b{{color:#fff}} .model-volume{{height:6px;background:#0a1016;border-radius:99px;overflow:hidden}} .model-volume span{{display:block;height:100%;min-width:0;background:linear-gradient(90deg,#527b8e,var(--blue));border-radius:inherit}} .model-row.residual .model-row-head{{color:var(--muted)}} .model-row.residual .model-volume span{{background:#58636d}}
.detail-link{{align-self:center;text-decoration:none;border-left:1px solid var(--line);padding:22px 4px 22px 24px;color:#b7c1ca;white-space:nowrap}}
.detail-link span{{color:var(--blue);margin-left:5px}} .detail-link:hover{{color:#fff}}
.empty{{padding:40px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:14px}}
.family-note{{margin:20px 0 70px;color:var(--muted);font-size:13px;max-width:760px}}
.method{{display:grid;grid-template-columns:.8fr 1.2fr;gap:56px;padding:54px 0 70px;border-top:1px solid var(--line)}}
.method p{{color:var(--muted);margin-top:0}} .method-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.method-card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.method-card b{{display:block;margin-bottom:5px}} .method-card span{{color:var(--muted);font-size:12px}}
footer{{border-top:1px solid var(--line);padding:24px 0 42px;color:var(--faint);font-size:12px}}
@media(prefers-reduced-motion:reduce){{.category-cloud,.weather-motion{{animation:none!important}}}}
@media(max-width:780px){{.hero{{grid-template-columns:1fr;padding-top:50px}}.stats-row{{grid-template-columns:1fr 1fr}}.stat:nth-child(2){{border-right:0}}.stat{{border-bottom:1px solid var(--line)}}.control-row{{grid-template-columns:1fr}}.model-card{{grid-template-columns:32px 1fr}}.detail-link{{grid-column:2;border-left:0;border-top:1px solid var(--line);padding:14px 0 0}}.meta-line,.section-head{{align-items:start;flex-direction:column}}.model-bars{{grid-template-columns:1fr}}.method{{grid-template-columns:1fr}}.method-grid{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div class="shell nav">
  <a class="brand" href="index.html"><span>☂</span> The Barometer</a>
  <div class="nav-note">fleet weather, not verdicts</div>
  <a class="report-button" href="report.html">Report an issue →</a>
</div></header>
<main class="shell">
  <section class="hero">
    <div><div class="eyebrow">AI model weather</div>
      <h1>Is it just you—or is something shifting?</h1>
      <p>Barometer gathers public reports across independent sources, collapses viral echoes, and shows where unusual model behaviour may be emerging.</p>
    </div>
    <aside class="weather-box"><span>Current fleet reading</span><strong>{fleet_reading}</strong><span>{source_summary}</span></aside>
  </section>
  <section class="stats-row" aria-label="Current observation summary">
    <div class="stat"><b>{total_reports}</b><span>accepted reports</span></div>
    <div class="stat"><b>{total_independent}</b><span>independent after dedup</span></div>
    <div class="stat"><b>{len(models)}</b><span>model families tracked</span></div>
    <div class="stat"><b>{len(all_sources)}</b><span>active source types</span></div>
  </section>
  <div class="data-note"><b>Preview data:</b> {html.escape(PREVIEW_DATA_NOTE)}</div>
  <section aria-labelledby="reported-heading">
    <div class="section-head"><div><div class="section-kicker">Live index</div><h2 id="reported-heading">Most reported right now</h2></div><div class="updated">Updated {updated}<br>Rolling {window_days}-day window</div></div>
    <div class="controls">
      <div class="control-row">
        <input id="model-search" type="search" placeholder="Search by lab, family, or model — e.g. Anthropic, Claude, Sonnet" aria-label="Search tracked models">
        <select id="status-filter" aria-label="Filter by signal status"><option value="all">All signal statuses</option><option value="corroborated">Corroborated only</option><option value="attention">Needs attention</option><option value="uncorroborated">Uncorroborated bursts</option><option value="quiet">No burst detected</option></select>
        <select id="sort-models" aria-label="Sort model panels"><option value="reports">Most reports</option><option value="independent">Most independent</option><option value="latest">Most recently reported</option></select>
      </div>
      <div class="lab-filters"><button type="button" class="filter-chip active" data-lab-filter="all">All labs</button>{lab_buttons}<span class="results-meta" id="results-meta"></span></div>
    </div>
    <div class="model-list" id="model-list">{''.join(cards)}</div>
    <div class="empty" id="empty-results" hidden>{html.escape(empty_message or 'No tracked models match those filters.')}</div>
    <p class="family-note"><b>How model attribution works:</b> each monitored model has its own report-volume bar. Exact counts rise only when a report names that model or one of its declared aliases; Sol, Luna, and Terra map to GPT-5.6. Ambiguous and older-model reports remain visible in residual lanes. Signal tiers are still assessed across the whole lab family until each model has enough non-synthetic history for a meaningful baseline.</p>
  </section>
  <section class="method" id="method"><div><div class="section-kicker">How to read this</div><h2>Signal, not diagnosis.</h2></div><div><p>A high report count is attention, not proof. Barometer only escalates when reports survive duplicate collapse and gain meaningful independence across sources.</p><div class="method-grid"><div class="method-card"><b>Reports</b><span>Posts matching a model and complaint pattern.</span></div><div class="method-card"><b>Independent</b><span>Viral repeats and shared links collapsed first.</span></div><div class="method-card"><b>Corroborated</b><span>Multiple independent source types agree.</span></div></div></div></section>
</main>
<footer><div class="shell">Something changed does not mean we know why. Model weights, serving configuration, quantisation, and product prompts remain indistinguishable from outside.</div></footer>
<script>
(() => {{
  const list = document.querySelector('#model-list');
  const cards = [...list.querySelectorAll('.model-card')];
  const search = document.querySelector('#model-search');
  const status = document.querySelector('#status-filter');
  const sort = document.querySelector('#sort-models');
  const meta = document.querySelector('#results-meta');
  const empty = document.querySelector('#empty-results');
  let lab = 'all';
  function apply() {{
    const query = search.value.trim().toLowerCase();
    const wantedStatus = status.value;
    const visible = cards.filter(card => {{
      const labMatch = lab === 'all' || card.dataset.lab === lab;
      const statusMatch = wantedStatus === 'all' ||
        (wantedStatus === 'corroborated' && ['corroborated','attributed'].includes(card.dataset.status)) ||
        (wantedStatus === 'attention' && ['attention','corroborated','attributed'].includes(card.dataset.status)) ||
        card.dataset.status === wantedStatus;
      const searchMatch = !query || card.dataset.search.includes(query);
      card.hidden = !(labMatch && statusMatch && searchMatch);
      return !card.hidden;
    }});
    const field = sort.value;
    visible.sort((a,b) => Number(b.dataset[field]) - Number(a.dataset[field]));
    visible.forEach((card,index) => {{
      list.append(card);
      const rank = card.querySelector('.rank');
      rank.textContent = String(index + 1).padStart(2,'0');
      rank.setAttribute('aria-label', `Rank ${{index + 1}}`);
    }});
    meta.textContent = `${{visible.length}} of ${{cards.length}} families`;
    empty.hidden = visible.length !== 0;
  }}
  document.querySelectorAll('[data-lab-filter]').forEach(button => button.addEventListener('click', () => {{
    lab = button.dataset.labFilter;
    document.querySelectorAll('[data-lab-filter]').forEach(item => item.classList.toggle('active', item === button));
    apply();
  }}));
  search.addEventListener('input', apply); status.addEventListener('change', apply); sort.addEventListener('change', apply); apply();
}})();
</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

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
 .back{{display:inline-block;color:#8fb4c5;text-decoration:none;margin-bottom:8px}}
 h1{{font-weight:600;letter-spacing:.5px}} h1 small{{color:#69707c;font-weight:400}}
 .card{{background:#161b24;border-radius:8px;padding:14px 18px;margin:14px 0}}
 .tier{{font-weight:700;letter-spacing:1px;font-size:13px}}
 .when{{color:#8a92a0;font-size:13px;margin:2px 0 6px}}
 .stats{{font-size:14px}} .summary{{margin-top:8px;color:#aeb6c2;font-style:italic}}
 .sources{{color:#8a92a0;font-size:13px;margin-top:-8px}}
 .ethic{{margin-top:36px;color:#69707c;font-size:13px;border-top:1px solid #232a36;padding-top:14px}}
</style>
<a class="back" href="index.html">← All models</a>
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
