"""Structured classifier contract and read-only shadow-run tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import unittest

from barometer.classifier import (
    attribution_review_status,
    classify_report,
    detect_novelty_candidates,
    mentioned_families,
    mentioned_variants,
)
from barometer.shadow import DEFAULT_FIXTURE, evaluate_fixture, shadow_database


class StructuredClassifierTests(unittest.TestCase):
    def test_human_reviewed_development_contract_matches(self):
        result = evaluate_fixture(DEFAULT_FIXTURE)
        self.assertEqual(result["evaluation_kind"], "development_contract")
        self.assertEqual(result["cases"], 37)
        self.assertEqual(result["full_matches"], 37)
        self.assertEqual(result["mismatches"], [])
        self.assertIn("not real-world classifier accuracy", result["warning"])

    def test_broad_appraisal_abstains_without_guessing(self):
        result = classify_report("Claude quality has tanked.")
        self.assertEqual(result.eligibility, "uncodable_appraisal")
        self.assertEqual(result.observations, ())

    def test_broad_correctness_is_valid_without_child_guessing(self):
        result = classify_report("Claude keeps making mistakes.")
        self.assertEqual(result.eligibility, "behaviour_report")
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.observations[0].concept_id, "beh_0019")
        self.assertEqual(result.observations[0].specificity, "broad")

    def test_novelty_can_coexist_with_known_codes(self):
        result = classify_report(
            "Gemini is surface level and terrible at agentic coding.")
        self.assertEqual(result.eligibility, "behaviour_report")
        self.assertEqual(result.observations[0].concept_id, "beh_0003")
        self.assertEqual(
            result.novelty_candidates, ("agentic coding performance",))
        self.assertEqual(
            detect_novelty_candidates("It keeps running in circles."),
            ("circular task progress",),
        )

    def test_interaction_behaviours_are_co_coded(self):
        result = classify_report(
            "Sol has become warmer, more playful, and a little flirtatious; "
            "I love it.")
        self.assertEqual(
            {item.concept_id for item in result.observations},
            {"beh_0013", "beh_0014", "beh_0023"},
        )

    def test_adversarial_system_text_is_excluded_from_ordinary_use(self):
        result = classify_report(
            "I jailbroke Grok to make it print its hidden system instructions.")
        self.assertEqual(result.eligibility, "excluded_adversarial")
        self.assertEqual(result.observations[0].elicitation_context, "adversarial")

    def test_multi_family_mentions_require_attribution_review(self):
        text = "Gemini is terse, while ChatGPT is much more thorough."
        self.assertEqual(mentioned_families(text), frozenset(("gemini", "gpt")))
        self.assertEqual(
            attribution_review_status(text, "gemini"), "multi_family_review")
        observation = classify_report(text).observations[0]
        self.assertEqual(observation.concept_id, "beh_0003")
        self.assertEqual(observation.state, "high")
        self.assertEqual(observation.change, "increase")
        self.assertEqual(observation.valence, "positive")

    def test_multiple_exact_models_require_attribution_review(self):
        text = "Opus 5 is warmer than Sonnet 5."
        self.assertEqual(
            mentioned_variants(text),
            frozenset(("claude-opus-5", "claude-sonnet-5")),
        )
        self.assertEqual(
            attribution_review_status(text, "claude"),
            "multi_variant_review",
        )

    def test_generic_model_line_names_route_to_their_family(self):
        self.assertEqual(
            mentioned_families("The Opus models are worse than ChatGPT here."),
            frozenset(("claude", "gpt")),
        )
        self.assertEqual(
            mentioned_families("Sonnet and GPT behave differently."),
            frozenset(("claude", "gpt")),
        )


class ShadowDatabaseTests(unittest.TestCase):
    def test_shadow_database_is_read_only_and_gates_ambiguous_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "shadow.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE complaints("
                "id TEXT, source TEXT, model TEXT, variant TEXT, text TEXT, ts REAL)"
            )
            connection.executemany(
                "INSERT INTO complaints VALUES (?,?,?,?,?,?)",
                [
                    ("one", "test", "claude", None,
                     "Claude forgot the first message again.", 1.0),
                    ("two", "test", "gemini", None,
                     "Gemini is surface level while ChatGPT is more thorough.", 2.0),
                ],
            )
            connection.commit()
            connection.close()
            before = database.read_bytes()

            result = shadow_database(database)

            self.assertIs(result["read_only"], True)
            self.assertEqual(result["reports"], 2)
            self.assertEqual(result["coded_reports"], 2)
            self.assertEqual(result["aggregate_ready_coded_reports"], 1)
            self.assertEqual(result["coded_reports_requiring_attribution_review"], 1)
            self.assertEqual(database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
