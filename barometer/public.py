"""Aggregate-only output suitable for a public Barometer frontend."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .catalog import PREVIEW_DATA_NOTE, model_catalog_entry, variant_breakdown
from .detect import Assessment, Complaint, cascade_clusters, classify

PUBLIC_WINDOWS = {"now": 86400, "7d": 7 * 86400, "21d": 21 * 86400}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _model_summary(
        model: str, complaints: list[Complaint],
        assessments: list[Assessment]) -> dict:
    meta = model_catalog_entry(model)
    categories = Counter(
        category
        for complaint in complaints
        for category in classify(complaint.text)
    )
    sources = Counter(complaint.source for complaint in complaints)
    highest_tier = max((assessment.tier for assessment in assessments), default=0)
    return {
        "label": meta["label"],
        "lab": meta["lab"],
        "recognised_terms": list(meta["recognised_terms"]),
        "model_breakdown": variant_breakdown(model, complaints),
        "reports": len(complaints),
        "independent_reports": len(cascade_clusters(complaints)),
        "latest_report_at": _iso(max(c.ts for c in complaints))
        if complaints else None,
        "sources": dict(sorted(sources.items())),
        "categories": dict(sorted(categories.items())),
        "highest_claim_tier": highest_tier,
        "bursts": [
            {
                "start": _iso(a.burst.start),
                "end": _iso(a.burst.end),
                "observed": a.burst.observed,
                "expected": a.burst.expected,
                "independent_sources": a.burst.independent_sources,
                "tier": a.tier,
                "summary": a.summary,
            }
            for a in assessments
        ],
    }


def _model_summary_with_windows(
        model: str, complaints: list[Complaint], assessments: list[Assessment],
        generated_at: float) -> dict:
    summary = _model_summary(model, complaints, assessments)
    summary["windows"] = {}
    for key, seconds in PUBLIC_WINDOWS.items():
        cutoff = generated_at - seconds
        current = [complaint for complaint in complaints if complaint.ts >= cutoff]
        active_assessments = [
            assessment for assessment in assessments
            if assessment.burst.end >= cutoff
        ]
        summary["windows"][key] = _model_summary(
            model, current, active_assessments,
        )
    return summary


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as temp:
            temp_path = temp.name
            json.dump(payload, temp, indent=2, sort_keys=True)
            temp.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _read_history(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "samples": []}
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("samples"), list):
        raise ValueError(f"unsupported or malformed history file: {path}")
    return data


def write_public_snapshot(
        models: dict[str, tuple[list[Complaint], list[Assessment]]],
        out_path: str,
        generated_at: float,
        window_days: int,
        history_path: str | None = None,
        history_days: int = 366) -> None:
    """Write aggregate statistics atomically; never serialize posts or URLs."""
    payload = {
        "schema_version": 1,
        "generated_at": _iso(generated_at),
        "window_days": window_days,
        "default_display_window": "now",
        "display_windows_seconds": PUBLIC_WINDOWS,
        "data_quality_note": PREVIEW_DATA_NOTE,
        "models": {
            model: _model_summary_with_windows(
                model, complaints, assessments, generated_at,
            )
            for model, (complaints, assessments) in sorted(models.items())
        },
    }
    _atomic_json(Path(out_path), payload)

    if history_path is not None:
        history_file = Path(history_path)
        history = _read_history(history_file)
        day = datetime.fromtimestamp(
            generated_at, tz=timezone.utc).date().isoformat()
        sample = {"date": day, **payload}
        samples = [s for s in history["samples"] if s.get("date") != day]
        samples.append(sample)
        samples.sort(key=lambda item: item.get("date", ""))
        history["samples"] = samples[-history_days:]
        _atomic_json(history_file, history)


def write_run_status(
        out_path: str,
        status: str,
        started_at: float,
        completed_at: float,
        report: dict | None = None,
        error: str | None = None) -> None:
    """Publish a small last-run health record without private report content."""
    payload = {
        "schema_version": 1,
        "status": status,
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
        "report": report,
        "error": error,
    }
    _atomic_json(Path(out_path), payload)
