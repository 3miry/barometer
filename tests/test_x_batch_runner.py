"""Daily X batches keep blind predictions and human decisions isolated."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from run_x_batch import (
    batch_paths, batch_status, create_batch, ensure_empty_review_db, load_batch,
    next_batch_id,
)


class XBatchRunnerTests(unittest.TestCase):
    def test_plan_allocates_a_versioned_unique_daily_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 25, 20, tzinfo=timezone.utc)
            self.assertEqual(next_batch_id(root, now), "2026-08-25-v5-01")
            manifest, paths = create_batch(
                root, "2026-08-25-v5-01", 80, 0.75)
            self.assertEqual(manifest["daily_read_limit"], 80)
            self.assertFalse(manifest["public_weather_updated"])
            self.assertTrue(paths["manifest"].is_file())
            self.assertEqual(next_batch_id(root, now), "2026-08-25-v5-02")

    def test_status_reads_each_batch_artifact_without_raw_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, paths = create_batch(
                root, "2026-08-25-v5-01", 80, 0.75)
            connection = sqlite3.connect(paths["source_db"])
            connection.executescript(
                "CREATE TABLE complaints(id TEXT);"
                "CREATE TABLE collection_runs(run_id TEXT);"
                "INSERT INTO complaints VALUES ('private-report');"
                "INSERT INTO collection_runs VALUES ('run-one');"
            )
            connection.commit()
            connection.close()
            paths["predictions"].write_text(json.dumps({
                "predictions": {"unit-one": {}},
                "failures": {"unit-two": {}},
            }), encoding="utf-8")

            status = batch_status(manifest, paths)

            self.assertEqual(status["source_reports"], 1)
            self.assertEqual(status["collection_runs"], 1)
            self.assertEqual(status["frozen_predictions"], 1)
            self.assertEqual(status["prediction_failures"], 1)
            self.assertNotIn("private-report", json.dumps(status))

    def test_paths_reject_unscoped_identifiers(self):
        with self.assertRaisesRegex(SystemExit, "batch id"):
            batch_paths(Path("batches"), "../escape")

    def test_non_plan_actions_cannot_invent_a_phantom_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "does not exist"):
                load_batch(Path(directory), "2026-08-25-v5-01")

    def test_classifier_can_initialise_a_new_empty_review_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.db"
            ensure_empty_review_db(path)
            self.assertTrue(path.is_file())
            connection = sqlite3.connect(path)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM classifier_review_units").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
