"""Private classifier review queue, storage, and localhost server tests."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from barometer.review_app import build_review_items, render_review_page, review_metadata
from barometer.reviews import (
    ReviewError, ReviewStore, review_unit_id, source_fingerprint,
    validate_review_decision,
)
from barometer.store import SCHEMA
from barometer.sampling_controls import SamplingControlStore
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
            (
                "report-three", 1_699_999_900.0, "hn", "claude",
                "Compared to Claude and Gemini, ChatGPT is much more thorough. "
                "Claude and Gemini give shorter answers.",
                "https://example.invalid/three", None, None,
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
            validate_review_decision("one::claude", "one", "hash", base)
        promoted = observation("beh_0045")
        promoted["claim_status"] = "attributed"
        with self.assertRaises(ReviewError):
            validate_review_decision(
                "one::claude", "one", "hash", {
                    **base,
                    "target_variant": "claude-opus-5",
                    "observations": [promoted],
                },
            )

    def test_rejects_parent_child_double_coding(self):
        with self.assertRaises(ReviewError):
            validate_review_decision(
                "one::claude", "one", "hash", {
                    "status": "corrected",
                    "target_family": "claude",
                    "target_variant": "claude-opus-5",
                    "observations": [
                        observation("beh_0045"), observation("beh_0041"),
                    ],
                    "novelty_candidates": [],
                    "review_note": "",
                },
            )

    def test_rejects_new_saves_using_a_superseded_concept(self):
        with self.assertRaisesRegex(ReviewError, "must be replaced"):
            validate_review_decision(
                "one::claude", "one", "hash", {
                    "status": "corrected",
                    "target_family": "claude",
                    "target_variant": "claude-opus-5",
                    "observations": [observation("beh_0019")],
                    "novelty_candidates": [],
                    "review_note": "",
                },
            )


class ReviewStorageTests(unittest.TestCase):
    def test_review_page_groups_comparisons_and_keeps_actions_click_only(self):
        page = render_review_page("test-token").decode("utf-8")

        self.assertIn("Reviewer guide · what each decision means", page)
        self.assertIn("Comparison source", page)
        self.assertIn("Previous target", page)
        self.assertIn("aria-live=\"polite\"", page)
        self.assertIn("key==='j'", page)
        self.assertIn("ev.key==='['", page)
        self.assertNotIn("key==='a'", page)
        self.assertNotIn("key==='d'", page)
        self.assertIn("reclassify", page)
        self.assertIn("data-replace", page)
        self.assertIn("Suppress future posts", page)
        self.assertIn("affects future collection only", page)
        self.assertIn("Signal + temporal priority", page)
        self.assertIn("Temporal only", page)
        self.assertIn("behaviour_report:40", page)
        self.assertIn("temporal_priority.score", page)

    def test_build_is_read_only_and_review_db_contains_no_raw_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            review = Path(directory) / "private" / "reviews.db"
            make_source(source)
            before = source.read_bytes()

            items = build_review_items(source, review)

            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(len(items), 5)
            self.assertEqual(items[0]["report_id"], "report-one")
            self.assertEqual(items[0]["temporal_priority"]["score"], 3)
            self.assertEqual(
                items[0]["temporal_priority"]["cues"], ["lately"])
            connection = sqlite3.connect(review)
            try:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(classifier_review_units)")
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
                review_unit_id("report-one", "claude", None),
                "report-one", source_fingerprint("report-one", text), {
                    "status": "approved",
                    "target_family": "claude",
                    "target_variant": "claude-opus-5",
                    "observations": [observation("beh_0045")],
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

    def test_multi_model_source_becomes_independent_target_slices(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            review = Path(directory) / "reviews.db"
            make_source(source)
            slices = [
                item for item in build_review_items(source, review)
                if item["report_id"] == "report-three"
            ]
            self.assertEqual(
                {item["seed_family"] for item in slices},
                {"claude", "gemini", "gpt"},
            )
            self.assertEqual(len({item["review_unit_id"] for item in slices}), 3)
            self.assertTrue(all(item["target_count"] == 3 for item in slices))
            self.assertTrue(all(item["proposal"]["observations"] == []
                                for item in slices))
            self.assertTrue(all(
                item["proposal"]["target_attribution_required"]
                for item in slices
            ))
            fingerprint = slices[0]["source_fingerprint"]
            claude_detail = observation("beh_0003")
            claude_detail.update({
                "state": "low", "change": "decrease", "valence": "negative",
            })
            gpt_detail = observation("beh_0003")
            gpt_detail.update({
                "state": "high", "change": "increase", "valence": "positive",
            })
            decisions = [
                validate_review_decision(
                    review_unit_id("report-three", "claude", None),
                    "report-three", fingerprint, {
                        "status": "corrected", "target_family": "claude",
                        "target_variant": None,
                        "observations": [claude_detail],
                        "novelty_candidates": [], "review_note": "comparison",
                    }, now=100.0,
                ),
                validate_review_decision(
                    review_unit_id("report-three", "gpt", None),
                    "report-three", fingerprint, {
                        "status": "corrected", "target_family": "gpt",
                        "target_variant": None,
                        "observations": [gpt_detail],
                        "novelty_candidates": [], "review_note": "comparison",
                    }, now=101.0,
                ),
            ]
            with ReviewStore(str(review)) as store:
                for decision in decisions:
                    store.put(decision)
                saved_claude = store.get("report-three::claude")
                saved_gpt = store.get("report-three::gpt")
            self.assertEqual(
                saved_claude["observations"][0]["valence"], "negative")
            self.assertEqual(
                saved_gpt["observations"][0]["valence"], "positive")

    def test_metadata_includes_hierarchy_parent(self):
        concepts = {item["id"]: item for item in review_metadata()["concepts"]}
        self.assertEqual(concepts["beh_0041"]["parent"], "correctness")
        self.assertEqual(concepts["beh_0041"]["status"], "provisional")
        self.assertEqual(concepts["beh_0005"]["status"], "superseded")
        self.assertEqual(concepts["beh_0005"]["replacement_id"], "beh_0041")

    def test_legacy_single_report_decision_is_preserved_as_target_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "reviews.db"
            connection = sqlite3.connect(review)
            connection.execute(
                "CREATE TABLE classifier_reviews("
                "report_id TEXT PRIMARY KEY,source_fingerprint TEXT NOT NULL,"
                "classifier_version TEXT NOT NULL,status TEXT NOT NULL,"
                "target_family TEXT,target_variant TEXT,"
                "observations_json TEXT NOT NULL,novelty_json TEXT NOT NULL,"
                "review_note TEXT,reviewed_at REAL NOT NULL)"
            )
            connection.execute(
                "INSERT INTO classifier_reviews VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "old-report", "fingerprint", "old-rules", "deferred",
                    "gpt", None, "[]", "[]", "keep this", 100.0,
                ),
            )
            connection.commit()
            connection.close()
            with ReviewStore(str(review)) as store:
                migrated = store.get("old-report::gpt")
            self.assertIsNotNone(migrated)
            self.assertEqual(migrated["source_report_id"], "old-report")
            self.assertEqual(migrated["review_note"], "keep this")


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.db"
        self.review = Path(self.temp.name) / "private" / "reviews.db"
        make_source(self.source)
        connection = sqlite3.connect(self.source)
        connection.execute(
            "UPDATE complaints SET author_id=?,author_handle=? WHERE id=?",
            ("12345", "course_bot", "report-one"),
        )
        connection.commit()
        connection.close()
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
        self.assertEqual(len(bootstrap["items"]), 5)
        item = bootstrap["items"][0]
        proposal = item["proposal"]
        payload = json.dumps({
            "status": "approved",
            "target_family": item["seed_family"],
            "target_variant": item["seed_variant"],
            "observations": proposal["observations"],
            "novelty_candidates": proposal["novelty_candidates"],
            "review_note": "reviewed in test",
            "source_fingerprint": item["source_fingerprint"],
        })
        status, _, body = self.request(
            "POST", f"/api/reviews/{item['review_unit_id']}", payload, {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["decision"]["status"], "approved")
        with ReviewStore(str(self.review)) as store:
            self.assertEqual(store.get(item["review_unit_id"])["review_note"],
                             "reviewed in test")

    def test_cross_origin_bad_token_and_stale_writes_are_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one%3A%3Aclaude", "{}", {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
                "Origin": "https://malicious.example",
            },
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one%3A%3Aclaude", "{}", {
                "Content-Type": "application/json",
                "X-Review-Token": "wrong",
            },
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST", "/api/reviews/report-one%3A%3Aclaude",
            json.dumps({"source_fingerprint": "old"}), {
                "Content-Type": "application/json",
                "X-Review-Token": self.server.review_token,
            },
        )
        self.assertEqual(status, 409)

    def test_source_suppression_is_bound_to_retained_author_and_reversible(self):
        status, _, body = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        item = next(
            item for item in json.loads(body)["items"]
            if item["report_id"] == "report-one")
        self.assertEqual(item["author_handle"], "course_bot")
        self.assertIsNone(item["source_suppression"])
        headers = {
            "Content-Type": "application/json",
            "X-Review-Token": self.server.review_token,
            "Origin": f"http://127.0.0.1:{self.port}",
        }
        payload = {
            "report_id": item["report_id"],
            "source_fingerprint": item["source_fingerprint"],
            "active": True,
            "reason": "marketing",
        }
        status, _, body = self.request(
            "POST", "/api/source-suppressions", json.dumps(payload), headers)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["suppression"]["active"])
        with SamplingControlStore(self.server.controls_db) as controls:
            self.assertEqual(controls.active("x")[0].author_id, "12345")

        payload["active"] = False
        status, _, body = self.request(
            "POST", "/api/source-suppressions", json.dumps(payload), headers)
        self.assertEqual(status, 200, body)
        self.assertFalse(json.loads(body)["suppression"]["active"])
        with SamplingControlStore(self.server.controls_db) as controls:
            self.assertEqual(controls.active("x"), [])


if __name__ == "__main__":
    unittest.main()
