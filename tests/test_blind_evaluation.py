"""Blind classifier evaluation remains aggregate, strict, and read-only."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from barometer.blind_evaluation import evaluate_blind_predictions
from barometer.reviews import ReviewStore, validate_review_decision
from barometer.vocabulary import concepts_by_id


def observation(concept_id: str, *, valence: str = "unstated") -> dict:
    concept = concepts_by_id()[concept_id]
    return {
        "concept_id": concept_id,
        "specificity": concept.coding_scope,
        "state": concept.allowed_states[0],
        "change": concept.allowed_changes[0],
        "event_state": None,
        "valence": valence,
        "claim_status": "reported",
        "suspected_layers": ["unknown"],
        "elicitation_context": "ordinary",
        "qualifiers": [],
    }


class BlindEvaluationTests(unittest.TestCase):
    def test_evaluation_is_aggregate_read_only_and_counts_failures_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "reviews.db"
            prediction_path = root / "predictions.json"
            source_hashes = {"one": "hash-one", "two": "hash-two", "three": "hash-three"}
            human_observation = observation("beh_0045", valence="negative")
            with ReviewStore(str(review_path)) as store:
                for unit_id, observations, novelty, status in (
                    ("one::claude", [human_observation], [], "corrected"),
                    ("two::gpt", [], [], "rejected"),
                    ("three::gemini", [human_observation], [], "corrected"),
                ):
                    report_id = unit_id.split("::", 1)[0]
                    decision = validate_review_decision(
                        unit_id, report_id, source_hashes[report_id], {
                            "status": status,
                            "target_family": (
                                "claude" if report_id == "one" else
                                "gpt" if report_id == "two" else "gemini"),
                            "target_variant": None,
                            "observations": observations,
                            "novelty_candidates": novelty,
                            "review_note": "SECRET REVIEW NOTE",
                        }, now=100.0,
                    )
                    store.put(decision)
            prediction_payload = {
                "model": "example/model",
                "adjudicator_contract_version": "test-contract",
                "predictions": {
                    "one::claude": {
                        "source_fingerprint": "hash-one",
                        "target_supported": True,
                        "classification": {
                            "eligibility": "behaviour_report",
                            "observations": [human_observation],
                            "novelty_candidates": [],
                        },
                    },
                    "two::gpt": {
                        "source_fingerprint": "hash-two",
                        "target_supported": True,
                        "classification": {
                            "eligibility": "chatter",
                            "observations": [],
                            "novelty_candidates": [],
                        },
                    },
                },
                "failures": {
                    "three::gemini": {
                        "error_type": "AdjudicationError",
                        "error": "synthetic validator failure",
                    },
                },
                "usage": {"calls": 3, "reported_cost_usd": 0.01},
            }
            prediction_path.write_text(
                json.dumps(prediction_payload), encoding="utf-8")
            before_review = review_path.read_bytes()
            before_predictions = prediction_path.read_bytes()

            result = evaluate_blind_predictions(prediction_path, review_path)

            self.assertEqual(review_path.read_bytes(), before_review)
            self.assertEqual(prediction_path.read_bytes(), before_predictions)
            self.assertEqual(result["coverage"]["human_decisions"], 3)
            self.assertEqual(result["coverage"]["scored_predictions"], 2)
            self.assertEqual(result["governed_detection"]["true_positive"], 1)
            self.assertEqual(result["governed_detection"]["true_negative"], 1)
            self.assertEqual(
                result["validator_failures"]["human_governed_signals_lost"], 1)
            rendered = json.dumps(result)
            self.assertNotIn("SECRET REVIEW NOTE", rendered)
            self.assertNotIn("one::claude", rendered)


if __name__ == "__main__":
    unittest.main()
