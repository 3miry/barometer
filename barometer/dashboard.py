from __future__ import annotations
from collections import Counter
import html
import json
import math
from datetime import datetime, timezone
from .catalog import (
    PREVIEW_DATA_NOTE, infer_variant, model_catalog_entry, variant_breakdown,
)
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


DISPLAY_WINDOWS = (
    ("now", "Now", "24 hours", 24 * HOUR),
    ("7d", "7 days", "7 days", 7 * 24 * HOUR),
    ("21d", "21 days", "21 days", 21 * 24 * HOUR),
)


def _window_summary(
        model: str, complaints: list[Complaint], assessments: list[Assessment],
        generated_at: float, seconds: float) -> dict:
    cutoff = generated_at - seconds
    current = [complaint for complaint in complaints if complaint.ts >= cutoff]
    active_assessments = [
        assessment for assessment in assessments
        if assessment.burst.end >= cutoff
    ]
    status_key, status_label, status_colour = _activity_status(active_assessments)
    categories = Counter(
        category
        for complaint in current
        for category in classify(complaint.text)
    )
    sources = Counter(c.source.split("/", 1)[0] for c in current)
    weather_key, weather_label = _weather_style(status_key, categories)
    return {
        "complaints": current,
        "reports": len(current),
        "independent": len(cascade_clusters(current)),
        "latest": max((complaint.ts for complaint in current), default=0),
        "categories": categories,
        "sources": sources,
        "breakdown": variant_breakdown(model, current),
        "status_key": status_key,
        "status_label": status_label,
        "status_colour": status_colour,
        "weather_key": weather_key,
        "weather_label": weather_label,
    }


_CLOUD_POSITIONS = (
    (50, 47), (27, 68), (74, 66), (44, 82), (63, 25),
    (16, 43), (84, 43), (31, 27), (70, 84), (50, 12),
)


def _cloud_word_style(category: str, count: int, rank: int) -> str:
    """Return stable visual variables for a governed aggregate theme."""
    frequency = min(1.0, math.log2(max(count, 1)) / 4)
    size = 10 + (18 * frequency)
    opacity = 0.38 + (0.62 * frequency)
    left, top = _CLOUD_POSITIONS[rank % len(_CLOUD_POSITIONS)]
    seed = sum((index + 1) * ord(char) for index, char in enumerate(category))
    drift_x = 1 + (seed % 2)
    drift_y = 1 + ((seed // 3) % 2)
    if seed % 3 == 0:
        drift_x *= -1
    if seed % 5 == 0:
        drift_y *= -1
    duration = 8 + (seed % 6)
    delay = -(seed % duration)
    return (
        f"--word-x:{left}%;--word-y:{top}%;--word-size:{size:.1f}px;"
        f"--word-opacity:{opacity:.2f};--drift-x:{drift_x}px;"
        f"--drift-y:{drift_y}px;--drift-duration:{duration}s;"
        f"--drift-delay:{delay}s"
    )


def _category_cloud(category_items: list[tuple[str, int]], weather_label: str) -> str:
    category_items = category_items[:10]
    category_label = ", ".join(
        f"{category} {count}" for category, count in category_items
    ) or "No report themes yet"
    if not category_items:
        return f"""
      <div class="cloud-heading cloud-heading-empty">
        <span class="breakdown-label">Report weather · {html.escape(weather_label)}</span>
        <span class="cloud-key">No reported themes</span>
      </div>"""
    category_words = "".join(
        f'<span class="cloud-word" style="{_cloud_word_style(category, count, rank)}">'
        f'<span class="cloud-word-inner">{html.escape(category)}</span></span>'
        for rank, (category, count) in enumerate(category_items)
    )
    return f"""
      <div class="cloud-heading">
        <span class="breakdown-label">Report weather · {html.escape(weather_label)}</span>
        <span class="cloud-key">size + opacity = report frequency</span>
      </div>
      <div class="category-cloud" role="img"
        aria-label="Reported themes: {html.escape(category_label, quote=True)}">
        <div class="cloud-words" aria-hidden="true">{category_words}</div>
      </div>"""


def render_landing(
        models: dict[str, tuple[list[Complaint], list[Assessment]]],
        out_path: str, generated_at: float, window_days: int) -> None:
    """Render the public, aggregate-only entry point across exact models."""
    all_complaints = [
        complaint
        for complaints, _ in models.values()
        for complaint in complaints
    ]
    fleet_windows = {}
    for key, _, _, seconds in DISPLAY_WINDOWS:
        cutoff = generated_at - seconds
        window_complaints = [c for c in all_complaints if c.ts >= cutoff]
        window_sources = Counter(
            c.source.split("/", 1)[0] for c in window_complaints
        )
        window_independent = sum(
            len(cascade_clusters([
                complaint for complaint in complaints
                if complaint.ts >= cutoff
            ]))
            for complaints, _ in models.values()
        )
        window_corroborated = sum(
            1 for _, assessments in models.values()
            if max((
                assessment.tier for assessment in assessments
                if assessment.burst.end >= cutoff
            ), default=0) >= 2
        )
        fleet_windows[key] = {
            "reports": len(window_complaints),
            "independent": window_independent,
            "sources": window_sources,
            "corroborated": window_corroborated,
        }
    labs = sorted({model_catalog_entry(model)["lab"] for model in models})
    tracked_count = sum(
        len(model_catalog_entry(model).get("tracked_variants", ()))
        for model in models
    )

    cards = []
    unattributed = []
    for model, (complaints, assessments) in sorted(models.items()):
        meta = model_catalog_entry(model)
        tracked_variants = meta.get("tracked_variants", ())
        def assigned_variant(complaint: Complaint) -> str:
            return complaint.variant or infer_variant(
                model, complaint.text,
            ) or "unspecified"

        unattributed_attributes = []
        for key, _, _, seconds in DISPLAY_WINDOWS:
            cutoff = generated_at - seconds
            count = sum(
                1 for complaint in complaints
                if complaint.ts >= cutoff
                and assigned_variant(complaint) == "unspecified"
            )
            unattributed_attributes.append(f'data-{key}="{count}"')
        unattributed.append(f"""
          <div class="unattributed-item" data-lab="{html.escape(meta['lab'].lower())}"
            {' '.join(unattributed_attributes)}>
            <b>{html.escape(meta['label'])}</b>
            <span><strong class="unattributed-count">0</strong> without an exact model</span>
          </div>""")

        for variant in tracked_variants:
            variant_complaints = [
                complaint for complaint in complaints
                if assigned_variant(complaint) == variant["key"]
            ]
            windows = {}
            for key, _, _, seconds in DISPLAY_WINDOWS:
                summary = _window_summary(
                    model, variant_complaints, assessments,
                    generated_at, seconds,
                )
                summary["weather_key"], summary["weather_label"] = (
                    _weather_style("quiet", summary["categories"])
                )
                windows[key] = summary
            baseline_daily = windows["21d"]["reports"] / 21
            pane_parts = []
            data_attributes = []
            for key, _, range_label, seconds in DISPLAY_WINDOWS:
                summary = windows[key]
                source_text = " · ".join(
                    f"{html.escape(source.upper())} {count}"
                    for source, count in sorted(summary["sources"].items())
                ) or "No source data"
                category_items = sorted(
                    summary["categories"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[:10]
                category_html = _category_cloud(
                    category_items, summary["weather_label"],
                )
                if key == "21d":
                    comparison = "context window"
                elif summary["reports"] < 3:
                    comparison = "limited sample"
                else:
                    expected = baseline_daily * (seconds / (24 * HOUR))
                    comparison = (
                        f"{summary['reports'] / expected:.1f}× usual rate"
                        if expected >= 0.5 else "building baseline"
                    )
                hidden = "" if key == "now" else " hidden"
                pane_parts.append(f"""
                <div class="window-pane" data-window-pane="{key}"{hidden}>
                  <div class="report-line">
                    <strong>{summary['reports']}</strong>
                    <span>reports · {range_label}</span>
                    <em>{html.escape(comparison)}</em>
                  </div>
                  <div class="meta-line">
                    <span>{summary['independent']} independent</span>
                    <span>{source_text}</span>
                  </div>
                  <div class="category-weather">{category_html}</div>
                </div>
                """)
                data_attributes.extend((
                    f'data-reports-{key}="{summary["reports"]}"',
                    f'data-independent-{key}="{summary["independent"]}"',
                    f'data-latest-{key}="{summary["latest"]:.0f}"',
                    f'data-status-{key}="{summary["status_key"]}"',
                    f'data-status-label-{key}="{html.escape(summary["status_label"], quote=True)}"',
                    f'data-status-colour-{key}="{summary["status_colour"]}"',
                    f'data-weather-{key}="{summary["weather_key"]}"',
                ))
            initial = windows["now"]
            search_terms = " ".join((
                model, meta["label"], meta["lab"], variant["label"],
                *variant.get("aliases", ()),
            )).lower()
            cards.append(f"""
          <article class="model-card weather-{initial['weather_key']}"
            style="--status-colour:{initial['status_colour']}"
            data-model="{html.escape(model)}"
            data-variant="{html.escape(variant['key'])}"
            data-lab="{html.escape(meta['lab'].lower())}"
            data-status="{initial['status_key']}" data-reports="{initial['reports']}"
            data-independent="{initial['independent']}" data-latest="{initial['latest']:.0f}"
            {' '.join(data_attributes)}
            data-search="{html.escape(search_terms, quote=True)}">
            <div class="weather-scene" aria-hidden="true"><span class="weather-motion"></span></div>
            <div class="card-heading">
              <div><div class="lab">{html.escape(meta['lab'])}</div>
                <h3>{html.escape(variant['label'])}</h3>
              </div>
            </div>
            <span class="status"><i></i><span>Family · </span><span class="status-text">{html.escape(initial['status_label'])}</span></span>
            {''.join(pane_parts)}
            <a class="detail-link" href="barometer_{html.escape(model)}.html?model={html.escape(variant['key'], quote=True)}"
               aria-label="Open {html.escape(variant['label'])} detail">View model <span>→</span></a>
          </article>""")

    lab_buttons = "".join(
        f'<button type="button" class="filter-chip" data-lab-filter="{html.escape(lab.lower())}">{html.escape(lab)}</button>'
        for lab in labs
    )
    updated = _fmt(generated_at)
    fleet_readings = {
        key: (
            f"{summary['corroborated']} corroborated signal"
            f"{'s' if summary['corroborated'] != 1 else ''}"
            if summary["corroborated"] else "No corroborated signals"
        )
        for key, summary in fleet_windows.items()
    }
    source_summaries = {
        key: " · ".join(
            f"{html.escape(source.upper())} {count}"
            for source, count in sorted(summary["sources"].items())
        ) or "No source data yet"
        for key, summary in fleet_windows.items()
    }
    fleet_reading_attrs = " ".join(
        f'data-{key}="{html.escape(value, quote=True)}"'
        for key, value in fleet_readings.items()
    )
    source_summary_attrs = " ".join(
        f'data-{key}="{html.escape(value, quote=True)}"'
        for key, value in source_summaries.items()
    )
    source_total_attrs = " ".join(
        f'data-{key}="{len(summary["sources"])}"'
        for key, summary in fleet_windows.items()
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
[hidden]{{display:none!important}}
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
.data-note{{margin:0 0 58px;padding:11px 14px;border:1px solid #4a4028;background:#17150e;border-radius:10px;color:#aa9d7f;font-size:12px}} .data-note b{{color:#e8c879}}
.section-head{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:20px}}
h2{{font-size:32px;letter-spacing:-.035em;margin:6px 0 0}} .updated{{color:var(--muted);font-size:12px;text-align:right}}
.controls{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:18px}}
.window-row{{display:flex;align-items:center;gap:8px;margin-bottom:12px}} .window-label{{color:var(--faint);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin-right:3px}} .window-button{{color:var(--muted);background:#0e141b;border:1px solid var(--line);border-radius:8px;padding:6px 11px;cursor:pointer;font:inherit;font-size:12px}} .window-button.active{{color:#071018;background:var(--blue);border-color:var(--blue);font-weight:800}} .window-button small{{font-size:9px;opacity:.74;margin-left:3px}}
.control-row{{display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:12px}}
input,select{{width:100%;color:var(--ink);background:#0e141b;border:1px solid #303c48;border-radius:10px;padding:11px 13px;font:inherit;outline:none}}
input:focus,select:focus{{border-color:var(--blue);box-shadow:0 0 0 3px rgba(126,178,200,.12)}}
select{{min-width:170px}} .lab-filters{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px}}
.filter-chip{{color:var(--muted);background:transparent;border:1px solid var(--line);border-radius:999px;padding:6px 10px;cursor:pointer}}
.filter-chip.active{{color:var(--paper);background:var(--blue);border-color:var(--blue);font-weight:750}}
.results-meta{{color:var(--muted);font-size:12px;margin-left:auto}}
.model-list{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:stretch}}
.model-card{{position:relative;isolation:isolate;overflow:hidden;display:flex;flex-direction:column;min-height:430px;background:#101720;border:1px solid var(--line);border-radius:18px;padding:21px;box-shadow:var(--shadow)}} .model-card>:not(.weather-scene){{position:relative;z-index:2}}
.weather-scene{{position:absolute;inset:0;z-index:0;overflow:hidden;pointer-events:none}} .weather-scene::after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(9,14,20,.38),rgba(9,14,20,.74) 74%,rgba(9,14,20,.9)),linear-gradient(0deg,rgba(8,12,17,.34),transparent 58%)}} .weather-motion{{position:absolute;display:block;pointer-events:none}}
.weather-rain .weather-scene{{background:radial-gradient(ellipse at 14% 4%,rgba(105,139,158,.42),transparent 42%),radial-gradient(ellipse at 70% 120%,rgba(47,76,94,.4),transparent 62%),linear-gradient(145deg,#1b2a35,#0c131b 72%)}} .weather-rain .weather-motion{{inset:-60% -20%;opacity:.28;background:repeating-linear-gradient(111deg,transparent 0 17px,rgba(187,220,234,.52) 18px 19px,transparent 20px 34px);animation:rain-fall 1.8s linear infinite}}
.weather-fog .weather-scene{{background:linear-gradient(150deg,#26323a,#101820 70%)}} .weather-fog .weather-motion{{inset:5% -28%;opacity:.34;filter:blur(14px);background:radial-gradient(ellipse at 28% 34%,rgba(213,225,226,.52),transparent 32%),radial-gradient(ellipse at 66% 72%,rgba(163,181,187,.44),transparent 38%);animation:fog-bank 14s ease-in-out infinite alternate}}
.weather-storm .weather-scene{{background:radial-gradient(circle at 76% 12%,rgba(125,123,171,.32),transparent 24%),radial-gradient(ellipse at 12% 115%,rgba(41,72,91,.4),transparent 60%),linear-gradient(142deg,#202638,#090e17 74%)}} .weather-storm .weather-motion{{right:15%;top:-12px;width:92px;height:175px;background:linear-gradient(180deg,#f6f0bf,#b4d8e8 58%,transparent);clip-path:polygon(52% 0,22% 48%,46% 46%,29% 100%,76% 37%,54% 40%);filter:drop-shadow(0 0 18px rgba(218,235,255,.8));animation:lightning 7s steps(1,end) infinite}}
.weather-heat .weather-scene{{background:radial-gradient(circle at 16% 18%,rgba(235,154,81,.43),transparent 29%),radial-gradient(ellipse at 76% 110%,rgba(156,70,52,.38),transparent 54%),linear-gradient(145deg,#38271e,#171218 72%)}} .weather-heat .weather-motion{{inset:-30% -10%;opacity:.2;filter:blur(12px);background:repeating-linear-gradient(92deg,transparent 0 42px,rgba(255,196,124,.6) 48px,transparent 58px);animation:heat-rise 8s ease-in-out infinite alternate}}
.weather-overcast .weather-scene{{background:radial-gradient(ellipse at 12% 12%,rgba(123,141,151,.36),transparent 33%),radial-gradient(ellipse at 68% 2%,rgba(80,99,111,.32),transparent 38%),linear-gradient(145deg,#202b33,#10171e 72%)}} .weather-overcast .weather-motion{{inset:0;opacity:.2;background:radial-gradient(ellipse at 22% 22%,#c4d0d4,transparent 19%),radial-gradient(ellipse at 55% 8%,#8499a3,transparent 23%);animation:fog-bank 18s ease-in-out infinite alternate}}
.weather-clear .weather-scene{{background:radial-gradient(circle at 14% 110%,rgba(48,105,117,.38),transparent 45%),linear-gradient(145deg,#111d2d,#090d16 76%)}} .weather-clear .weather-motion{{inset:0;opacity:.55;background:radial-gradient(circle at 12% 22%,#d8edf2 0 1px,transparent 1.5px),radial-gradient(circle at 28% 10%,#a8d1db 0 1px,transparent 1.5px),radial-gradient(circle at 44% 31%,#e7f4f6 0 1px,transparent 1.5px),radial-gradient(circle at 65% 16%,#a8c7d5 0 1px,transparent 1.5px),radial-gradient(circle at 82% 35%,#d5e6ec 0 1px,transparent 1.5px);background-size:190px 150px}}
.model-card[hidden]{{display:none}}
.card-heading{{display:flex;justify-content:space-between;align-items:start;gap:18px}} h3{{font-size:28px;letter-spacing:-.035em;margin:3px 0 0}}
.status{{display:inline-flex;align-items:center;gap:6px;color:#aeb9c1;font-size:11px;margin-top:12px;white-space:nowrap}}
.status i{{width:8px;height:8px;border-radius:50%;background:var(--status-colour);box-shadow:0 0 0 4px color-mix(in srgb,var(--status-colour) 14%,transparent)}}
.report-line{{display:flex;align-items:baseline;gap:8px;margin:22px 0 8px;flex-wrap:wrap}} .report-line strong{{font-size:38px;line-height:1}} .report-line span{{color:var(--muted);font-size:12px}} .report-line em{{margin-left:auto;color:#99afbb;background:rgba(9,14,20,.46);border:1px solid rgba(126,178,200,.2);border-radius:999px;padding:3px 8px;font-size:9px;font-style:normal}}
.meta-line{{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:11px;margin:9px 0 12px}}
.category-weather{{width:100%;margin:16px 0 4px}} .cloud-heading{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:0}} .cloud-heading-empty{{margin-bottom:24px}} .cloud-key{{color:#71808d;font-size:8px;text-align:right}}
.category-cloud{{position:relative;width:100%;aspect-ratio:2.65/1;isolation:isolate;filter:drop-shadow(0 12px 15px rgba(0,0,0,.2))}}
.cloud-words{{position:absolute;inset:0;color:color-mix(in srgb,var(--status-colour) 36%,#f4f8f9);text-shadow:0 1px 8px rgba(0,0,0,.78)}} .cloud-word{{position:absolute;left:var(--word-x);top:var(--word-y);line-height:1;font-weight:720;letter-spacing:-.018em;white-space:nowrap;transform:translate(-50%,-50%)}} .cloud-word-inner{{display:block;font-size:var(--word-size);opacity:var(--word-opacity);animation:word-drift var(--drift-duration) ease-in-out var(--drift-delay) infinite alternate;will-change:transform}}
@keyframes word-drift{{from{{transform:translate3d(calc(var(--drift-x) * -1),calc(var(--drift-y) * -1),0)}}to{{transform:translate3d(var(--drift-x),var(--drift-y),0)}}}}
@keyframes rain-fall{{from{{transform:translate3d(0,-8%,0)}}to{{transform:translate3d(-5%,16%,0)}}}}
@keyframes fog-bank{{from{{transform:translate3d(-4%,0,0)}}to{{transform:translate3d(5%,1%,0)}}}}
@keyframes heat-rise{{from{{transform:translate3d(0,4%,0) scaleX(1)}}to{{transform:translate3d(2%,-4%,0) scaleX(1.04)}}}}
@keyframes lightning{{0%,88%,92%,100%{{opacity:0}}89%{{opacity:.9}}90%{{opacity:.18}}91%{{opacity:.75}}}}
.breakdown-label{{display:block;color:var(--faint);font-size:10px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin-bottom:7px}}
.detail-link{{display:flex;justify-content:space-between;align-items:center;margin-top:auto;padding-top:16px;border-top:1px solid rgba(255,255,255,.1);text-decoration:none;color:#c6d0d7;font-size:12px}}
.detail-link span{{color:var(--blue);margin-left:5px}} .detail-link:hover{{color:#fff}}
.empty{{padding:40px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:14px}}
.unattributed{{margin:22px 0 16px;padding:15px 17px;border:1px solid var(--line);background:rgba(17,24,33,.64);border-radius:13px}} .unattributed-head{{display:flex;justify-content:space-between;gap:16px;align-items:baseline;margin-bottom:10px}} .unattributed-head b{{font-size:12px}} .unattributed-head span{{color:var(--faint);font-size:10px}} .unattributed-list{{display:flex;gap:9px;flex-wrap:wrap}} .unattributed-item{{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:11px;border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:5px 9px}} .unattributed-item b,.unattributed-item strong{{color:#d8e1e6}}
.family-note{{margin:14px 0 32px;color:var(--muted);font-size:12px;max-width:820px}} .tracking-note{{display:flex;gap:18px;flex-wrap:wrap;color:var(--faint);font-size:11px;margin-bottom:54px}}
.method{{display:grid;grid-template-columns:.8fr 1.2fr;gap:56px;padding:54px 0 70px;border-top:1px solid var(--line)}}
.method p{{color:var(--muted);margin-top:0}} .method-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.method-card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.method-card b{{display:block;margin-bottom:5px}} .method-card span{{color:var(--muted);font-size:12px}}
footer{{border-top:1px solid var(--line);padding:24px 0 42px;color:var(--faint);font-size:12px}}
@media(prefers-reduced-motion:reduce){{.cloud-word-inner,.weather-motion{{animation:none!important}}}}
@media(max-width:1000px){{.model-list{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:680px){{.hero{{grid-template-columns:1fr;padding-top:50px}}.window-row{{flex-wrap:wrap}}.control-row{{grid-template-columns:1fr}}.model-list{{grid-template-columns:1fr}}.model-card{{min-height:410px}}.meta-line,.section-head,.unattributed-head{{align-items:start;flex-direction:column}}.method{{grid-template-columns:1fr}}.method-grid{{grid-template-columns:1fr}}}}
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
    <aside class="weather-box"><span>Current fleet reading</span><strong id="fleet-reading" {fleet_reading_attrs}>{html.escape(fleet_readings['now'])}</strong><span id="fleet-sources" {source_summary_attrs}>{source_summaries['now']}</span></aside>
  </section>
  <div class="data-note"><b>Preview data:</b> {html.escape(PREVIEW_DATA_NOTE)}</div>
  <section aria-labelledby="reported-heading">
    <div class="section-head"><div><div class="section-kicker">Live index</div><h2 id="reported-heading">Most reported models right now</h2></div><div class="updated">Updated {updated}<br><span id="window-description">Rolling 24-hour window</span></div></div>
    <div class="controls">
      <div class="window-row" role="group" aria-label="Display window">
        <span class="window-label">Display window</span>
        <button type="button" class="window-button active" data-display-window="now">Now <small>24h</small></button>
        <button type="button" class="window-button" data-display-window="7d">7 days</button>
        <button type="button" class="window-button" data-display-window="21d">21 days</button>
      </div>
      <div class="control-row">
        <input id="model-search" type="search" placeholder="Search exact models or labs — e.g. Opus 5, GPT-5.6, Google" aria-label="Search tracked models">
        <select id="status-filter" aria-label="Filter by signal status"><option value="all">All signal statuses</option><option value="corroborated">Corroborated only</option><option value="attention">Needs attention</option><option value="uncorroborated">Uncorroborated bursts</option><option value="quiet">No burst detected</option></select>
        <select id="sort-models" aria-label="Sort model panels"><option value="reports">Most reports</option><option value="independent">Most independent</option><option value="latest">Most recently reported</option></select>
      </div>
      <div class="lab-filters"><button type="button" class="filter-chip active" data-lab-filter="all">All labs</button>{lab_buttons}<span class="results-meta" id="results-meta"></span></div>
    </div>
    <div class="model-list" id="model-list">{''.join(cards)}</div>
    <div class="empty" id="empty-results" hidden>{html.escape(empty_message or 'No tracked models match those filters.')}</div>
    <aside class="unattributed" id="unattributed" aria-label="Family reports without an exact model">
      <div class="unattributed-head"><b>Family-wide reports without an exact model</b><span>Kept separate from model weather</span></div>
      <div class="unattributed-list">{''.join(unattributed)}</div>
    </aside>
    <p class="family-note"><b>How model attribution works:</b> a card rises only when a report explicitly names that model or one of its declared aliases; Sol, Luna, and Terra map to GPT-5.6. Ambiguous family reports stay in the strip above and never colour a specific model's weather. Signal tiers remain family-level context until each exact model has enough non-synthetic history for its own defensible baseline.</p>
    <div class="tracking-note"><span>Tracking {tracked_count} exact models across {len(labs)} labs</span><span id="tracking-source-count" {source_total_attrs}>{len(fleet_windows['now']['sources'])} active source types in this window</span><span>Reports deduplicated before display</span></div>
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
  const fleetReading = document.querySelector('#fleet-reading');
  const fleetSources = document.querySelector('#fleet-sources');
  const trackingSourceCount = document.querySelector('#tracking-source-count');
  const unattributedPanel = document.querySelector('#unattributed');
  const unattributedItems = [...document.querySelectorAll('.unattributed-item')];
  const windowDescription = document.querySelector('#window-description');
  let lab = 'all';
  let displayWindow = 'now';
  const weatherClasses = ['rain','fog','storm','heat','overcast','clear'].map(item => `weather-${{item}}`);
  function setWindow(key) {{
    displayWindow = key;
    cards.forEach(card => {{
      ['reports','independent','latest','status'].forEach(field => {{
        card.dataset[field] = card.getAttribute(`data-${{field}}-${{key}}`);
      }});
      const colour = card.getAttribute(`data-status-colour-${{key}}`);
      const weather = card.getAttribute(`data-weather-${{key}}`);
      card.style.setProperty('--status-colour', colour);
      card.classList.remove(...weatherClasses);
      card.classList.add(`weather-${{weather}}`);
      card.querySelector('.status-text').textContent = card.getAttribute(`data-status-label-${{key}}`);
      card.querySelectorAll('[data-window-pane]').forEach(pane => {{
        pane.hidden = pane.dataset.windowPane !== key;
      }});
    }});
    [fleetReading,fleetSources].forEach(item => {{
      item.textContent = item.dataset[key];
    }});
    const sourceTypes = Number(trackingSourceCount.dataset[key]);
    trackingSourceCount.textContent = `${{sourceTypes}} active source type${{sourceTypes === 1 ? '' : 's'}} in this window`;
    windowDescription.textContent = {{now:'Rolling 24-hour window','7d':'Rolling 7-day window','21d':'Rolling 21-day window'}}[key];
    document.querySelectorAll('[data-display-window]').forEach(button => {{
      button.classList.toggle('active', button.dataset.displayWindow === key);
      button.setAttribute('aria-pressed', button.dataset.displayWindow === key ? 'true' : 'false');
    }});
    apply();
  }}
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
    visible.forEach(card => list.append(card));
    meta.textContent = `${{visible.length}} of ${{cards.length}} models`;
    empty.hidden = visible.length !== 0;
    let unattributedVisible = 0;
    unattributedItems.forEach(item => {{
      const count = Number(item.dataset[displayWindow]);
      const labMatch = lab === 'all' || item.dataset.lab === lab;
      item.querySelector('.unattributed-count').textContent = count;
      item.hidden = !labMatch || count === 0;
      if (!item.hidden) unattributedVisible += 1;
    }});
    unattributedPanel.hidden = unattributedVisible === 0;
  }}
  document.querySelectorAll('[data-lab-filter]').forEach(button => button.addEventListener('click', () => {{
    lab = button.dataset.labFilter;
    document.querySelectorAll('[data-lab-filter]').forEach(item => item.classList.toggle('active', item === button));
    apply();
  }}));
  document.querySelectorAll('[data-display-window]').forEach(button => button.addEventListener('click', () => setWindow(button.dataset.displayWindow)));
  search.addEventListener('input', apply); status.addEventListener('change', apply); sort.addEventListener('change', apply); setWindow(displayWindow);
}})();
</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

def render_dashboard(
        model: str, complaints: list[Complaint], assessments: list[Assessment],
        out_path: str, generated_at: float | None = None,
        window_days: int = 21) -> None:
    """Render an aggregate-only family detail page with exact-model controls."""
    generated_at = generated_at or max(
        (complaint.ts for complaint in complaints), default=0,
    )
    meta = model_catalog_entry(model)
    cs = sorted((c for c in complaints if c.model == model), key=lambda c: c.ts)
    tracked = tuple(meta.get("tracked_variants", ()))
    variants = [{"key": "all", "label": f"All {meta['label']}"}] + [
        {"key": item["key"], "label": item["label"]} for item in tracked
    ]

    def assigned_variant(complaint: Complaint) -> str:
        return complaint.variant or infer_variant(model, complaint.text) or "unspecified"

    def timeline(current: list[Complaint], key: str, seconds: float) -> dict:
        bins = 8 if key == "now" else (7 if key == "7d" else 21)
        step = seconds / bins
        start = generated_at - seconds
        counts = [0] * bins
        for complaint in current:
            index = min(int((complaint.ts - start) / step), bins - 1)
            if index >= 0:
                counts[index] += 1
        labels = []
        for index in range(bins):
            stamp = datetime.fromtimestamp(
                start + (index * step), tz=timezone.utc,
            )
            labels.append(stamp.strftime("%H:%M") if key == "now" else stamp.strftime("%d %b"))
        return {"counts": counts, "labels": labels}

    views = {}
    for variant in variants:
        variant_cs = cs if variant["key"] == "all" else [
            complaint for complaint in cs
            if assigned_variant(complaint) == variant["key"]
        ]
        views[variant["key"]] = {}
        for key, _, range_label, seconds in DISPLAY_WINDOWS:
            summary = _window_summary(
                model, variant_cs, assessments, generated_at, seconds,
            )
            if variant["key"] != "all":
                summary["weather_key"], summary["weather_label"] = (
                    _weather_style("quiet", summary["categories"])
                )
            category_items = sorted(
                summary["categories"].items(),
                key=lambda item: (item[0] == "other", -item[1], item[0]),
            )[:4]
            views[variant["key"]][key] = {
                "reports": summary["reports"],
                "independent": summary["independent"],
                "sources": [
                    {"name": source.upper(), "count": count}
                    for source, count in sorted(summary["sources"].items())
                ],
                "categories": [
                    {"name": category, "count": count}
                    for category, count in category_items
                ],
                "weather": summary["weather_key"],
                "weather_label": summary["weather_label"],
                "status": summary["status_key"],
                "status_label": summary["status_label"],
                "status_colour": summary["status_colour"],
                "range_label": range_label,
                "timeline": timeline(summary["complaints"], key, seconds),
            }
    payload = {
        "family": model,
        "family_label": meta["label"],
        "lab": meta["lab"],
        "default_variant": "all",
        "default_window": "21d",
        "variants": variants,
        "views": views,
    }
    payload_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    variant_buttons = "".join(
        f'<button type="button" class="selector-button'
        f'{" active" if item["key"] == "all" else ""}" '
        f'data-variant="{html.escape(item["key"], quote=True)}">'
        f'{html.escape(item["label"])}</button>'
        for item in variants
    )
    updated = _fmt(generated_at)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(meta['label'])} weather — The Barometer</title>
<style>
:root{{--ink:#e8edf1;--muted:#8e9aa6;--faint:#5f6b76;--paper:#0a0e13;--panel:#111821;--line:#27333f;--blue:#7eb2c8;--status-colour:#9db0bf}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}} a{{color:inherit}} .shell{{width:min(1060px,calc(100% - 40px));margin:auto}}
header{{border-bottom:1px solid rgba(255,255,255,.06)}} .nav{{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:18px}} .brand{{font-weight:800;text-decoration:none}} .brand span{{color:var(--blue)}} .back{{color:var(--muted);font-size:13px;text-decoration:none}} .back:hover{{color:#fff}}
.hero{{padding:54px 0 28px;display:flex;justify-content:space-between;align-items:end;gap:28px}} .eyebrow{{color:var(--blue);font-size:11px;font-weight:850;letter-spacing:.14em;text-transform:uppercase}} h1{{font-size:clamp(42px,7vw,70px);line-height:.95;letter-spacing:-.05em;margin:8px 0 13px}} .hero p{{color:var(--muted);margin:0}} .updated{{color:var(--faint);font-size:11px;text-align:right}}
.selectors{{display:grid;gap:11px;margin-bottom:18px}} .selector-row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}} .selector-label{{width:92px;color:var(--faint);font-size:10px;font-weight:850;letter-spacing:.11em;text-transform:uppercase}} .selector-button{{color:var(--muted);background:#0e141b;border:1px solid var(--line);border-radius:999px;padding:7px 11px;font:inherit;font-size:12px;cursor:pointer}} .selector-button.active{{color:#071018;background:var(--blue);border-color:var(--blue);font-weight:800}}
.weather-panel{{position:relative;isolation:isolate;overflow:hidden;border:1px solid var(--line);border-radius:20px;min-height:500px;padding:28px;background:#101720;box-shadow:0 24px 70px rgba(0,0,0,.28)}} .weather-panel>:not(.weather-scene){{position:relative;z-index:2}} .weather-scene{{position:absolute;inset:0;z-index:0;overflow:hidden;pointer-events:none}} .weather-scene::after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(9,14,20,.28),rgba(9,14,20,.72) 76%,rgba(9,14,20,.87)),linear-gradient(0deg,rgba(8,12,17,.48),transparent 62%)}} .weather-motion{{position:absolute;display:block}}
.weather-rain .weather-scene{{background:radial-gradient(ellipse at 14% 4%,rgba(105,139,158,.42),transparent 42%),linear-gradient(145deg,#1b2a35,#0c131b 72%)}} .weather-rain .weather-motion{{inset:-60% -20%;opacity:.28;background:repeating-linear-gradient(111deg,transparent 0 17px,rgba(187,220,234,.52) 18px 19px,transparent 20px 34px);animation:rain-fall 1.8s linear infinite}}
.weather-fog .weather-scene{{background:linear-gradient(150deg,#26323a,#101820 70%)}} .weather-fog .weather-motion{{inset:5% -28%;opacity:.34;filter:blur(14px);background:radial-gradient(ellipse at 28% 34%,rgba(213,225,226,.52),transparent 32%),radial-gradient(ellipse at 66% 72%,rgba(163,181,187,.44),transparent 38%);animation:fog-bank 14s ease-in-out infinite alternate}}
.weather-storm .weather-scene{{background:radial-gradient(circle at 76% 12%,rgba(125,123,171,.32),transparent 24%),linear-gradient(142deg,#202638,#090e17 74%)}} .weather-storm .weather-motion{{right:15%;top:-12px;width:92px;height:175px;background:linear-gradient(180deg,#f6f0bf,#b4d8e8 58%,transparent);clip-path:polygon(52% 0,22% 48%,46% 46%,29% 100%,76% 37%,54% 40%);filter:drop-shadow(0 0 18px rgba(218,235,255,.8));animation:lightning 7s steps(1,end) infinite}}
.weather-heat .weather-scene{{background:radial-gradient(circle at 16% 18%,rgba(235,154,81,.43),transparent 29%),linear-gradient(145deg,#38271e,#171218 72%)}} .weather-heat .weather-motion{{inset:-30% -10%;opacity:.2;filter:blur(12px);background:repeating-linear-gradient(92deg,transparent 0 42px,rgba(255,196,124,.6) 48px,transparent 58px);animation:heat-rise 8s ease-in-out infinite alternate}}
.weather-overcast .weather-scene{{background:radial-gradient(ellipse at 12% 12%,rgba(123,141,151,.36),transparent 33%),linear-gradient(145deg,#202b33,#10171e 72%)}} .weather-clear .weather-scene{{background:radial-gradient(circle at 14% 110%,rgba(48,105,117,.38),transparent 45%),linear-gradient(145deg,#111d2d,#090d16 76%)}} .weather-clear .weather-motion{{inset:0;opacity:.55;background:radial-gradient(circle at 12% 22%,#d8edf2 0 1px,transparent 1.5px),radial-gradient(circle at 44% 31%,#e7f4f6 0 1px,transparent 1.5px),radial-gradient(circle at 82% 35%,#d5e6ec 0 1px,transparent 1.5px);background-size:190px 150px}}
.panel-head{{display:flex;justify-content:space-between;gap:18px;align-items:start}} .weather-name{{color:#b5c9d2;font-size:11px;font-weight:850;letter-spacing:.12em;text-transform:uppercase}} .signal{{display:flex;align-items:center;gap:8px;color:#c6d0d7;font-size:12px}} .signal i{{width:8px;height:8px;border-radius:50%;background:var(--status-colour);box-shadow:0 0 0 4px color-mix(in srgb,var(--status-colour) 15%,transparent)}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.08);border-radius:13px;overflow:hidden;margin:20px 0}} .metric{{padding:17px;background:rgba(8,13,18,.55)}} .metric b{{display:block;font-size:28px}} .metric span{{color:var(--muted);font-size:11px}}
.detail-grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:24px}} .section-label{{display:block;color:#7d8b96;font-size:10px;font-weight:850;letter-spacing:.12em;text-transform:uppercase;margin-bottom:10px}} .chart{{height:174px;display:flex;align-items:end;gap:4px;padding:12px 8px 25px;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(6,10,15,.42)}} .bar-wrap{{height:100%;flex:1;display:flex;align-items:end;position:relative}} .bar{{width:100%;min-height:2px;background:linear-gradient(180deg,#9fc5d4,#537b8d);border-radius:3px 3px 0 0}} .bar-wrap span{{position:absolute;left:50%;bottom:-20px;transform:translateX(-50%);color:#65737e;font-size:8px;white-space:nowrap}} .bar-wrap:not(:first-child):not(:last-child) span{{display:none}}
.theme-list,.source-list{{display:grid;gap:8px}} .theme,.source{{display:flex;justify-content:space-between;gap:14px;padding:10px 12px;border:1px solid rgba(255,255,255,.08);background:rgba(6,10,15,.42);border-radius:9px;color:#b8c5cc}} .theme b,.source b{{color:#fff}} .empty-note{{color:var(--muted);padding:24px;border:1px dashed rgba(255,255,255,.12);border-radius:10px}}
.actions{{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:24px;padding-top:20px;border-top:1px solid rgba(255,255,255,.1)}} .actions p{{color:#91a0aa;font-size:12px;max-width:620px;margin:0}} .report-link{{white-space:nowrap;text-decoration:none;border:1px solid #719bae;border-radius:999px;padding:9px 13px;color:#d9e9ef;background:rgba(40,72,86,.28)}}
.data-note{{margin:18px 0 58px;color:#aa9d7f;border:1px solid #4a4028;background:#17150e;border-radius:10px;padding:11px 14px;font-size:12px}} footer{{border-top:1px solid var(--line);padding:24px 0 42px;color:var(--faint);font-size:12px}}
@keyframes rain-fall{{from{{transform:translate3d(0,-8%,0)}}to{{transform:translate3d(-5%,16%,0)}}}} @keyframes fog-bank{{from{{transform:translate3d(-4%,0,0)}}to{{transform:translate3d(5%,1%,0)}}}} @keyframes heat-rise{{from{{transform:translate3d(0,4%,0)}}to{{transform:translate3d(2%,-4%,0)}}}} @keyframes lightning{{0%,88%,92%,100%{{opacity:0}}89%{{opacity:.9}}90%{{opacity:.18}}91%{{opacity:.75}}}}
@media(prefers-reduced-motion:reduce){{.weather-motion{{animation:none!important}}}} @media(max-width:760px){{.hero,.panel-head,.actions{{align-items:start;flex-direction:column}}.updated{{text-align:left}}.metrics{{grid-template-columns:1fr}}.detail-grid{{grid-template-columns:1fr}}.selector-label{{width:100%}}}}
</style></head><body>
<header><div class="shell nav"><a class="brand" href="index.html"><span>☂</span> The Barometer</a><a class="back" href="index.html">← All model weather</a></div></header>
<main class="shell">
  <section class="hero"><div><div class="eyebrow">{html.escape(meta['lab'])} · model detail</div><h1>{html.escape(meta['label'])}</h1><p>Twenty-one days of context, with a closer look at the weather now.</p></div><div class="updated">Updated {updated}<br>Aggregate data only</div></section>
  <div class="selectors">
    <div class="selector-row"><span class="selector-label">Model</span>{variant_buttons}</div>
    <div class="selector-row"><span class="selector-label">Window</span><button type="button" class="selector-button" data-window="now">Now · 24h</button><button type="button" class="selector-button" data-window="7d">7 days</button><button type="button" class="selector-button active" data-window="21d">21 days</button></div>
  </div>
  <section class="weather-panel" id="weather-panel">
    <div class="weather-scene" aria-hidden="true"><span class="weather-motion"></span></div>
    <div class="panel-head"><div><span class="weather-name" id="weather-name"></span><h2 id="selection-title"></h2></div><span class="signal"><i></i><span id="signal-label"></span></span></div>
    <div class="metrics"><div class="metric"><b id="report-count"></b><span id="report-range"></span></div><div class="metric"><b id="independent-count"></b><span>independent after deduplication</span></div><div class="metric"><b id="source-count"></b><span>active source types</span></div></div>
    <div class="detail-grid"><div><span class="section-label">Report activity</span><div class="chart" id="chart"></div></div><div><span class="section-label">Report themes</span><div class="theme-list" id="theme-list"></div><span class="section-label" style="margin-top:18px">Source mix</span><div class="source-list" id="source-list"></div></div></div>
    <div class="actions"><p>Signal status remains family-level until an exact model has enough non-synthetic history for a defensible baseline.</p><a class="report-link" id="report-link" href="report.html">Report this model →</a></div>
  </section>
  <div class="data-note"><b>Preview data:</b> {html.escape(PREVIEW_DATA_NOTE)}</div>
</main>
<footer><div class="shell">A report trend says something may have changed. It does not say why.</div></footer>
<script id="detail-data" type="application/json">{payload_json}</script>
<script>
(() => {{
  const data = JSON.parse(document.querySelector('#detail-data').textContent);
  const panel = document.querySelector('#weather-panel');
  const weatherClasses = ['rain','fog','storm','heat','overcast','clear'].map(item => `weather-${{item}}`);
  const requestedVariant = new URLSearchParams(location.search).get('model');
  let variant = data.views[requestedVariant] ? requestedVariant : data.default_variant;
  let windowKey = data.default_window;
  const empty = text => `<div class="empty-note">${{text}}</div>`;
  function render() {{
    const view = data.views[variant][windowKey];
    const variantMeta = data.variants.find(item => item.key === variant);
    panel.classList.remove(...weatherClasses); panel.classList.add(`weather-${{view.weather}}`);
    panel.style.setProperty('--status-colour', view.status_colour);
    document.querySelector('#weather-name').textContent = `Report weather · ${{view.weather_label}}`;
    document.querySelector('#selection-title').textContent = variantMeta.label;
    document.querySelector('#signal-label').textContent = `Family signal · ${{view.status_label}}`;
    document.querySelector('#report-count').textContent = view.reports;
    document.querySelector('#report-range').textContent = `reports in rolling ${{view.range_label}}`;
    document.querySelector('#independent-count').textContent = view.independent;
    document.querySelector('#source-count').textContent = view.sources.length;
    const maximum = Math.max(1,...view.timeline.counts);
    document.querySelector('#chart').innerHTML = view.timeline.counts.map((count,index) => `<div class="bar-wrap" title="${{view.timeline.labels[index]}}: ${{count}} reports"><div class="bar" style="height:${{Math.max(2,(count/maximum)*100)}}%"></div><span>${{view.timeline.labels[index]}}</span></div>`).join('');
    document.querySelector('#theme-list').innerHTML = view.categories.length ? view.categories.map(item => `<div class="theme"><span>${{item.name}}</span><b>${{item.count}}</b></div>`).join('') : empty('No report themes in this window.');
    document.querySelector('#source-list').innerHTML = view.sources.length ? view.sources.map(item => `<div class="source"><span>${{item.name}}</span><b>${{item.count}}</b></div>`).join('') : empty('No source data in this window.');
    const reportModel = variant === 'all' ? data.family_label : variantMeta.label;
    document.querySelector('#report-link').href = `report.html?model=${{encodeURIComponent(reportModel)}}`;
    document.querySelectorAll('[data-variant]').forEach(button => button.classList.toggle('active',button.dataset.variant === variant));
    document.querySelectorAll('[data-window]').forEach(button => button.classList.toggle('active',button.dataset.window === windowKey));
  }}
  document.querySelectorAll('[data-variant]').forEach(button => button.addEventListener('click',()=>{{variant=button.dataset.variant;render()}}));
  document.querySelectorAll('[data-window]').forEach(button => button.addEventListener('click',()=>{{windowKey=button.dataset.window;render()}}));
  render();
}})();
</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
