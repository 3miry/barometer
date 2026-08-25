"""Governed LLT-like retrieval terms remain separate from classifier coding."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from barometer.search_terms import (
    SearchTermError, latest_search_terms, pilot_search_terms,
    validate_search_term_ledger,
)


def term_event() -> dict:
    return {
        "event_id": "term_evt_0001",
        "type": "search_term_version_created",
        "recorded_at": "2026-08-25T19:00:00Z",
        "term": {
            "id": "llt_0001",
            "definition_version": 1,
            "phrase": "slow",
            "concept_id": "beh_0001",
            "known_ambiguities": ["May describe network latency"],
            "origin": "test",
            "lifecycle": "proposed",
            "created_at": "2026-08-25T19:00:00Z",
        },
    }


class SearchTermRegistryTests(unittest.TestCase):
    def test_seed_terms_are_pilot_retrieval_terms_with_neutral_links(self):
        terms = pilot_search_terms()
        self.assertEqual(
            {item.phrase for item in terms},
            {"slow", "fast", "nerfed", "upgraded", "lazy"},
        )
        self.assertEqual({item.lifecycle for item in terms}, {"pilot"})
        mapping = {item.phrase: item.concept_id for item in terms}
        self.assertEqual(mapping["slow"], "beh_0001")
        self.assertEqual(mapping["fast"], "beh_0001")
        self.assertEqual(mapping["nerfed"], "beh_0038")
        self.assertTrue(next(
            item for item in terms if item.phrase == "lazy"
        ).known_ambiguities)
        self.assertEqual(latest_search_terms(), terms)

    def test_phrase_cannot_smuggle_query_operators(self):
        payload = {"schema_version": 1, "events": [term_event()]}
        payload["events"][0]["term"]["phrase"] = "slow OR from:someone"
        with self.assertRaisesRegex(SearchTermError, "unsafe query syntax"):
            validate_search_term_ledger(payload)

    def test_lifecycle_cannot_skip_offline_test(self):
        payload = {"schema_version": 1, "events": [term_event()]}
        payload["events"].append({
            "event_id": "term_evt_0002",
            "type": "search_term_lifecycle_changed",
            "recorded_at": "2026-08-25T19:01:00Z",
            "term_id": "llt_0001",
            "definition_version": 1,
            "from": "proposed",
            "to": "pilot",
            "rationale": "skip",
        })
        with self.assertRaisesRegex(SearchTermError, "invalid.*transition"):
            validate_search_term_ledger(payload)

    def test_phrase_cannot_be_owned_by_two_llt_ids(self):
        first = term_event()
        second = copy.deepcopy(first)
        second["event_id"] = "term_evt_0002"
        second["term"]["id"] = "llt_0002"
        with self.assertRaisesRegex(SearchTermError, "multiple LLT IDs"):
            validate_search_term_ledger(
                {"schema_version": 1, "events": [first, second]})

    def test_new_proposed_version_does_not_disable_current_pilot(self):
        events = [term_event()]
        for number, before, after in (
            (2, "proposed", "offline-tested"),
            (3, "offline-tested", "pilot"),
        ):
            events.append({
                "event_id": f"term_evt_{number:04d}",
                "type": "search_term_lifecycle_changed",
                "recorded_at": f"2026-08-25T19:0{number}:00Z",
                "term_id": "llt_0001",
                "definition_version": 1,
                "from": before,
                "to": after,
                "rationale": "test transition",
            })
        second_version = copy.deepcopy(term_event())
        second_version["event_id"] = "term_evt_0004"
        second_version["recorded_at"] = "2026-08-25T19:04:00Z"
        second_version["term"]["definition_version"] = 2
        second_version["term"]["known_ambiguities"] = ["New draft wording"]
        events.append(second_version)

        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "events": events}, handle)
            path = handle.name
        try:
            terms = pilot_search_terms(path)
        finally:
            Path(path).unlink()
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].definition_version, 1)


if __name__ == "__main__":
    unittest.main()
