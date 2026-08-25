"""Human-review replay metrics remain aggregate and read-only."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from barometer.detect import Complaint
from barometer.probes import CollectionRun
from barometer.review_analysis import analyze_review_batch
from barometer.review_app import build_review_items
from barometer.reviews import (
    ReviewStore,
    validate_review_decision,
)
from barometer.store import Store


class ReviewAnalysisTests(unittest.TestCase):
    def test_analysis_is_read_only_aggregate_and_provenance_aware(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            review = Path(directory) / "review.db"
            governed_text = "Opus 5 keeps making mistakes SECRET_ANALYSIS_TEXT."
            chatter_text = "Claude wrote this article SECRET_CHATTER_TEXT."
            run = CollectionRun(
                run_id="run-one", source="hn", lane="targeted",
                query_id="probe.hn.general_correctness", query_version=1,
                started_at=100.0, completed_at=101.0,
                returned_candidates=1, retained_candidates=1,
                item_cap=20,
            )
            with Store(str(source)) as store:
                store.add_complaints([
                    Complaint(
                        100.0, "hn", "claude", governed_text,
                        "https://example.invalid/governed"),
                ], run, [1])
                store.add_complaints([
                    Complaint(
                        99.0, "hn", "claude", chatter_text,
                        "https://example.invalid/chatter"),
                ])

            items = build_review_items(source, review)
            with ReviewStore(str(review)) as store:
                for item in items:
                    proposal = item["proposal"]
                    positive = bool(proposal["observations"])
                    decision = validate_review_decision(
                        item["review_unit_id"], item["report_id"],
                        item["source_fingerprint"], {
                            "status": "approved" if positive else "rejected",
                            "target_family": item["seed_family"],
                            "target_variant": item["seed_variant"],
                            "observations": proposal["observations"],
                            "novelty_candidates": (
                                proposal["novelty_candidates"] if positive else []),
                            "review_note": "synthetic review",
                        }, now=200.0,
                    )
                    store.put(decision)

            before_source = source.read_bytes()
            before_review = review.read_bytes()
            result = analyze_review_batch(source, review)
            self.assertEqual(source.read_bytes(), before_source)
            self.assertEqual(review.read_bytes(), before_review)
            self.assertTrue(result["complete"])
            self.assertEqual(result["source_reports"], 2)
            self.assertEqual(result["source_outcomes"], {
                "governed_report": 1,
                "no_attributable_signal": 1,
            })
            self.assertEqual(
                result["single_target_governed_detection"]["true_positive"], 1)
            self.assertEqual(
                result["single_target_governed_detection"]["true_negative"], 1)
            provenance = result["collection_provenance"]
            self.assertTrue(provenance["query_provenance_available"])
            self.assertEqual(provenance["reports_with_query_provenance"], 1)
            self.assertEqual(provenance["reports_without_query_provenance"], 1)
            lane_yield = provenance["human_yield_by_lane_membership"]
            self.assertEqual(
                lane_yield["targeted_only"]["useful_reports"], 1)
            self.assertEqual(
                lane_yield["no_query_provenance"]["useful_reports"], 0)
            self.assertEqual(
                result["human_yield_by_temporal_language"]
                ["no_temporal_cue"]["source_reports"],
                2,
            )
            targeting = result["query_targeting_hypothesis"]
            self.assertEqual(
                targeting["buckets"]["exact_variant"]["useful_reports"], 1)
            self.assertEqual(
                targeting["buckets"]["product_family"]["source_reports"], 1)
            self.assertEqual(
                targeting["exact_variant_or_model_line"]
                ["useful_capture_share"],
                1.0,
            )
            rendered = json.dumps(result)
            self.assertNotIn("SECRET_ANALYSIS_TEXT", rendered)
            self.assertNotIn("SECRET_CHATTER_TEXT", rendered)


if __name__ == "__main__":
    unittest.main()
