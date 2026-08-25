"""Aggregate frozen model predictions against private human review decisions.

The evaluator never loads source reports. Its output contains counts, rates,
and input hashes only: no report text, URLs, reviewer notes, or unit IDs.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .reviews import load_review_decisions_read_only


EVALUATION_WARNING = (
    "Small, query-selected development batch; not a prevalence estimate or "
    "an independent held-out benchmark."
)
OBSERVATION_FIELDS = (
    "specificity", "state", "change", "event_state", "valence",
    "suspected_layers", "elicitation_context", "qualifiers",
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(value):
    if isinstance(value, list):
        return tuple(sorted(value))
    return value


def _observation_signature(observation: dict) -> tuple:
    return (
        observation.get("concept_id"),
        *(_normalise(observation.get(field)) for field in OBSERVATION_FIELDS),
    )


def _outcome(observations: list, novelty: list) -> str:
    if observations:
        return "governed_report"
    if novelty:
        return "novel_only"
    return "no_attributable_signal"


def _confusion_payload(counts: Counter) -> dict:
    tp = counts[(True, True)]
    fp = counts[(False, True)]
    fn = counts[(True, False)]
    tn = counts[(False, False)]
    return {
        "evaluated_slices": tp + fp + fn + tn,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
        "accuracy": _ratio(tp + tn, tp + fp + fn + tn),
    }


def evaluate_blind_predictions(
    predictions_path: str | Path,
    review_db: str | Path,
) -> dict:
    """Return a read-only, aggregate comparison of one completed blind batch."""
    prediction_path = Path(predictions_path).resolve()
    review_path = Path(review_db).resolve()
    with prediction_path.open(encoding="utf-8") as handle:
        frozen = json.load(handle)
    predictions = frozen.get("predictions") or {}
    failures = frozen.get("failures") or {}
    decisions = load_review_decisions_read_only(review_path)
    if not isinstance(predictions, dict) or not isinstance(failures, dict):
        raise ValueError("prediction file has invalid prediction/failure maps")
    if set(predictions) & set(failures):
        raise ValueError("a review unit cannot be both a prediction and a failure")

    frozen_units = set(predictions) | set(failures)
    decision_units = set(decisions)
    deferred = {
        unit_id for unit_id, decision in decisions.items()
        if decision["status"] == "deferred"
    }
    scorable_units = sorted((set(predictions) & decision_units) - deferred)
    fingerprint_mismatches = 0
    human_statuses = Counter()
    human_outcomes = Counter()
    machine_eligibility = Counter()
    machine_outcomes = Counter()
    outcome_matrix = Counter()
    governed_detection = Counter()
    any_signal_detection = Counter()
    concept_human = Counter()
    concept_machine = Counter()
    concept_true_positive = 0
    exact_structured = 0
    exact_structured_human_positive = 0
    human_positive_slices = 0
    aligned_concepts = 0
    field_matches = Counter()
    field_totals = Counter()
    novelty_human = 0
    novelty_machine = 0
    novelty_exact_overlap = 0
    target_unsupported = 0

    for unit_id in sorted(decision_units - deferred):
        decision = decisions[unit_id]
        human_statuses[decision["status"]] += 1
        human_outcomes[_outcome(
            decision["observations"], decision["novelty_candidates"])] += 1

    for unit_id in scorable_units:
        decision = decisions[unit_id]
        prediction = predictions[unit_id]
        if decision["source_fingerprint"] != prediction.get("source_fingerprint"):
            fingerprint_mismatches += 1
            continue
        classification = prediction.get("classification") or {}
        human_observations = decision["observations"]
        machine_observations = classification.get("observations") or []
        human_novelty = decision["novelty_candidates"]
        machine_novelty = classification.get("novelty_candidates") or []
        human_outcome = _outcome(human_observations, human_novelty)
        machine_outcome = _outcome(machine_observations, machine_novelty)
        machine_eligibility[str(classification.get("eligibility"))] += 1
        machine_outcomes[machine_outcome] += 1
        outcome_matrix[(human_outcome, machine_outcome)] += 1
        if not prediction.get("target_supported", True):
            target_unsupported += 1

        human_governed = bool(human_observations)
        machine_governed = bool(machine_observations)
        human_any = human_governed or bool(human_novelty)
        machine_any = machine_governed or bool(machine_novelty)
        governed_detection[(human_governed, machine_governed)] += 1
        any_signal_detection[(human_any, machine_any)] += 1

        human_by_concept = {
            observation["concept_id"]: observation
            for observation in human_observations
        }
        machine_by_concept = {
            observation["concept_id"]: observation
            for observation in machine_observations
        }
        concept_human.update(human_by_concept.keys())
        concept_machine.update(machine_by_concept.keys())
        shared = set(human_by_concept) & set(machine_by_concept)
        concept_true_positive += len(shared)
        aligned_concepts += len(shared)
        for concept_id in shared:
            human = human_by_concept[concept_id]
            machine = machine_by_concept[concept_id]
            for field in OBSERVATION_FIELDS:
                field_totals[field] += 1
                field_matches[field] += int(
                    _normalise(human.get(field)) == _normalise(machine.get(field)))

        human_signatures = sorted(
            _observation_signature(item) for item in human_observations)
        machine_signatures = sorted(
            _observation_signature(item) for item in machine_observations)
        is_exact = human_signatures == machine_signatures
        exact_structured += int(is_exact)
        if human_governed:
            human_positive_slices += 1
            exact_structured_human_positive += int(is_exact)

        human_novelty_set = {
            str(label).strip().casefold() for label in human_novelty}
        machine_novelty_set = {
            str(label).strip().casefold() for label in machine_novelty}
        novelty_human += len(human_novelty_set)
        novelty_machine += len(machine_novelty_set)
        novelty_exact_overlap += len(human_novelty_set & machine_novelty_set)

    failure_types = Counter()
    failure_human_governed = 0
    failure_human_any = 0
    for unit_id, failure in failures.items():
        failure_types[
            f"{failure.get('error_type', 'unknown')}: "
            f"{failure.get('error', 'unspecified')}"
        ] += 1
        decision = decisions.get(unit_id)
        if decision and decision["status"] != "deferred":
            failure_human_governed += int(bool(decision["observations"]))
            failure_human_any += int(bool(
                decision["observations"] or decision["novelty_candidates"]))

    human_concept_total = sum(concept_human.values())
    machine_concept_total = sum(concept_machine.values())
    return {
        "evaluation_kind": "blind_human_machine_comparison",
        "warning": EVALUATION_WARNING,
        "model": frozen.get("model"),
        "contract_version": frozen.get("adjudicator_contract_version"),
        "input_hashes": {
            "predictions_sha256": _file_hash(prediction_path),
            "reviews_sha256": _file_hash(review_path),
        },
        "coverage": {
            "human_decisions": len(decisions),
            "deferred_human_decisions": len(deferred),
            "frozen_units": len(frozen_units),
            "valid_predictions": len(predictions),
            "validator_failures": len(failures),
            "scored_predictions": len(scorable_units) - fingerprint_mismatches,
            "missing_human_decisions": len(frozen_units - decision_units),
            "human_decisions_without_frozen_result": len(
                decision_units - frozen_units),
            "fingerprint_mismatches": fingerprint_mismatches,
        },
        "human_review_statuses": dict(sorted(human_statuses.items())),
        "human_outcomes": dict(sorted(human_outcomes.items())),
        "machine_eligibility": dict(sorted(machine_eligibility.items())),
        "machine_outcomes": dict(sorted(machine_outcomes.items())),
        "outcome_matrix": [
            {"human": human, "machine": machine, "slices": count}
            for (human, machine), count in sorted(outcome_matrix.items())
        ],
        "governed_detection": _confusion_payload(governed_detection),
        "any_reportable_or_novel_detection": _confusion_payload(
            any_signal_detection),
        "concept_coding": {
            "human_concepts": human_concept_total,
            "machine_concepts": machine_concept_total,
            "true_positive_concepts": concept_true_positive,
            "false_positive_concepts": (
                machine_concept_total - concept_true_positive),
            "false_negative_concepts": (
                human_concept_total - concept_true_positive),
            "micro_precision": _ratio(
                concept_true_positive, machine_concept_total),
            "micro_recall": _ratio(
                concept_true_positive, human_concept_total),
            "exact_structured_slices": exact_structured,
            "exact_structured_rate": _ratio(
                exact_structured, len(scorable_units) - fingerprint_mismatches),
            "human_governed_slices": human_positive_slices,
            "exact_structured_human_governed_slices": (
                exact_structured_human_positive),
            "exact_structured_human_governed_rate": _ratio(
                exact_structured_human_positive, human_positive_slices),
        },
        "field_agreement_on_shared_concepts": {
            "aligned_concepts": aligned_concepts,
            "fields": {
                field: {
                    "matches": field_matches[field],
                    "comparisons": field_totals[field],
                    "agreement": _ratio(
                        field_matches[field], field_totals[field]),
                }
                for field in OBSERVATION_FIELDS
            },
        },
        "novelty_exact_label_overlap": {
            "human_labels": novelty_human,
            "machine_labels": novelty_machine,
            "exact_matches": novelty_exact_overlap,
        },
        "target_unsupported_predictions": target_unsupported,
        "validator_failures": {
            "by_reason": dict(sorted(failure_types.items())),
            "human_governed_signals_lost": failure_human_governed,
            "human_reportable_or_novel_signals_lost": failure_human_any,
        },
        "usage": frozen.get("usage") or {},
        "raw_source_material_in_output": False,
    }
