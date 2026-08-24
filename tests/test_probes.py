"""Offline probe-registry and collection-provenance contracts."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from barometer.detect import Complaint
from barometer.probes import (
    PROBE_LEDGER_PATH,
    CollectionRun,
    ProbeRegistryError,
    load_probe_versions,
    validate_probe_ledger,
)
from barometer.store import Store


def probe_event(event_id="probe_evt_0001") -> dict:
    return {
        "event_id": event_id,
        "type": "probe_version_created",
        "recorded_at": "2026-08-24T22:00:00Z",
        "probe": {
            "id": "probe.hn.factual_accuracy",
            "definition_version": 1,
            "source": "hn",
            "model_families": ["claude"],
            "exact_query": "Claude factual accuracy",
            "intended_concept_ids": ["beh_0041"],
            "known_ambiguities": ["Generic discussion of benchmarks"],
            "exclusions": ["Quoted claims without reporter endorsement"],
            "returned_item_cap": 20,
            "cost_ceiling_usd": None,
            "lifecycle": "proposed",
            "created_at": "2026-08-24T22:00:00Z",
        },
    }


class ProbeRegistryTests(unittest.TestCase):
    def test_checked_in_registry_is_empty_and_non_active(self):
        self.assertEqual(load_probe_versions(), ())
        with PROBE_LEDGER_PATH.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["events"], [])

    def test_probe_versions_and_lifecycle_are_append_only(self):
        payload = {"schema_version": 1, "events": [probe_event()]}
        payload["events"].append({
            "event_id": "probe_evt_0002",
            "type": "probe_lifecycle_changed",
            "recorded_at": "2026-08-24T22:10:00Z",
            "probe_id": "probe.hn.factual_accuracy",
            "definition_version": 1,
            "from": "proposed",
            "to": "offline-tested",
            "rationale": "Synthetic retained-data replay passed.",
        })
        probes = validate_probe_ledger(payload)
        self.assertEqual(probes[0].lifecycle, "offline-tested")

        invalid = copy.deepcopy(payload)
        invalid["events"].append({
            "event_id": "probe_evt_0003",
            "type": "probe_lifecycle_changed",
            "recorded_at": "2026-08-24T22:20:00Z",
            "probe_id": "probe.hn.factual_accuracy",
            "definition_version": 1,
            "from": "offline-tested",
            "to": "active",
            "rationale": "Skipped supervised pilot.",
        })
        with self.assertRaisesRegex(ProbeRegistryError, "invalid.*transition"):
            validate_probe_ledger(invalid)

    def test_probe_cannot_target_superseded_problem_label(self):
        payload = {"schema_version": 1, "events": [probe_event()]}
        payload["events"][0]["probe"]["intended_concept_ids"] = ["beh_0005"]
        with self.assertRaisesRegex(ProbeRegistryError, "superseded"):
            validate_probe_ledger(payload)


class CollectionProvenanceTests(unittest.TestCase):
    def run_record(self, run_id: str, lane: str, query_id: str) -> CollectionRun:
        return CollectionRun(
            run_id=run_id,
            source="hn",
            lane=lane,
            query_id=query_id,
            query_version=1,
            started_at=100.0,
            completed_at=101.0,
            returned_candidates=1,
            retained_candidates=1,
            item_cap=20,
            saturated=False,
            frame_note="synthetic test run",
        )

    def test_duplicate_report_retains_every_query_run(self):
        report = Complaint(
            100.0, "hn", "claude", "Claude is more accurate now.",
            "https://news.ycombinator.com/item?id=one",
        )
        with tempfile.TemporaryDirectory() as directory:
            with Store(os.path.join(directory, "barometer.db")) as store:
                first = self.run_record(
                    "run-discovery", "discovery", "hn.discovery.claude")
                second = self.run_record(
                    "run-targeted", "targeted", "probe.hn.factual_accuracy")
                self.assertEqual(
                    store.add_complaints([report], first, [1]), 1)
                self.assertEqual(
                    store.add_complaints([report], second, [1]), 0)
                provenance = store.complaint_provenance()
                self.assertEqual(len(provenance), 2)
                self.assertEqual(
                    {item["lane"] for item in provenance},
                    {"discovery", "targeted"},
                )
                self.assertEqual(len(store.collection_run_records()), 2)

    def test_conflicting_run_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with Store(os.path.join(directory, "barometer.db")) as store:
                original = self.run_record(
                    "same-run", "discovery", "hn.discovery.claude")
                store.record_collection_run(original)
                conflict = self.run_record(
                    "same-run", "targeted", "probe.hn.factual_accuracy")
                with self.assertRaisesRegex(ValueError, "different metadata"):
                    store.record_collection_run(conflict)


if __name__ == "__main__":
    unittest.main()
