"""User reports stay private, bounded, and separate from weather detection."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from barometer.cli import tick
from barometer.store import Store
from barometer.submissions import (
    DuplicateSubmission, SubmissionError, SubmissionStore, author_token,
    validate_submission,
)
from serve_barometer import BarometerHandler, BarometerServer


VALID = {
    "family": "claude",
    "model_name": "Claude Opus 5",
    "category": "quality",
    "surface": "web",
    "timing": "today",
    "description": "Responses became much shorter across unrelated tasks.",
    "consent": True,
    "website": "",
}


class ValidationTests(unittest.TestCase):
    def test_valid_report_gets_explicit_variant(self):
        report = validate_submission(VALID, now=1234.0)
        self.assertEqual(report.family, "claude")
        self.assertEqual(report.variant, "claude-opus-5")
        self.assertEqual(report.status, "pending")
        self.assertEqual(report.created_at, 1234.0)

    def test_consent_honeypot_and_allowlists_are_enforced(self):
        for patch in (
            {"consent": False},
            {"website": "spam.example"},
            {"family": "unknown"},
            {"category": "catastrophic vibes"},
            {"surface": "telepathy"},
        ):
            with self.subTest(patch=patch), self.assertRaises(SubmissionError):
                validate_submission({**VALID, **patch})

    def test_description_is_bounded(self):
        with self.assertRaises(SubmissionError):
            validate_submission({**VALID, "description": "x" * 601})


class SubmissionStoreTests(unittest.TestCase):
    def test_queue_deduplicates_and_requires_moderation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            report = validate_submission(VALID, now=2000.0)
            with SubmissionStore(path) as store:
                self.assertEqual(store.add(report), report.id)
                with self.assertRaises(DuplicateSubmission):
                    store.add(validate_submission(VALID, now=2100.0))
                listing = store.list()
                self.assertEqual(len(listing), 1)
                self.assertNotIn("description", listing[0])
                self.assertTrue(store.moderate(report.id, "approved", "reviewed"))
                self.assertEqual(store.get(report.id)["status"], "approved")

    def test_approved_bridge_excludes_raw_text_and_rejected_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            approved = validate_submission(
                {**VALID, "description": "PRIVATE APPROVED WORDS"}, now=2000.0,
            )
            rejected = validate_submission(
                {**VALID, "category": "sluggish",
                 "description": "PRIVATE REJECTED WORDS"}, now=2001.0,
            )
            with SubmissionStore(path) as store:
                store.add(approved)
                store.add(rejected)
                store.moderate(approved.id, "approved")
                store.moderate(rejected.id, "rejected")
                observations = store.approved_complaints(since=1900.0)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].source, "user")
            self.assertEqual(observations[0].variant, "claude-opus-5")
            self.assertIn("quality dropped", observations[0].text)
            self.assertNotIn("PRIVATE", observations[0].text)

    def test_rate_limit_ledger_is_durable_and_contains_no_address(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            token = author_token(b"test-secret", "2001:0db8::1")
            self.assertEqual(token, author_token(b"test-secret", "2001:db8:0::1"))
            self.assertNotIn("2001", token)
            with SubmissionStore(path) as store:
                self.assertEqual(
                    store.consume_attempt(token, now=1000.0, limits=((2, 60),)),
                    (True, 0),
                )
                self.assertEqual(
                    store.consume_attempt(token, now=1010.0, limits=((2, 60),)),
                    (True, 0),
                )
            with SubmissionStore(path) as store:
                allowed, retry_after = store.consume_attempt(
                    token, now=1020.0, limits=((2, 60),),
                )
                self.assertFalse(allowed)
                self.assertEqual(retry_after, 40)

    def test_approved_observation_reaches_aggregate_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            submission_path = os.path.join(directory, "reports.db")
            observed_at = 1_700_000_000.0
            report = validate_submission(
                {**VALID, "description": "NEVER PUBLISH THIS"}, now=observed_at,
            )
            with SubmissionStore(submission_path) as submissions:
                submissions.add(report)
                submissions.moderate(report.id, "approved")
                approved = submissions.approved_complaints()
            public = os.path.join(directory, "public")
            snapshot = os.path.join(public, "summary.json")
            with Store(os.path.join(directory, "barometer.db")) as store:
                result = tick(
                    store, [], None, out_dir=public, now=observed_at + 100,
                    public_snapshot=snapshot,
                    approved_user_reports=approved,
                )
            self.assertEqual(result["approved_user_reports"], 1)
            with open(snapshot, encoding="utf-8") as handle:
                public_text = handle.read()
            self.assertNotIn("NEVER PUBLISH THIS", public_text)
            payload = json.loads(public_text)
            self.assertEqual(payload["models"]["claude"]["sources"], {"user": 1})
            self.assertEqual(
                payload["models"]["claude"]["categories"], {"quality": 1},
            )

    def test_prune_removes_expired_raw_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reports.db")
            with SubmissionStore(path) as store:
                store.add(validate_submission(VALID, now=1000.0))
                self.assertEqual(store.prune(1001.0), 1)
                self.assertEqual(store.list(), [])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        public = Path(self.temp.name) / "public"
        public.mkdir()
        (public / "index.html").write_text("hello barometer", encoding="utf-8")
        self.db_path = str(Path(self.temp.name) / "private" / "reports.db")
        self.server = BarometerServer(
            ("127.0.0.1", 0), BarometerHandler, str(public), self.db_path,
            rate_limit_secret=b"test-rate-limit-secret",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.headers), payload
        connection.close()
        return result

    def test_static_site_and_report_endpoint(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"hello barometer", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        encoded = json.dumps(VALID).encode("utf-8")
        status, _, body = self.request(
            "POST", "/api/reports", encoded,
            {
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        self.assertEqual(status, 201)
        response = json.loads(body)
        self.assertEqual(response["status"], "pending")
        with SubmissionStore(self.db_path) as store:
            stored = store.get(response["id"])
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["variant"], "claude-opus-5")

    def test_cross_origin_post_is_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/reports", json.dumps(VALID),
            {
                "Content-Type": "application/json",
                "Origin": "https://malicious.example",
            },
        )
        self.assertEqual(status, 403)

    def test_rejected_attempts_are_durably_rate_limited_without_raw_ip(self):
        headers = {"Content-Type": "application/json"}
        for _ in range(3):
            status, _, _ = self.request("POST", "/api/reports", "{bad", headers)
            self.assertEqual(status, 400)
        status, response_headers, body = self.request(
            "POST", "/api/reports", "{bad", headers,
        )
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", response_headers)
        self.assertIn("too many reports", json.loads(body)["error"])
        db = sqlite3.connect(self.db_path)
        try:
            tokens = [row[0] for row in db.execute(
                "SELECT author_token FROM submission_attempts"
            )]
        finally:
            db.close()
        self.assertEqual(len(tokens), 3)
        self.assertTrue(all("127.0.0.1" not in token for token in tokens))

    def test_server_retention_pass_removes_expired_reports(self):
        old = validate_submission(VALID, now=1000.0)
        with SubmissionStore(self.db_path) as store:
            store.add(old)
        self.server._prune_expired(now=1000.0 + 31 * 86400)
        with SubmissionStore(self.db_path) as store:
            self.assertIsNone(store.get(old.id))


if __name__ == "__main__":
    unittest.main()
