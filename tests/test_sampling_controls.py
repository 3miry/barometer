"""Private source suppressions are reversible and contain no report text."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from barometer.sampling_controls import (
    SamplingControlError, SamplingControlStore,
)


class SamplingControlTests(unittest.TestCase):
    def test_query_exclusion_is_reversible_and_rejects_query_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.db"
            with SamplingControlStore(path) as store:
                active = store.set_query_exclusion(
                    "x", "Best", "repeated_chatter", active=True, now=10.0)
                self.assertEqual(active.phrase, "best")
                self.assertEqual(
                    [item.phrase for item in store.active_query_exclusions("x")],
                    ["best"],
                )
                paused = store.set_query_exclusion(
                    "x", "best", "repeated_chatter", active=False, now=20.0)
                self.assertFalse(paused.active)
                self.assertEqual(store.active_query_exclusions("x"), [])
                with self.assertRaises(SamplingControlError):
                    store.set_query_exclusion(
                        "x", "best OR from:someone", "other", active=True)

    def test_source_suppression_round_trip_is_reversible_and_auditable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.db"
            with SamplingControlStore(path) as store:
                active = store.set_source_suppression(
                    "x", "12345", "course_bot", "marketing",
                    active=True, now=100.0)
                self.assertTrue(active.active)
                self.assertEqual(store.active("x"), [active])
                restored = store.set_source_suppression(
                    "x", "12345", "course_bot", "marketing",
                    active=False, now=200.0)
                self.assertFalse(restored.active)
                self.assertEqual(store.active("x"), [])
                self.assertEqual(len(store.all()), 1)
                self.assertEqual(restored.created_at, 100.0)
                self.assertEqual(restored.updated_at, 200.0)
            self.assertNotIn(b"tweet text", path.read_bytes())
            self.assertNotIn("text", json.dumps(restored.as_dict()))

    def test_invalid_source_and_reason_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with SamplingControlStore(Path(directory) / "controls.db") as store:
                with self.assertRaises(SamplingControlError):
                    store.set_source_suppression(
                        "reddit", "123", "name", "marketing", active=True)
                with self.assertRaises(SamplingControlError):
                    store.set_source_suppression(
                        "x", "123", "name", "because_i_said_so", active=True)


if __name__ == "__main__":
    unittest.main()
