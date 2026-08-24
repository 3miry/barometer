"""Private classifier review queue, storage, and localhost server tests."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from barometer.review_app import build_review_items, review_metadata
from barometer.reviews import (
    ReviewError, ReviewStore, source_fingerprint, validate_review_decision,
)
from barometer.store import SCHEMA
from barometer.vocabulary import concepts_by_id
from review_classifier import ReviewHandler, ReviewServer


def make_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO complaints(id,ts,source,model,text,url,seed_url,variant) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                "report-one", 1_700_000_100.0, "x", "claude",
                "Claude keeps making mistakes lately.",
                "https://example.invalid/one", None, "claude-opus-5",
            ),
            (
                "report-two", 1_700_000_000.0, "hn", "gpt",
                "GPT-5.6 wrote this article about databases.",
                "https://example.invalid/two", None, "gpt-5-6",
            ),
        ],
    )
    connection.commit()
    connection.close()


def observation(concept_id: str) -> dict:
    concept = concepts_by_id()[concept_id]
    return {
        "concept_id": concept_id,
        "specificity": concept.coding_scope,
        "state": concept.allowed_states[0] if concept.shape == "dimension" else None,
        "change": concept.allowed_changes[0] if concept.shape == "dimension" else None,
        "event_state": (
            concept.allowed_event_states[0] if concept.shape == "event" else None
        ),
        "valence": "unstated",
        "claim_status": "reported",
        "suspected_layers": ["unknown"],
        "elicitation_context": "ordinary",
        "qualifiers": [],
    }


class ReviewValidationTests(unittest.TestCase):
    def test_rejects_wrong_variant_and_causal_promotion(self):
        base = {
            "status": "corrected",
            "target_family": "claude",
            "target_variant": "gpt-5-6",
            "observations": [],
            "novelty_candidates": [],
            "review_note": "",
        }
        with self.assertRaises(ReviewError):
            validate_review_decision("one", "hash", base)
        promoted = observation("beh_0019")
        promoted["claim_status"] = "attributed"
        with self.assertRaises(ReviewError):
            validate_review_decision(
                "one", "hash", {
                    **base,
                    "target_variant": "claude-opus-5",
                    "observations": [promoted],
                },
            )

    def test_rejects_parent_child_double_coding(self):
        with self.assertRaises(ReviewError):
            validate_review_decision(
                "one", "hash", {
                    "status": "corrected",
                    "target_family": "claude",
                    "target_variant": "claude-opus-5",
                    "observations": [
                        observation("beh_0019"), observation("beh_0005"),
                    ],
                    "novelty_candidates": [],
                    "review_note": "",
                },
            )


class ReviewStorageTests(unittest.TestCase):
    def test_build_is_read_only_and_review_db_contains_no_raw_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            review = Path(directory) / "private" / "reviews.db"
            make_source(source)
            before = source.read_bytes()

            items = build_review_items(source, review)

            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["report_id"], "report-one")
            connection = sqlite3.connect(review)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(classifier_reviews)")
                }
            finally:
                connection.close()
            self.assertFalse(columns & {"text", "raw_text", "description", "url"})
            self.assertIn("observations_json", columns)

    def test_saved_decision_is_joined_and_stale_source_is_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            review = Path(directory) / "reviews.db"
            make_source(source)
            text = "Claude keeps making mistakes lately."
            decision = validate_review_decision(
                "report-one", source_fingerprint("report-one", text), {
                    "status": "approved",
                    "target_family": "claude",
                    "target_variant": "claude-opus-5",
                    "observations": [observation("beh_0019")],
                    "novelty_candidates": [],
                    "review_note": "sounds right",
                }, now=123.0,
            )
            with ReviewStore(str(review)) as store:
                store.put(decision)
            first = build_review_items(source, review)[0]
            self.assertEqual(first["decision"]["status"], "approved")
            connection = sqlite3.connect(source)
            connection.execute(
                "UPDATE complaints SET text=? WHERE id='report-one'",
                ("Claude now makes different mistakes.",),
            )
            connection.commit()
            connection.close()
            stale = build_review_items(source, review)[0]
            self.assertTrue(stale["decision"]["stale"])

    def test_metadata_includes_hierarchy_parent(self):
        concepts = {item["id"]: item for item in review_metadata()["concepts"]}
        self.assertEqual(concepts["beh_0005"]["parent"], "correctness")


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.db"
        self.review = Path(self.temp.name) / "private" / "reviews.db"
        make_source(self.source)
        self.server = ReviewServer(
            ("127.0.0.1", 0), ReviewHandler,
            str(self.source), str(self.review),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def test_bootstrap_and_review_round_trip(self):
        status, headers, body = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        bootstrap = json.loads(body)
        self.assertEqual(len(bootstrap["items"]), 2)
        item = bootstrap["items"][0]
        proposal = item["proposal"]
        payload = json.dumps({
            "status": "approved",
            "target_family": item["stored_family"],
            "target_variant": item["stored_variant"],
            "observations": proposal["observations"],
            "novelty_candidates": proposal["novelty_candidates"],
            "review_note": "reviewed in test",
            "source_fingerprint": item["source_fingerprint"],
        })
        status, _, body = self.request(
            "POST", f"/api/reviews/{item['report_id']}", payload, {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["decision"]["status"], "approved")
        with ReviewStore(str(self.review)) as store:
            self.assertEqual(store.get(item["report_id"])["review_note"],
                             "reviewed in test")

    def test_cross_origin_bad_token_and_stale_writes_are_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one", "{}", {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
                "Origin": "https://malicious.example",
            },
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one", "{}", {
                "Content-Type": "application/json",
                "X-Review-Token": "wrong",
            },
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one",
            json.dumps({"source_fingerprint": "old"}), {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
            },
        )
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
