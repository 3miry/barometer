"""barometer tick — the whole weather station in one idempotent pass.
Run by cron/systemd timer. Deliberately not a daemon: a station, not a
service."""
from __future__ import annotations
from pathlib import Path
import time
from .store import Store
from .canary import CanaryRunner
from .catalog import MODEL_CATALOG
from .detect import Complaint, detect_bursts, classify_tier
from .dashboard import render_dashboard, render_landing
from .public import write_public_snapshot
from .report_page import render_report_form

def tick(store: Store, adapters: list, runner: CanaryRunner | None = None,
         out_dir: str = ".", window_days: int = 21,
         now: float | None = None, retention_days: int | None = None,
         public_snapshot: str | None = None,
         public_history: str | None = None,
         approved_user_reports: list[Complaint] | None = None) -> dict:
    """One pass: ingest -> canaries (if due) -> detect -> tier -> render.
    Returns a small report dict; side effects are the store and the HTML."""
    now = now or time.time()
    since = now - window_days * 86400
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    report: dict = {"new_complaints": 0, "readings": 0, "assessments": {}}
    ephemeral_complaints = list(approved_user_reports or [])
    if ephemeral_complaints:
        report["approved_user_reports"] = len(ephemeral_complaints)

    for adapter in adapters:
        try:
            fetched = adapter.fetch(since)
            if getattr(adapter, "ephemeral", False):
                ephemeral_complaints.extend(fetched)
                report["ephemeral_complaints"] = (
                    report.get("ephemeral_complaints", 0) + len(fetched))
            else:
                report["new_complaints"] += store.add_complaints(fetched)
        except Exception as exc:                       # taps fail loudly but singly
            report.setdefault("tap_errors", []).append(f"{type(adapter).__name__}: {exc}")
        else:
            for error in getattr(adapter, "errors", []):
                report.setdefault("tap_errors", []).append(
                    f"{type(adapter).__name__}: {error}")
        usage_report = getattr(adapter, "usage_report", None)
        if usage_report is not None:
            try:
                usage = usage_report()
                source = getattr(adapter, "source_name", type(adapter).__name__)
                report.setdefault("source_usage", {})[source] = usage
            except Exception as exc:
                report.setdefault("tap_errors", []).append(
                    f"{type(adapter).__name__} usage: {exc}")

    if runner is not None:
        try:
            report["readings"] = len(runner.run_all_due(now))
        except Exception as exc:
            report.setdefault("canary_errors", []).append(str(exc))

    public_models = {}
    models = (set(MODEL_CATALOG) | set(store.models())
              | {c.model for c in ephemeral_complaints})
    for model in sorted(models):
        cs = store.complaints(model=model, since=since)
        cs.extend(c for c in ephemeral_complaints if c.model == model)
        events = store.events(model)
        bursts = detect_bursts(cs, events)
        assessments = [classify_tier(b, store.readings(model), events)
                       for b in bursts]
        render_dashboard(
            model, cs, assessments, f"{out_dir}/barometer_{model}.html",
            generated_at=now, window_days=window_days,
        )
        report["assessments"][model] = [a.summary for a in assessments]
        public_models[model] = (cs, assessments)

    render_landing(
        public_models,
        f"{out_dir}/index.html",
        generated_at=now,
        window_days=window_days,
    )
    render_report_form(public_models, f"{out_dir}/report.html")

    if public_snapshot is not None:
        write_public_snapshot(
            public_models,
            public_snapshot,
            now,
            window_days,
            history_path=public_history,
        )

    if retention_days is not None:
        cutoff = now - retention_days * 86400
        report["pruned_complaints"] = store.prune_complaints(cutoff)
    return report
