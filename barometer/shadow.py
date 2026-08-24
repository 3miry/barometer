"""Read-only shadow evaluation for the provisional structured classifier."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .classifier import (
    CLASSIFIER_VERSION,
    attribution_is_aggregate_ready,
    attribution_review_status,
    classify_report,
)
from .vocabulary import concepts_by_id


DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "behaviour_reports.v1.json"
)


def _jsonable_observation(observation) -> dict:
    result = asdict(observation)
    result["suspected_layers"] = list(result["suspected_layers"])
    result["qualifiers"] = list(result["qualifiers"])
    return result


def _normalise_observations(observations: list[dict]) -> list[dict]:
    return sorted(observations, key=lambda item: item["concept_id"])


def evaluate_fixture(path: str | Path = DEFAULT_FIXTURE) -> dict:
    """Evaluate the development contract; this is not held-out accuracy."""
    with Path(path).open(encoding="utf-8") as handle:
        fixture = json.load(handle)

    eligibility_matches = 0
    onset_matches = 0
    observation_matches = 0
    full_matches = 0
    true_positive = 0
    false_positive = 0
    false_negative = 0
    mismatches = []

    for case in fixture["cases"]:
        expected = case["expected"]
        actual = classify_report(case["text"])
        actual_observations = [
            _jsonable_observation(item) for item in actual.observations]
        expected_observations = expected["observations"]
        eligibility_ok = actual.eligibility == expected["eligibility"]
        onset_ok = actual.onset_precision == expected["onset_precision"]
        observations_ok = (
            _normalise_observations(actual_observations)
            == _normalise_observations(expected_observations)
        )
        eligibility_matches += int(eligibility_ok)
        onset_matches += int(onset_ok)
        observation_matches += int(observations_ok)
        full_matches += int(eligibility_ok and onset_ok and observations_ok)

        expected_ids = {
            item["concept_id"] for item in expected_observations}
        actual_ids = {item["concept_id"] for item in actual_observations}
        true_positive += len(expected_ids & actual_ids)
        false_positive += len(actual_ids - expected_ids)
        false_negative += len(expected_ids - actual_ids)

        if not (eligibility_ok and onset_ok and observations_ok):
            mismatches.append({
                "id": case["id"],
                "text": case["text"],
                "expected": expected,
                "actual": {
                    "eligibility": actual.eligibility,
                    "onset_precision": actual.onset_precision,
                    "observations": actual_observations,
                    "abstention_reason": actual.abstention_reason,
                },
            })

    case_count = len(fixture["cases"])
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "evaluation_kind": "development_contract",
        "classifier_version": CLASSIFIER_VERSION,
        "warning": (
            "Rules were developed against this synthetic human-reviewed set. "
            "Scores verify the contract, not real-world classifier accuracy."
        ),
        "fixture_version": fixture["fixture_version"],
        "cases": case_count,
        "full_matches": full_matches,
        "full_match_rate": round(full_matches / case_count, 4),
        "eligibility_matches": eligibility_matches,
        "onset_matches": onset_matches,
        "observation_matches": observation_matches,
        "concept_precision": round(
            true_positive / precision_denominator, 4)
        if precision_denominator else 1.0,
        "concept_recall": round(
            true_positive / recall_denominator, 4)
        if recall_denominator else 1.0,
        "mismatches": mismatches,
    }


def shadow_database(path: str | Path) -> dict:
    """Classify retained rows through a read-only SQLite connection."""
    database = Path(path).resolve()
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, source, model, variant, text FROM complaints ORDER BY ts"
        ).fetchall()
    finally:
        connection.close()

    eligibility = Counter()
    concepts = Counter()
    specificity = Counter()
    suspected_layers = Counter()
    onset_precision = Counter()
    by_family = Counter()
    attribution_status = Counter()
    aggregate_ready_concepts = Counter()
    novelty_candidates = Counter()
    reports_with_novelty_candidates = 0
    observation_total = 0
    coded_report_total = 0
    aggregate_ready_coded_reports = 0
    for _report_id, _source, family, _variant, text in rows:
        result = classify_report(text)
        attribution = attribution_review_status(text, family, _variant)
        aggregate_ready = attribution_is_aggregate_ready(attribution)
        attribution_status[attribution] += 1
        eligibility[result.eligibility] += 1
        if result.novelty_candidates:
            reports_with_novelty_candidates += 1
            novelty_candidates.update(result.novelty_candidates)
        onset_precision[result.onset_precision] += 1
        by_family[(family, result.eligibility)] += 1
        if result.observations:
            coded_report_total += 1
            if aggregate_ready:
                aggregate_ready_coded_reports += 1
        for observation in result.observations:
            observation_total += 1
            concepts[observation.concept_id] += 1
            specificity[observation.specificity] += 1
            suspected_layers.update(observation.suspected_layers)
            if aggregate_ready:
                aggregate_ready_concepts[observation.concept_id] += 1

    labels = concepts_by_id()
    return {
        "evaluation_kind": "retained_data_shadow",
        "classifier_version": CLASSIFIER_VERSION,
        "warning": (
            "Retained rows are synthetic/legacy and were admitted by the old "
            "complaint filter. This is pipeline diagnosis, not prevalence or accuracy."
        ),
        "database": str(database),
        "read_only": True,
        "reports": len(rows),
        "coded_reports": coded_report_total,
        "aggregate_ready_coded_reports": aggregate_ready_coded_reports,
        "coded_reports_requiring_attribution_review": (
            coded_report_total - aggregate_ready_coded_reports),
        "reports_with_novelty_candidates": reports_with_novelty_candidates,
        "observations": observation_total,
        "eligibility": dict(sorted(eligibility.items())),
        "onset_precision": dict(sorted(onset_precision.items())),
        "specificity": dict(sorted(specificity.items())),
        "suspected_layers": dict(sorted(suspected_layers.items())),
        "attribution_status": dict(sorted(attribution_status.items())),
        "all_coded_concepts": [
            {
                "concept_id": concept_id,
                "label": labels[concept_id].public_label,
                "reports": count,
            }
            for concept_id, count in concepts.most_common()
        ],
        "aggregate_ready_concepts": [
            {
                "concept_id": concept_id,
                "label": labels[concept_id].public_label,
                "reports": count,
            }
            for concept_id, count in aggregate_ready_concepts.most_common()
        ],
        "novelty_candidates": [
            {"candidate": label, "reports": count}
            for label, count in novelty_candidates.most_common()
        ],
        "family_eligibility": [
            {"family": family, "eligibility": status, "reports": count}
            for (family, status), count in sorted(by_family.items())
        ],
    }
