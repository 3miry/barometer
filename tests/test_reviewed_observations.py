"""Human-reviewed observations cross the public boundary as aggregates only."""
from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import run_barometer

from barometer.cli import tick
from barometer.detect import Complaint, cascade_clusters
from barometer.reviewed_observations import load_reviewed_observations
from barometer.reviews import (
    ReviewStore, review_unit_id, source_fingerprint, validate_review_decision,
)
from barometer.store import SCHEMA, Store
from barometer.vocabulary import concepts_by_id


NOW = 1_787_620_000.0
RAW_SECRET = "RAW_TWEET_SECRET Opus has become very slow lately"


def observation(concept_id: str, valence: str, change: str) -> dict:
    concept = concepts_by_id()[concept_id]
    return {
        "concept_id": concept_id,
        "specificity": concept.coding_scope,
        "state": "low" if concept.shape == "dimension" else None,
        "change": change if concept.shape == "dimension" else None,
        "event_state": "occurred" if concept.shape == "event" else None,
        "valence": valence,
        "claim_status": "reported",
        "suspected_layers": ["unknown"],
        "elicitation_context": "ordinary",
        "qualifiers": [],
    }


def make_databases(root: Path) -> tuple[Path, Path]:
    source = root / "source.db"
    review = root / "reviews.db"
    connection = sqlite3.connect(source)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO complaints(id,ts,source,model,text,url,seed_url,variant,"
        "author_id,author_handle) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "private-source-id", NOW - 60, "x", "claude", RAW_SECRET,
            "https://x.example/private", None, "claude-opus-5",
            "private-author-id", "private_handle",
        ),
    )
    connection.commit()
    connection.close()
    payload = {
        "status": "corrected",
        "target_family": "claude",
        "target_variant": "claude-opus-5",
        "observations": [
            observation("beh_0001", "negative", "increase"),
            observation("beh_0008", "positive", "increase"),
        ],
        "novelty_candidates": [],
        "review_note": "PRIVATE REVIEW NOTE",
    }
    decision = validate_review_decision(
        review_unit_id("private-source-id", "claude", "claude-opus-5"),
        "private-source-id",
        source_fingerprint("private-source-id", RAW_SECRET),
        payload,
        now=NOW,
    )
    with ReviewStore(str(review)) as store:
        store.put(decision)
    return source, review


class ReviewedObservationTests(unittest.TestCase):
    def test_provisional_preview_cannot_target_default_public_directory(self):
        base = [
            "--reviewed-source-db", "source.db",
            "--classifier-review-db", "review.db",
            "--include-provisional-review-concepts",
        ]
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                run_barometer.parse_args(base)
            with self.assertRaises(SystemExit):
                run_barometer.parse_args(
                    base + ["--out-dir", "observation/public"])
        args = run_barometer.parse_args(
            base + ["--out-dir", "observation/preview_reviewed"])
        self.assertTrue(args.include_provisional_review_concepts)

    def test_production_gate_withholds_provisional_vocabulary(self):
        with tempfile.TemporaryDirectory() as directory:
            source, review = make_databases(Path(directory))
            result = load_reviewed_observations(source, review, since=NOW - 3600)
        self.assertEqual(result.complaints, ())
        self.assertEqual(result.withheld_unpublishable_observations, 2)
        self.assertEqual(result.promoted_slices, 0)

    def test_preview_is_structured_private_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            source, review = make_databases(Path(directory))
            source_before = source.read_bytes()
            review_before = review.read_bytes()
            result = load_reviewed_observations(
                source, review, since=NOW - 3600, include_provisional=True)
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(review.read_bytes(), review_before)

        self.assertEqual(result.promoted_slices, 1)
        self.assertEqual(result.promoted_observations, 2)
        self.assertEqual(result.provisional_observations_included, 2)
        complaint = result.complaints[0]
        self.assertEqual(complaint.governed_themes,
                         ("latency", "writing clarity"))
        self.assertEqual(complaint.governed_valences,
                         ("negative", "positive"))
        self.assertEqual(complaint.governed_changes, ("increase",))
        self.assertEqual(complaint.variant, "claude-opus-5")
        public_fields = " ".join(filter(None, (
            complaint.text, complaint.url, complaint.seed_url,
            complaint.author_id, complaint.author_handle,
        )))
        self.assertNotIn(RAW_SECRET, public_fields)
        self.assertNotIn("private-source-id", public_fields)
        self.assertNotIn("private_handle", public_fields)

    def test_active_publishable_concepts_promote_without_preview_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            source, review = make_databases(Path(directory))
            concepts = concepts_by_id()
            concepts["beh_0001"] = replace(
                concepts["beh_0001"], status="active", publishable=True)
            with patch(
                    "barometer.reviewed_observations.concepts_by_id",
                    return_value=concepts):
                result = load_reviewed_observations(
                    source, review, since=NOW - 3600)
        self.assertEqual(result.promoted_slices, 1)
        self.assertEqual(result.promoted_observations, 1)
        self.assertEqual(
            result.complaints[0].governed_themes, ("latency",))
        self.assertEqual(result.withheld_unpublishable_observations, 1)

    def test_stale_source_fingerprint_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            source, review = make_databases(Path(directory))
            connection = sqlite3.connect(source)
            connection.execute(
                "UPDATE complaints SET text='changed after review'")
            connection.commit()
            connection.close()
            result = load_reviewed_observations(
                source, review, include_provisional=True)
        self.assertEqual(result.complaints, ())
        self.assertEqual(result.stale_decisions, 1)

    def test_dashboard_contains_only_governed_aggregates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, review = make_databases(root)
            promoted = load_reviewed_observations(
                source, review, since=NOW - 3600,
                include_provisional=True)
            out = root / "public"
            snapshot = out / "summary.json"
            with Store(str(root / "barometer.db")) as store:
                report = tick(
                    store, [], out_dir=str(out), now=NOW,
                    public_snapshot=str(snapshot),
                    reviewed_reports=list(promoted.complaints),
                )
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            page = (out / "index.html").read_text(encoding="utf-8")
            detail = (out / "barometer_claude.html").read_text(encoding="utf-8")
            snapshot_text = snapshot.read_text(encoding="utf-8")

        self.assertEqual(report["reviewed_reports"], 1)
        claude = payload["models"]["claude"]
        self.assertEqual(claude["categories"], {
            "latency": 1, "writing clarity": 1,
        })
        self.assertEqual(claude["valences"], {
            "negative": 1, "positive": 1,
        })
        self.assertEqual(claude["changes"], {"increase": 1})
        combined = page + detail + snapshot_text
        for secret in (
                RAW_SECRET, "private-source-id", "private-author-id",
                "private_handle", "PRIVATE REVIEW NOTE", "x.example/private"):
            self.assertNotIn(secret, combined)
        self.assertIn("latency", combined)
        self.assertIn("writing clarity", combined)
        self.assertNotIn("provisional vocabulary", combined)

    def test_private_dedup_keys_prevent_synthetic_text_collapse(self):
        first = Complaint(
            NOW, "x", "claude", "Human-reviewed structured observation.",
            dedup_key="one")
        same_source = Complaint(
            NOW, "x", "claude", "Human-reviewed structured observation.",
            dedup_key="one")
        second = Complaint(
            NOW, "x", "claude", "Human-reviewed structured observation.",
            dedup_key="two")
        self.assertEqual(len(cascade_clusters([first, same_source, second])), 2)


if __name__ == "__main__":
    unittest.main()
