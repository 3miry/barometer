"""User reports stay private, bounded, and separate from weather detection."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from barometer.submissions import (
    DuplicateSubmission, SubmissionError, SubmissionStore, validate_submission,
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
            ("127.0.0.1", 0), BarometerHandler, str(public), self.db_path)
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

    def test_server_retention_pass_removes_expired_reports(self):
        old = validate_submission(VALID, now=1000.0)
        with SubmissionStore(self.db_path) as store:
            store.add(old)
        self.server._prune_expired(now=1000.0 + 31 * 86400)
        with SubmissionStore(self.db_path) as store:
            self.assertIsNone(store.get(old.id))


if __name__ == "__main__":
    unittest.main()
