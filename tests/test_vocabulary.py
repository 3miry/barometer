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
    VALID_CHANGES,
    VALID_CLAIM_STATUSES,
    VALID_ELICITATION_CONTEXTS,
    VALID_EVENT_STATES,
    VALID_SPECIFICITIES,
    VALID_STATES,
    VALID_SUSPECTED_LAYERS,
    VALID_VALENCES,
    VocabularyError,
    concepts_by_id,
    concept_replacements,
    load_hierarchy,
    load_vocabulary,
    narrower_concept_ids,
    ordinary_use_signal_eligible,
    validate_coded_observation,
    validate_coded_observations,
    validate_ledger,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "behaviour_reports.v1.json"
VALID_ELIGIBILITY = frozenset((
    "behaviour_report", "novel_candidate", "chatter",
    "uncodable_appraisal", "ambiguous", "excluded_adversarial",
))
VALID_ONSET_PRECISION = frozenset((
    "exact", "day", "broad_period", "unknown",
))


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class VocabularyLedgerTests(unittest.TestCase):
    def test_seed_ledger_is_valid_governed_and_non_public(self):
        concepts = load_vocabulary()
        self.assertGreaterEqual(len(concepts), 45)
        self.assertTrue(any(item.shape == "dimension" for item in concepts))
        self.assertTrue(any(item.shape == "event" for item in concepts))
        self.assertTrue(any(item.coding_scope == "broad" for item in concepts))
        self.assertTrue(
            {item.status for item in concepts} <= {"provisional", "superseded"})
        self.assertTrue(any(item.status == "superseded" for item in concepts))
        self.assertFalse(any(item.publishable for item in concepts))
        labels = {item.public_label.casefold() for item in concepts}
        self.assertTrue(labels.isdisjoint(RESERVED_LEGACY_LABELS))

    def test_hierarchy_has_codable_broad_parents_and_transitive_lookup(self):
        edges = load_hierarchy()
        self.assertGreaterEqual(len(edges), 9)
        self.assertEqual(
            narrower_concept_ids("beh_0019"),
            frozenset(("beh_0005", "beh_0006", "beh_0007")),
        )
        self.assertEqual(
            narrower_concept_ids("beh_0045"),
            frozenset(("beh_0041", "beh_0042", "beh_0043")),
        )
        self.assertEqual(
            narrower_concept_ids("beh_0033"),
            frozenset(("beh_0034", "beh_0035", "beh_0036", "beh_0037")),
        )
        self.assertEqual(narrower_concept_ids("beh_0027"), frozenset())
        self.assertEqual(concepts_by_id()["beh_0027"].coding_scope, "broad")

    def test_superseded_concepts_name_their_neutral_replacements(self):
        replacements = concept_replacements()
        self.assertEqual(replacements["beh_0002"], "beh_0040")
        self.assertEqual(replacements["beh_0005"], "beh_0041")
        self.assertEqual(replacements["beh_0006"], "beh_0042")
        self.assertEqual(replacements["beh_0007"], "beh_0043")
        self.assertEqual(replacements["beh_0011"], "beh_0044")
        self.assertEqual(replacements["beh_0019"], "beh_0045")
        concepts = concepts_by_id()
        self.assertTrue(all(
            concepts[concept_id].status == "superseded"
            for concept_id in replacements
        ))

    def test_supersession_rejects_unknown_and_repeated_replacements(self):
        payload = _read_json(LEDGER_PATH)
        unknown = copy.deepcopy(payload)
        unknown["events"].append({
            "event_id": "vocab_evt_9998",
            "type": "concept_superseded",
            "recorded_at": "2026-08-24T21:00:00Z",
            "concept_id": "beh_0040",
            "replacement_id": "beh_9999",
            "rationale": "test",
        })
        with self.assertRaisesRegex(VocabularyError, "unknown concept"):
            validate_ledger(unknown)

        repeated = copy.deepcopy(payload)
        repeated["events"].append({
            "event_id": "vocab_evt_9998",
            "type": "concept_superseded",
            "recorded_at": "2026-08-24T21:00:00Z",
            "concept_id": "beh_0002",
            "replacement_id": "beh_0040",
            "rationale": "test",
        })
        with self.assertRaisesRegex(VocabularyError, "already superseded"):
            validate_ledger(repeated)

    def test_hierarchy_rejects_unknown_concepts_cycles_and_specific_parents(self):
        payload = _read_json(LEDGER_PATH)
        unknown = copy.deepcopy(payload)
        unknown["events"].append({
            "event_id": "vocab_evt_9998",
            "type": "hierarchy_edge_created",
            "recorded_at": "2026-08-24T18:00:00Z",
            "broader_id": "beh_0019",
            "narrower_id": "beh_9999",
        })
        with self.assertRaisesRegex(VocabularyError, "unknown concept"):
            validate_ledger(unknown)

        cycle = copy.deepcopy(payload)
        cycle["events"].append({
            "event_id": "vocab_evt_9998",
            "type": "hierarchy_edge_created",
            "recorded_at": "2026-08-24T18:00:00Z",
            "broader_id": "beh_0005",
            "narrower_id": "beh_0019",
        })
        cycle["events"][4]["concept"]["coding_scope"] = "broad"
        with self.assertRaisesRegex(VocabularyError, "cycle"):
            validate_ledger(cycle)

        specific_parent = copy.deepcopy(payload)
        specific_parent["events"][18]["concept"]["coding_scope"] = "specific"
        with self.assertRaisesRegex(VocabularyError, "broader concept"):
            validate_ledger(specific_parent)

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
        self.assertEqual(self.fixture["schema_version"], 2)
        self.assertIs(self.fixture["synthetic"], True)
        cases = self.fixture["cases"]
        self.assertGreaterEqual(len(cases), 37)
        eligibility = {case["expected"]["eligibility"] for case in cases}
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
            self.assertIn(expected["onset_precision"], VALID_ONSET_PRECISION)
            for raw in expected["observations"]:
                observation = validate_coded_observation(raw)
                self.assertIn(observation.specificity, VALID_SPECIFICITIES)
                self.assertIn(observation.valence, VALID_VALENCES)
                self.assertIn(observation.claim_status, VALID_CLAIM_STATUSES)
                self.assertTrue(
                    set(observation.suspected_layers) <= VALID_SUSPECTED_LAYERS)
                self.assertIn(
                    observation.elicitation_context, VALID_ELICITATION_CONTEXTS)
                concept = self.concepts[observation.concept_id]
                if concept.shape == "dimension":
                    self.assertIn(observation.state, VALID_STATES)
                    self.assertIn(observation.change, VALID_CHANGES)
                    self.assertIsNone(observation.event_state)
                else:
                    self.assertIsNone(observation.state)
                    self.assertIsNone(observation.change)
                    self.assertIn(observation.event_state, VALID_EVENT_STATES)

    def test_state_change_and_valence_are_independent(self):
        lost_warmth = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_026")
        observation = lost_warmth["expected"]["observations"][0]
        self.assertEqual(observation["state"], "low")
        self.assertEqual(observation["change"], "decrease")
        self.assertEqual(observation["valence"], "negative")

    def test_reports_can_be_co_coded_to_sibling_concepts(self):
        co_coded = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_027")
        concept_ids = {
            observation["concept_id"]
            for observation in co_coded["expected"]["observations"]
        }
        self.assertEqual(
            concept_ids, {"beh_0013", "beh_0014", "beh_0023"})
        validate_coded_observations(co_coded["expected"]["observations"])

    def test_parent_child_and_duplicate_storage_are_rejected(self):
        broad = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_025")
        factual = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_002")
        parent = copy.deepcopy(broad["expected"]["observations"][0])
        child = copy.deepcopy(factual["expected"]["observations"][1])
        with self.assertRaisesRegex(VocabularyError, "broad parent"):
            validate_coded_observations([parent, child])
        with self.assertRaisesRegex(VocabularyError, "same concept"):
            validate_coded_observations([child, copy.deepcopy(child)])

    def test_broad_parent_is_a_valid_final_code(self):
        broad = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_025")
        observation = broad["expected"]["observations"][0]
        self.assertEqual(observation["concept_id"], "beh_0045")
        self.assertEqual(observation["specificity"], "broad")
        validate_coded_observation(observation)

    def test_adversarial_elicitation_is_not_an_ordinary_use_signal(self):
        adversarial = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_033")
        self.assertEqual(
            adversarial["expected"]["eligibility"], "excluded_adversarial")
        observation = adversarial["expected"]["observations"][0]
        self.assertEqual(observation["elicitation_context"], "adversarial")
        coded = validate_coded_observation(observation)
        self.assertFalse(ordinary_use_signal_eligible(coded))

    def test_every_fixture_observation_starts_as_reported_not_proven(self):
        claim_statuses = {
            observation["claim_status"]
            for case in self.fixture["cases"]
            for observation in case["expected"]["observations"]
        }
        self.assertEqual(claim_statuses, {"reported"})

    def test_unsupported_affect_qualifier_is_rejected(self):
        affect = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_029")
        observation = copy.deepcopy(affect["expected"]["observations"][0])
        observation["qualifiers"] = ["definitely a tiny sad person inside"]
        with self.assertRaisesRegex(VocabularyError, "unsupported qualifier"):
            validate_coded_observation(observation)

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
        quality = next(
            case for case in self.fixture["cases"] if case["id"] == "eval_020")
        self.assertEqual(
            quality["expected"]["eligibility"], "uncodable_appraisal")
        self.assertEqual(quality["expected"]["observations"], [])


if __name__ == "__main__":
    unittest.main()
