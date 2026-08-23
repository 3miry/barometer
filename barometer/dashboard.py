from __future__ import annotations
from collections import Counter
import html
from datetime import datetime, timezone
from .catalog import model_catalog_entry, variant_breakdown
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
    max_reports = max((len(complaints) for complaints, _ in models.values()), default=1)
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
        source_text = " · ".join(
            f"{html.escape(source.upper())} {count}"
            for source, count in sorted(sources.items())
        ) or "No source data"
        category_html = "".join(
            f'<span class="chip">{html.escape(category)} <b>{count}</b></span>'
            for category, count in category_items
        ) or '<span class="chip muted">No categories yet</span>'
        terms = tuple(meta["recognised_terms"])
        breakdown = variant_breakdown(model, complaints)
        breakdown_html = "".join(
            f'<span class="model-chip{"" if item["explicit"] else " unspecified"}">'
            f'{html.escape(item["label"])} <b>{item["reports"]}</b></span>'
            for item in breakdown
        ) or '<span class="model-chip unspecified">No reports yet</span>'
        variant_terms = tuple(item["label"] for item in breakdown)
        search_terms = " ".join(
            (model, meta["label"], meta["lab"], *terms, *variant_terms)
        ).lower()
        latest = max((c.ts for c in complaints), default=0)
        width = round((len(complaints) / max_reports) * 100) if max_reports else 0
        cards.append(f"""
        <article class="model-card" data-model="{html.escape(model)}"
          data-lab="{html.escape(meta['lab'].lower())}"
          data-status="{status_key}" data-reports="{len(complaints)}"
          data-independent="{len(clusters)}" data-latest="{latest:.0f}"
          data-search="{html.escape(search_terms, quote=True)}">
          <div class="rank" aria-label="Rank {rank}">{rank:02d}</div>
          <div class="card-main">
            <div class="card-heading">
              <div>
                <div class="lab">{html.escape(meta['lab'])}</div>
                <h3>{html.escape(meta['label'])}</h3>
              </div>
              <span class="status" style="--status:{status_colour}">
                <i></i>{html.escape(status_label)}
              </span>
            </div>
            <div class="report-line">
              <strong>{len(complaints)}</strong>
              <span>reports in the last {window_days} days</span>
            </div>
            <div class="volume" aria-label="Relative report volume">
              <span style="width:{width}%"></span>
            </div>
            <div class="meta-line">
              <span>{len(clusters)} independent after deduplication</span>
              <span>{source_text}</span>
            </div>
            <div class="chips">{category_html}</div>
            <div class="breakdown">
              <span class="breakdown-label">Model breakdown</span>
              <div class="model-chips">{breakdown_html}</div>
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
.stats-row{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:72px}}
.stat{{padding:19px 21px;background:rgba(18,24,33,.72);border-right:1px solid var(--line)}} .stat:last-child{{border:0}}
.stat b{{font-size:24px;display:block}} .stat span{{color:var(--muted);font-size:12px}}
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
.model-card{{display:grid;grid-template-columns:46px minmax(0,1fr) auto;gap:18px;align-items:stretch;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}}
.model-card[hidden]{{display:none}} .rank{{color:#52606d;font:700 15px/1 ui-monospace,monospace;padding-top:6px}}
.card-heading{{display:flex;justify-content:space-between;align-items:start;gap:18px}} h3{{font-size:26px;letter-spacing:-.025em;margin:2px 0 0}}
.status{{display:inline-flex;align-items:center;gap:7px;color:#c4ccd3;font-size:12px;white-space:nowrap}}
.status i{{width:8px;height:8px;border-radius:50%;background:var(--status);box-shadow:0 0 0 4px color-mix(in srgb,var(--status) 14%,transparent)}}
.report-line{{display:flex;align-items:baseline;gap:9px;margin:19px 0 8px}} .report-line strong{{font-size:34px;line-height:1}} .report-line span{{color:var(--muted)}}
.volume{{height:7px;background:#0c1117;border-radius:99px;overflow:hidden}} .volume span{{display:block;height:100%;background:linear-gradient(90deg,#547d91,var(--blue));border-radius:inherit}}
.meta-line{{display:flex;justify-content:space-between;gap:16px;color:var(--muted);font-size:12px;margin:9px 0 14px}}
.chips{{display:flex;gap:7px;flex-wrap:wrap}} .chip{{background:var(--panel-2);border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:#aab4bf;font-size:11px}}
.chip b{{color:var(--ink)}} .breakdown{{margin-top:14px}} .breakdown-label{{display:block;color:var(--faint);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin-bottom:7px}}
.model-chips{{display:flex;gap:7px;flex-wrap:wrap}} .model-chip{{border:1px solid #345064;background:#13202a;color:#b8d2de;border-radius:7px;padding:5px 9px;font-size:11px}} .model-chip b{{color:#fff;margin-left:3px}} .model-chip.unspecified{{border-color:var(--line);background:#10161e;color:var(--muted)}}
.detail-link{{align-self:center;text-decoration:none;border-left:1px solid var(--line);padding:22px 4px 22px 24px;color:#b7c1ca;white-space:nowrap}}
.detail-link span{{color:var(--blue);margin-left:5px}} .detail-link:hover{{color:#fff}}
.empty{{padding:40px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:14px}}
.family-note{{margin:20px 0 70px;color:var(--muted);font-size:13px;max-width:760px}}
.method{{display:grid;grid-template-columns:.8fr 1.2fr;gap:56px;padding:54px 0 70px;border-top:1px solid var(--line)}}
.method p{{color:var(--muted);margin-top:0}} .method-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.method-card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.method-card b{{display:block;margin-bottom:5px}} .method-card span{{color:var(--muted);font-size:12px}}
footer{{border-top:1px solid var(--line);padding:24px 0 42px;color:var(--faint);font-size:12px}}
@media(max-width:780px){{.hero{{grid-template-columns:1fr;padding-top:50px}}.stats-row{{grid-template-columns:1fr 1fr}}.stat:nth-child(2){{border-right:0}}.stat{{border-bottom:1px solid var(--line)}}.control-row{{grid-template-columns:1fr}}.model-card{{grid-template-columns:32px 1fr}}.detail-link{{grid-column:2;border-left:0;border-top:1px solid var(--line);padding:14px 0 0}}.meta-line,.section-head{{align-items:start;flex-direction:column}}.method{{grid-template-columns:1fr}}.method-grid{{grid-template-columns:1fr}}}}
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
    <p class="family-note"><b>How model attribution works:</b> exact-model counts appear only when a report explicitly names one. Ambiguous reports remain in the visible unspecified bucket. Signal tiers are still assessed across the whole model family until each exact model has enough history for a meaningful baseline.</p>
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
