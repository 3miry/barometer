"""Aggregate a completed private review batch without exposing report text."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
import sqlite3

from .review_app import build_review_items
from .reviews import load_review_decisions_read_only
from .vocabulary import concepts_by_id


ANALYSIS_WARNING = (
    "This is a small reviewed development batch, not a held-out accuracy "
    "estimate or a platform-prevalence denominator."
)


# Query-shape diagnostics are deliberately broader than the governed model
# catalogue. A line name such as "Opus" can be useful for discovery even when
# the post does not identify a tracked release precisely.
MODEL_LINE_MENTION = re.compile(
    r"(?<![a-z0-9])(?:opus|sonnet|fable|sol|luna|terra|"
    r"flash(?:[-\s]+lite)?)(?![a-z0-9])",
    re.IGNORECASE,
)
PRODUCT_FAMILY_MENTION = re.compile(
    r"(?<![a-z0-9])(?:claude|chatgpt|gpt|gemini|grok)(?![a-z0-9])",
    re.IGNORECASE,
)
LAB_MENTION = re.compile(
    r"(?<![a-z0-9])(?:anthropic|openai|xai|google)(?![a-z0-9])",
    re.IGNORECASE,
)


def _query_targeting_bucket(row: dict) -> str:
    """Describe the narrowest model-name shape present in one source report."""
    if row.get("mentioned_variants"):
        return "exact_variant"
    text = row.get("text", "")
    if MODEL_LINE_MENTION.search(text):
        return "model_line"
    if PRODUCT_FAMILY_MENTION.search(text):
        return "product_family"
    if LAB_MENTION.search(text):
        return "lab_only"
    return "other_or_unresolved"


def _targeting_summary(counts: Counter) -> dict:
    total = sum(counts.values())
    useful = counts["governed_report"] + counts["novel_only"]
    return {
        "source_reports": total,
        "governed_reports": counts["governed_report"],
        "novel_only": counts["novel_only"],
        "no_attributable_signal": counts["no_attributable_signal"],
        "pending": counts["pending"],
        "useful_reports": useful,
        "useful_share": _ratio(useful, total),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _observation_signature(item: dict) -> tuple:
    return (
        item.get("concept_id"), item.get("specificity"), item.get("state"),
        item.get("change"), item.get("event_state"), item.get("valence"),
        tuple(item.get("suspected_layers", ())),
        item.get("elicitation_context"), tuple(item.get("qualifiers", ())),
    )


def _source_provenance(path: Path, report_ids: set[str]) -> dict:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"collection_runs", "complaint_provenance"} <= tables:
            return {
                "query_provenance_available": False,
                "reports_with_query_provenance": 0,
                "reports_without_query_provenance": len(report_ids),
                "collection_runs": 0,
                "cross_lane_overlap_reports": 0,
                "note": (
                    "Retained rows predate query-run provenance; discovery "
                    "yield and probe overlap cannot be reconstructed."),
            }
        placeholders = ",".join("?" for _ in report_ids)
        if not placeholders:
            return {
                "query_provenance_available": True,
                "reports_with_query_provenance": 0,
                "reports_without_query_provenance": 0,
                "collection_runs": 0,
                "cross_lane_overlap_reports": 0,
                "reports_by_lane": {},
            }
        rows = connection.execute(
            "SELECT p.complaint_id,r.run_id,r.lane FROM complaint_provenance p "
            "JOIN collection_runs r ON r.run_id=p.run_id "
            f"WHERE p.complaint_id IN ({placeholders})",
            tuple(sorted(report_ids)),
        ).fetchall()
        reports_by_lane: dict[str, set[str]] = defaultdict(set)
        runs = set()
        report_lanes: dict[str, set[str]] = defaultdict(set)
        for report_id, run_id, lane in rows:
            reports_by_lane[lane].add(report_id)
            report_lanes[report_id].add(lane)
            runs.add(run_id)
        covered = set(report_lanes)
        return {
            "query_provenance_available": True,
            "reports_with_query_provenance": len(covered),
            "reports_without_query_provenance": len(report_ids - covered),
            "collection_runs": len(runs),
            "cross_lane_overlap_reports": sum(
                len(lanes) > 1 for lanes in report_lanes.values()),
            "reports_by_lane": {
                lane: len(ids) for lane, ids in sorted(reports_by_lane.items())
            },
        }
    finally:
        connection.close()


def analyze_review_batch(
        source_db: str | Path,
        review_db: str | Path) -> dict:
    """Return aggregate review/yield diagnostics; never include raw text or URLs."""
    source_path, review_path = Path(source_db), Path(review_db)
    decisions = load_review_decisions_read_only(review_path)
    items = build_review_items(
        source_path, review_path, decisions=decisions)
    grouped: dict[str, list[dict]] = defaultdict(list)
    statuses = Counter()
    concepts = Counter()
    valences = Counter()
    novelty_by_source: dict[str, set[str]] = defaultdict(set)
    coded_slices = 0
    current_decisions = 0
    for item in items:
        grouped[item["report_id"]].append(item)
        decision = item.get("decision")
        status = "pending"
        if decision is not None and not decision.get("stale"):
            status = decision["status"]
            current_decisions += 1
            if decision["observations"]:
                coded_slices += 1
            for observation in decision["observations"]:
                concepts[observation["concept_id"]] += 1
                valences[observation["valence"]] += 1
            novelty_by_source[item["report_id"]].update(
                decision["novelty_candidates"])
        statuses[status] += 1

    source_outcomes = Counter()
    source_yield = defaultdict(Counter)
    targeting_yield = defaultdict(Counter)
    for report_id, rows in grouped.items():
        fresh = [
            row["decision"] for row in rows
            if row.get("decision") and not row["decision"].get("stale")
        ]
        if len(fresh) != len(rows):
            outcome = "pending"
        elif any(decision["observations"] for decision in fresh):
            outcome = "governed_report"
        elif any(decision["novelty_candidates"] for decision in fresh):
            outcome = "novel_only"
        else:
            outcome = "no_attributable_signal"
        source_outcomes[outcome] += 1
        source_yield[rows[0]["source"]][outcome] += 1
        targeting_yield[_query_targeting_bucket(rows[0])][outcome] += 1

    detection = Counter()
    exact_structured = 0
    comparable = 0
    comparison_slices = Counter()
    for item in items:
        decision = item.get("decision")
        if decision is None or decision.get("stale"):
            continue
        human_positive = bool(decision["observations"])
        if item["target_count"] > 1:
            comparison_slices[
                "governed" if human_positive else "no_governed_observation"
            ] += 1
            continue
        proposal_positive = bool(item["proposal"]["observations"])
        detection[(proposal_positive, human_positive)] += 1
        comparable += 1
        proposal = sorted(
            _observation_signature(obs)
            for obs in item["proposal"]["observations"])
        human = sorted(
            _observation_signature(obs) for obs in decision["observations"])
        if proposal == human:
            exact_structured += 1

    tp = detection[(True, True)]
    fp = detection[(True, False)]
    fn = detection[(False, True)]
    tn = detection[(False, False)]
    report_ids = set(grouped)
    reportable = (
        source_outcomes["governed_report"] + source_outcomes["novel_only"])
    novelty_counts = Counter(
        label for labels in novelty_by_source.values() for label in labels)
    primary_targeting = (
        targeting_yield["exact_variant"] + targeting_yield["model_line"])
    primary_useful = (
        primary_targeting["governed_report"]
        + primary_targeting["novel_only"])
    governed_concepts = concepts_by_id()
    return {
        "evaluation_kind": "reviewed_development_batch",
        "warning": ANALYSIS_WARNING,
        "complete": current_decisions == len(items),
        "source_reports": len(grouped),
        "model_target_slices": len(items),
        "review_statuses": dict(sorted(statuses.items())),
        "source_outcomes": dict(sorted(source_outcomes.items())),
        "reportable_or_novel_source_yield": {
            "count": reportable,
            "denominator": len(grouped),
            "share": _ratio(reportable, len(grouped)),
        },
        "source_yield_by_tap": {
            source: dict(sorted(counts.items()))
            for source, counts in sorted(source_yield.items())
        },
        "query_targeting_hypothesis": {
            "warning": (
                "Observed name shapes in this reviewed development batch; "
                "this is not an independent query-volume or prevalence estimate."
            ),
            "buckets": {
                bucket: _targeting_summary(targeting_yield[bucket])
                for bucket in (
                    "exact_variant", "model_line", "product_family",
                    "lab_only", "other_or_unresolved")
            },
            "exact_variant_or_model_line": {
                **_targeting_summary(primary_targeting),
                "captured_useful_reports": primary_useful,
                "all_useful_reports": reportable,
                "useful_capture_share": _ratio(primary_useful, reportable),
            },
        },
        "human_coded_target_slices": coded_slices,
        "human_observations": sum(concepts.values()),
        "concept_counts": dict(sorted(concepts.items())),
        "concept_labels": {
            concept_id: governed_concepts[concept_id].public_label
            for concept_id in sorted(concepts)
        },
        "valence_counts": dict(sorted(valences.items())),
        "novelty_candidate_source_counts": dict(sorted(novelty_counts.items())),
        "single_target_governed_detection": {
            "comparable_slices": comparable,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "true_negative": tn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "exact_structured_matches": exact_structured,
        },
        "comparison_target_slices": dict(sorted(comparison_slices.items())),
        "collection_provenance": _source_provenance(source_path, report_ids),
    }
