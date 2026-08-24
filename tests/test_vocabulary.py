"""Governed vocabulary and balanced evaluation-fixture contract tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from barometer.catalog import MODEL_CATALOG
from barometer.vocabulary import (
    LEDGER_PATH,
    RESERVED_LEGACY_LABELS,
    VALID_DIRECTIONS,
    VALID_EVENT_STATES,
    VALID_VALENCES,
    VocabularyError,
    concepts_by_id,
    load_vocabulary,
    validate_ledger,
)


FIXTURE_PATH = (
    Path(__file__).with_name("fixtures") / "behaviour_reports.v1.json"
)
VALID_ELIGIBILITY = frozenset((
    "behaviour_report", "novel_candidate", "chatter",
    "uncodable_appraisal", "ambiguous",
))
VALID_ONSET_PRECISION = frozenset((
    "exact", "day", "broad_period", "unknown",
))


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class VocabularyLedgerTests(unittest.TestCase):
    def test_seed_ledger_is_valid_provisional_and_non_public(self):
        concepts = load_vocabulary()
        self.assertGreaterEqual(len(concepts), 18)
        self.assertTrue(any(item.shape == "dimension" for item in concepts))
        self.assertTrue(any(item.shape == "event" for item in concepts))
        self.assertTrue(all(item.status == "provisional" for item in concepts))
        self.assertFalse(any(item.publishable for item in concepts))
        labels = {item.public_label.casefold() for item in concepts}
        self.assertTrue(labels.isdisjoint(RESERVED_LEGACY_LABELS))

    def test_ledger_rejects_duplicate_concepts(self):
        payload = _read_json(LEDGER_PATH)
        duplicate = copy.deepcopy(payload["events"][0])
        duplicate["event_id"] = "vocab_evt_9999"
        payload["events"].append(duplicate)
        with self.assertRaisesRegex(VocabularyError, "duplicate concept id"):
            validate_ledger(payload)

    def test_provisional_concept_cannot_be_publishable(self):
        payload = _read_json(LEDGER_PATH)
        payload["events"][0]["concept"]["publishable"] = True
        with self.assertRaisesRegex(VocabularyError, "before activation"):
            validate_ledger(payload)

    def test_ledger_events_cannot_be_inserted_out_of_order(self):
        payload = _read_json(LEDGER_PATH)
        payload["events"][0], payload["events"][1] = (
            payload["events"][1], payload["events"][0])
        with self.assertRaisesRegex(VocabularyError, "append-only"):
            validate_ledger(payload)

    def test_spiky_remains_outside_seeded_candidate_phrases(self):
        phrases = {
            phrase.casefold()
            for concept in load_vocabulary()
            for phrase in concept.candidate_phrases
        }
        self.assertNotIn("spiky", phrases)


class EvaluationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _read_json(FIXTURE_PATH)
        cls.concepts = concepts_by_id()

    def test_fixture_is_explicitly_synthetic_and_balanced(self):
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertIs(self.fixture["synthetic"], True)
        cases = self.fixture["cases"]
        self.assertGreaterEqual(len(cases), 20)
        eligibility = {
            case["expected"]["eligibility"] for case in cases
        }
        self.assertEqual(eligibility, VALID_ELIGIBILITY)
        valences = {
            observation["valence"]
            for case in cases
            for observation in case["expected"]["observations"]
        }
        self.assertTrue({"positive", "negative", "mixed"} <= valences)

    def test_fixture_ids_routing_and_observations_are_valid(self):
        ids = set()
        tracked_variants = {
            variant["key"]
            for family in MODEL_CATALOG.values()
            for variant in family.get("tracked_variants", ())
        }
        for case in self.fixture["cases"]:
            self.assertNotIn(case["id"], ids)
            ids.add(case["id"])
            self.assertIn(case["family"], MODEL_CATALOG)
            if case["variant"] is not None:
                self.assertIn(case["variant"], tracked_variants)
            expected = case["expected"]
            self.assertIn(expected["eligibility"], VALID_ELIGIBILITY)
            self.assertIn(
                expected["onset_precision"], VALID_ONSET_PRECISION)
            for observation in expected["observations"]:
                concept = self.concepts[observation["concept_id"]]
                self.assertIn(observation["valence"], VALID_VALENCES)
                if concept.shape == "dimension":
                    self.assertIn(
                        observation["direction"], VALID_DIRECTIONS)
                    self.assertIsNone(observation["event_state"])
                else:
                    self.assertIsNone(observation["direction"])
                    self.assertIn(
                        observation["event_state"], VALID_EVENT_STATES)

    def test_unknown_onset_does_not_exclude_behaviour_reports(self):
        cases = self.fixture["cases"]
        self.assertTrue(any(
            case["expected"]["eligibility"] == "behaviour_report"
            and case["expected"]["onset_precision"] == "unknown"
            for case in cases
        ))
        spiky = next(case for case in cases if case["id"] == "eval_003")
        self.assertEqual(spiky["expected"]["eligibility"], "novel_candidate")
        self.assertEqual(spiky["expected"]["onset_precision"], "unknown")

    def test_broad_quality_language_is_not_guessed_into_a_concept(self):
        quality = next(case for case in self.fixture["cases"]
                       if case["id"] == "eval_020")
        self.assertEqual(
            quality["expected"]["eligibility"], "uncodable_appraisal")
        self.assertEqual(quality["expected"]["observations"], [])


if __name__ == "__main__":
    unittest.main()
