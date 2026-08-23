"""Pipeline torture: taps parse fixtures, the store deduplicates, the canary
refuses to over-sample, and one tick produces weather. No network anywhere."""
import json
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
import run_barometer
from barometer.store import Store
from barometer.adapters import (RedditAdapter, HNAdapter, XAdapter,
                                reddit_token_transport, route_model)
from barometer.canary import CanaryRunner, BudgetRefusal, CANARY_TEXT
from barometer.catalog import infer_variant
from barometer.cli import tick
from barometer.dashboard import render_landing
from barometer.detect import Complaint, ProviderEvent

NOW = 1_781_300_000.0

REDDIT_FIXTURE = {"data": {"children": [
    {"data": {"id": "abc1", "created_utc": NOW - 3600,
              "title": "Is Claude worse today or is it just me",
              "selftext": "responses feel degraded since this morning",
              "permalink": "/r/testsub/comments/abc1/",
              "url_overridden_by_dest": None}},
    {"data": {"id": "abc2", "created_utc": NOW - 3000,
              "title": "chatgpt suddenly so lazy",
              "selftext": "truncating everything, anyone else",
              "permalink": "/r/testsub/comments/abc2/",
              "url_overridden_by_dest": "https://x.com/viral/1"}},
    {"data": {"id": "abc3", "created_utc": NOW - 2000,
              "title": "I love ducks",             # not a complaint, not a model
              "selftext": "ducks are great",
              "permalink": "/r/testsub/comments/abc3/",
              "url_overridden_by_dest": None}},
]}}

HN_FIXTURE = {"hits": [
    {"objectID": "1", "created_at_i": int(NOW - 5000),
     "title": "Claude quality dropped after the update?", "url": None},
    {"objectID": "2", "created_at_i": int(NOW - 4000),
     "comment_text": "gemini has been weirdly slow and worse all week",
     "url": "https://example.com/thread"},
]}


def x_created_at(ts):
    return datetime.fromtimestamp(
        ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


X_POSTS = {
    "claude": {
        "id": "1900000000000000001",
        "created_at": x_created_at(NOW - 1800),
        "text": "Claude is suddenly worse and lazy today, anyone else?",
        "entities": {"urls": [{"expanded_url": "https://example.com/seed"}]},
    },
    "gpt": {
        "id": "1900000000000000002",
        "created_at": x_created_at(NOW - 1700),
        "text": "ChatGPT is delightful today",  # model, but not a complaint
    },
    "gemini": {
        "id": "1900000000000000003",
        "created_at": x_created_at(NOW - 1600),
        "text": "Gemini feels degraded and slow since the update",
    },
}

REDDIT_USER_AGENT = "windows:barometer:v0.2 (by /u/test_operator)"


def reddit_test_adapter(listing_transport=None):
    listing_transport = listing_transport or (
        lambda url, token, user_agent: (
            REDDIT_FIXTURE,
            {"x-ratelimit-used": "1", "x-ratelimit-remaining": "99",
             "x-ratelimit-reset": "42"},
        )
    )
    return RedditAdapter(
        ["testsub"],
        client_id="test-client",
        client_secret="test-secret",
        user_agent=REDDIT_USER_AGENT,
        token_transport=lambda client_id, secret, user_agent: {
            "access_token": "test-access-token",
            "expires_in": 3600,
        },
        listing_transport=listing_transport,
        clock=lambda: NOW,
    )

class AdapterTests(unittest.TestCase):
    def test_variant_routing_is_explicit_only(self):
        self.assertEqual(
            infer_variant("claude", "Claude Opus 5 feels slow"),
            "claude-opus-5",
        )
        self.assertEqual(
            infer_variant("claude", "Claude 4 Opus feels slow"),
            "claude-opus-4",
        )
        self.assertEqual(
            infer_variant("gpt", "GPT-5 quality dropped"),
            "gpt-5",
        )
        self.assertEqual(
            infer_variant("gemini", "Gemini 2.5 Flash feels slow"),
            "gemini-2.5-flash",
        )
        self.assertIsNone(
            infer_variant("claude", "Claude feels slow today"),
        )

    @patch("barometer.adapters.urllib.request.urlopen")
    def test_reddit_token_exchange_uses_basic_auth(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"access_token":"short-lived-token"}'
        payload = reddit_token_transport(
            "client-id", "client-secret", REDDIT_USER_AGENT)
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://www.reddit.com/api/v1/access_token")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"grant_type=client_credentials")
        self.assertTrue(request.get_header("Authorization").startswith("Basic "))
        self.assertEqual(request.get_header("User-agent"), REDDIT_USER_AGENT)
        self.assertEqual(payload["access_token"], "short-lived-token")

    def test_reddit_rejects_anonymous_user_agent(self):
        with self.assertRaises(ValueError):
            RedditAdapter(
                ["testsub"], "test-client", "test-secret", "generic-client")

    def test_reddit_parses_and_filters(self):
        calls = []

        def listing(url, token, user_agent):
            calls.append((url, token, user_agent))
            return REDDIT_FIXTURE, {
                "x-ratelimit-used": "1.5",
                "x-ratelimit-remaining": "98.5",
                "x-ratelimit-reset": "37",
            }

        ad = reddit_test_adapter(listing)
        cs = ad.fetch(since=NOW - 86400)
        self.assertEqual(len(cs), 2)                 # duck post filtered out
        self.assertEqual({c.model for c in cs}, {"claude", "gpt"})
        self.assertEqual(cs[1].seed_url, "https://x.com/viral/1")
        self.assertEqual(calls[0][1], "test-access-token")
        self.assertEqual(calls[0][2], REDDIT_USER_AGENT)
        self.assertTrue(calls[0][0].startswith("https://oauth.reddit.com/"))
        usage = ad.usage_report()
        self.assertEqual(usage["candidate_posts"], 3)
        self.assertEqual(usage["accepted_complaints"], 2)
        self.assertEqual(usage["rate_limit_used"], 1.5)
        self.assertEqual(usage["rate_limit_remaining"], 98.5)
        self.assertFalse(usage["raw_persisted"])

    def test_hn_parses(self):
        ad = HNAdapter(transport=lambda url: HN_FIXTURE)
        cs = ad.fetch(since=NOW - 86400)
        self.assertEqual({c.model for c in cs}, {"claude", "gemini"})

    def test_routing(self):
        self.assertEqual(route_model("anthropic's sonnet is acting up"), "claude")
        self.assertIsNone(route_model("my toaster is acting up"))

    def test_x_is_capped_cursor_based_and_reports_cost(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def transport(url, token):
                self.assertEqual(token, "test-token")
                params = parse_qs(urlparse(url).query)
                calls.append(params)
                query = params["query"][0]
                model = "claude" if "Claude" in query else (
                    "gpt" if "ChatGPT" in query else "gemini")
                if "since_id" in params:
                    return {"data": []}
                return {"data": [X_POSTS[model]]}

            with Store(os.path.join(d, "b.db")) as store:
                adapter = XAdapter(
                    store,
                    "test-token",
                    daily_read_limit=60,
                    per_query_limit=20,
                    transport=transport,
                    clock=lambda: NOW,
                )
                complaints = adapter.fetch(NOW - 86400)
                self.assertEqual(
                    {c.model for c in complaints}, {"claude", "gemini"})
                self.assertEqual(
                    complaints[0].seed_url, "https://example.com/seed")
                self.assertEqual(store.tap_usage("2026-06-12", "x"), 3)
                usage = adapter.usage_report()
                self.assertEqual(usage["candidate_posts"], 3)
                self.assertEqual(usage["accepted_complaints"], 2)
                self.assertEqual(usage["estimated_cost_usd_upper_bound"], 0.015)

                # Cursors suppress old posts. Empty successful responses refund
                # their reservation, so the conservative daily ledger stays put.
                self.assertEqual(adapter.fetch(NOW - 86400), [])
                self.assertEqual(store.tap_usage("2026-06-12", "x"), 3)
                self.assertTrue(all("since_id" in p for p in calls[3:]))
                self.assertTrue(all(p["max_results"] == ["20"] for p in calls))

    def test_x_ambiguous_failure_consumes_reserved_allowance(self):
        with tempfile.TemporaryDirectory() as d:
            calls = {"count": 0}

            def timeout(url, token):
                calls["count"] += 1
                raise TimeoutError("uncertain delivery")

            with Store(os.path.join(d, "b.db")) as store:
                adapter = XAdapter(
                    store,
                    "test-token",
                    queries={"claude": "Claude worse", "gpt": "ChatGPT worse"},
                    daily_read_limit=10,
                    per_query_limit=10,
                    transport=timeout,
                    clock=lambda: NOW,
                )
                self.assertEqual(adapter.fetch(NOW - 86400), [])
                self.assertEqual(calls["count"], 1)
                self.assertEqual(store.tap_usage("2026-06-12", "x"), 10)
                self.assertTrue(adapter.usage_report()["budget_exhausted"])
                self.assertEqual(len(adapter.errors), 1)

class StoreTests(unittest.TestCase):
    def test_legacy_complaint_table_gains_variant_column(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "legacy.db")
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE complaints(id TEXT PRIMARY KEY, ts REAL, "
                "source TEXT, model TEXT, text TEXT, url TEXT, seed_url TEXT)"
            )
            db.close()
            with Store(path) as store:
                columns = {
                    row[1]
                    for row in store.db.execute("PRAGMA table_info(complaints)")
                }
            self.assertIn("variant", columns)

    def test_dedup_on_reingest(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(os.path.join(d, "b.db")) as store:
                ad = HNAdapter(transport=lambda url: HN_FIXTURE)
                first = store.add_complaints(ad.fetch(NOW - 86400))
                second = store.add_complaints(ad.fetch(NOW - 86400))
                self.assertEqual(first, 2)
                self.assertEqual(second, 0, "re-ingesting the same tap must add nothing")

class CanaryTests(unittest.TestCase):
    def test_budget_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(os.path.join(d, "b.db")) as store:
                calls = {"n": 0}
                def provider(text):
                    calls["n"] += 1
                    self.assertEqual(text, CANARY_TEXT)
                    return [-1.1, -0.9, -2.0], "fp_x"
                runner = CanaryRunner(store, {"claude": provider})
                runner.run("claude", now=NOW)
                with self.assertRaises(BudgetRefusal):
                    runner.run("claude", now=NOW + 3600)   # an hour later: refused
                runner.run("claude", now=NOW + 86401)       # a day later: fine
                self.assertEqual(calls["n"], 2, "the wrist is not a keyboard")

class TickTests(unittest.TestCase):
    def test_x_usage_is_visible_in_the_run_report(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(os.path.join(d, "b.db")) as store:
                adapter = XAdapter(
                    store,
                    "test-token",
                    queries={"claude": "Claude worse"},
                    daily_read_limit=10,
                    per_query_limit=10,
                    transport=lambda url, token: {"data": [X_POSTS["claude"]]},
                    clock=lambda: NOW,
                )
                report = tick(store, [adapter], None, out_dir=d, now=NOW)
                self.assertEqual(report["new_complaints"], 1)
                self.assertEqual(report["source_usage"]["x"][
                    "read_units_upper_bound"], 1)
                self.assertEqual(report["source_usage"]["x"][
                    "saturated_queries"], 0)

    def test_one_tick_produces_weather(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(os.path.join(d, "b.db")) as store:
                store.add_event(ProviderEvent(NOW - 86400, "claude", "release", "test"))
                adapters = [reddit_test_adapter(),
                            HNAdapter(transport=lambda u: HN_FIXTURE)]
                runner = CanaryRunner(store, {"claude": lambda t: ([-1.0, -1.2], "fp")})
                report = tick(store, adapters, runner, out_dir=d, now=NOW)
                self.assertEqual(report["new_complaints"], 2)
                self.assertEqual(report["ephemeral_complaints"], 2)
                self.assertEqual(report["readings"], 1)
                self.assertNotIn("tap_errors", report)
                self.assertTrue(os.path.exists(os.path.join(d, "barometer_claude.html")))
                self.assertTrue(all(
                    not c.source.startswith("reddit/")
                    for c in store.complaints()))
                with open(os.path.join(d, "barometer_claude.html"),
                          encoding="utf-8") as handle:
                    self.assertIn("reddit/testsub × 1", handle.read())
                # idempotence: a second tick an hour later adds nothing, breaks nothing
                report2 = tick(store, adapters, runner, out_dir=d, now=NOW + 3600)
                self.assertEqual(report2["new_complaints"], 0)
                self.assertEqual(report2["ephemeral_complaints"], 2)
                self.assertEqual(report2["readings"], 0)

    def test_dead_tap_fails_loudly_but_singly(self):
        with tempfile.TemporaryDirectory() as d:
            with Store(os.path.join(d, "b.db")) as store:
                def broken(client_id, secret, user_agent):
                    raise ConnectionError("tap rusted shut")
                reddit = RedditAdapter(
                    ["testsub"], "test-client", "test-secret",
                    REDDIT_USER_AGENT, token_transport=broken)
                adapters = [reddit,
                            HNAdapter(transport=lambda u: HN_FIXTURE)]
                report = tick(store, adapters, None, out_dir=d, now=NOW)
                self.assertEqual(len(report["tap_errors"]), 1)
                self.assertEqual(report["new_complaints"], 2,
                                 "one dead tap must not stop the others")

    def test_public_snapshot_is_aggregate_only_then_raw_data_expires(self):
        with tempfile.TemporaryDirectory() as d:
            snapshot = os.path.join(d, "public", "summary.json")
            history = os.path.join(d, "public", "history.json")
            with Store(os.path.join(d, "b.db")) as store:
                store.add_complaints([Complaint(
                    NOW - 2 * 86400,
                    "hn",
                    "claude",
                    "Claude quality dropped SECRET_RAW_WORDS",
                    "https://news.ycombinator.com/item?id=private-test",
                )])
                tick(
                    store,
                    [],
                    None,
                    out_dir=os.path.join(d, "public"),
                    now=NOW,
                    public_snapshot=snapshot,
                    public_history=history,
                )
                tick(
                    store, [], None,
                    out_dir=os.path.join(d, "public"),
                    now=NOW + 3600,
                    public_snapshot=snapshot,
                    public_history=history,
                )
                with open(history, encoding="utf-8") as handle:
                    same_day_history = json.load(handle)
                self.assertEqual(len(same_day_history["samples"]), 1)

                report = tick(
                    store, [], None,
                    out_dir=os.path.join(d, "public"),
                    now=NOW + 86400,
                    retention_days=1,
                    public_snapshot=snapshot,
                    public_history=history,
                )
                self.assertEqual(report["pruned_complaints"], 1)
                self.assertEqual(store.complaints(), [])

            with open(snapshot, encoding="utf-8") as handle:
                public_text = handle.read()
            self.assertNotIn("SECRET_RAW_WORDS", public_text)
            self.assertNotIn("private-test", public_text)
            payload = json.loads(public_text)
            self.assertEqual(payload["models"]["claude"]["reports"], 1)
            self.assertEqual(payload["models"]["claude"]["lab"], "Anthropic")
            self.assertIn(
                "Sonnet", payload["models"]["claude"]["recognised_terms"])
            self.assertEqual(
                payload["models"]["claude"]["model_breakdown"],
                [{
                    "explicit": False,
                    "key": "unspecified",
                    "label": "Unspecified Claude model",
                    "reports": 1,
                }],
            )
            with open(history, encoding="utf-8") as handle:
                public_history = handle.read()
            self.assertNotIn("SECRET_RAW_WORDS", public_history)
            self.assertNotIn("private-test", public_history)
            self.assertEqual(len(json.loads(public_history)["samples"]), 2)
            with open(os.path.join(d, "public", "barometer_claude.html"),
                      encoding="utf-8") as handle:
                public_html = handle.read()
            self.assertNotIn("SECRET_RAW_WORDS", public_html)
            self.assertNotIn("private-test", public_html)
            self.assertIn("source mix: hn × 1", public_html)
            with open(os.path.join(d, "public", "index.html"),
                      encoding="utf-8") as handle:
                landing_html = handle.read()
            self.assertNotIn("SECRET_RAW_WORDS", landing_html)
            self.assertNotIn("private-test", landing_html)
            self.assertIn("Most reported right now", landing_html)
            self.assertIn("Search by lab, family, or model", landing_html)
            with open(os.path.join(d, "public", "report.html"),
                      encoding="utf-8") as handle:
                report_form = handle.read()
            self.assertNotIn("SECRET_RAW_WORDS", report_form)
            self.assertNotIn("private-test", report_form)
            self.assertIn("Private moderation boundary", report_form)
            self.assertIn('name="model_name"', report_form)

    def test_landing_ranks_reports_and_exposes_family_filters(self):
        with tempfile.TemporaryDirectory() as d:
            models = {
                "gpt": ([Complaint(
                    NOW, "hn", "gpt", "GPT-5 quality worse PRIVATE GPT TEXT")], []),
                "claude": ([
                    Complaint(NOW - 2, "hn", "claude", "Claude Opus 5 slow PRIVATE ONE"),
                    Complaint(NOW - 1, "x", "claude", "Claude lazy PRIVATE TWO"),
                ], []),
            }
            out_path = os.path.join(d, "index.html")
            render_landing(models, out_path, generated_at=NOW, window_days=21)
            with open(out_path, encoding="utf-8") as handle:
                page = handle.read()
            self.assertLess(
                page.index('data-model="claude"'),
                page.index('data-model="gpt"'),
            )
            self.assertIn('data-lab-filter="anthropic"', page)
            self.assertIn('data-lab-filter="openai"', page)
            self.assertIn("Sonnet", page)
            self.assertIn("GPT-5", page)
            self.assertIn("Claude Opus 5", page)
            self.assertIn("Unspecified Claude model", page)
            self.assertNotIn("PRIVATE ONE", page)
            self.assertNotIn("PRIVATE GPT TEXT", page)

    def test_corrupt_public_history_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            history = os.path.join(d, "history.json")
            with open(history, "w", encoding="utf-8") as handle:
                handle.write("{broken history")
            with Store(os.path.join(d, "b.db")) as store:
                with self.assertRaises(json.JSONDecodeError):
                    tick(
                        store, [], None,
                        out_dir=d,
                        now=NOW,
                        public_snapshot=os.path.join(d, "summary.json"),
                        public_history=history,
                    )
            with open(history, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{broken history")


class RunnerSafetyTests(unittest.TestCase):
    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_default_run_is_offline_archive_render(
            self, store_factory, tick_mock, print_mock):
        store = store_factory.return_value.__enter__.return_value
        run_barometer.main([])
        tick_mock.assert_called_once()
        args, kwargs = tick_mock.call_args
        self.assertIs(args[0], store)
        self.assertEqual(args[1], [])
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["window_days"], 36500)

    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_live_features_require_flags(
            self, store_factory, tick_mock, print_mock):
        store = store_factory.return_value.__enter__.return_value
        run_barometer.main(["--hn", "--openai-canary"])
        args, kwargs = tick_mock.call_args
        self.assertIs(args[0], store)
        self.assertEqual(len(args[1]), 1)
        self.assertIsInstance(args[1][0], HNAdapter)
        self.assertIsInstance(args[2], CanaryRunner)
        self.assertEqual(kwargs["window_days"], 21)

    def test_x_requires_an_environment_credential(self):
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    run_barometer.parse_args(["--x"])

    def test_reddit_requires_all_environment_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    run_barometer.parse_args(["--reddit"])

    @patch.dict(os.environ, {
        "REDDIT_CLIENT_ID": "test-client",
        "REDDIT_CLIENT_SECRET": "test-secret",
        "REDDIT_USER_AGENT": REDDIT_USER_AGENT,
    })
    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_reddit_observation_profile_is_ephemeral(
            self, store_factory, tick_mock, print_mock):
        store = store_factory.return_value.__enter__.return_value
        tick_mock.return_value = {"new_complaints": 0}
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "private", "trial.db")
            out_dir = os.path.join(d, "public")
            run_barometer.main([
                "--observe-reddit", "--db", db_path, "--out-dir", out_dir,
            ])

        args, kwargs = tick_mock.call_args
        self.assertIs(args[0], store)
        self.assertEqual(len(args[1]), 1)
        self.assertIsInstance(args[1][0], RedditAdapter)
        self.assertTrue(args[1][0].ephemeral)
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["retention_days"], 30)

    @patch.dict(os.environ, {"X_BEARER_TOKEN": "test-token"})
    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_x_observation_profile_is_isolated_and_capped(
            self, store_factory, tick_mock, print_mock):
        store = store_factory.return_value.__enter__.return_value
        tick_mock.return_value = {"new_complaints": 0}
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "private", "trial.db")
            out_dir = os.path.join(d, "public")
            run_barometer.main([
                "--observe-x", "--x-daily-read-limit", "30",
                "--db", db_path, "--out-dir", out_dir,
            ])

        args, kwargs = tick_mock.call_args
        self.assertIs(args[0], store)
        self.assertEqual(len(args[1]), 1)
        self.assertIsInstance(args[1][0], XAdapter)
        self.assertEqual(args[1][0].daily_read_limit, 30)
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["retention_days"], 30)

    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_hn_observation_profile_is_isolated_and_retained(
            self, store_factory, tick_mock, print_mock):
        tick_mock.return_value = {"new_complaints": 0}
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "private", "trial.db")
            out_dir = os.path.join(d, "public")
            run_barometer.main([
                "--observe-hn", "--db", db_path, "--out-dir", out_dir,
            ])
            with open(os.path.join(out_dir, "status.json"),
                      encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["status"], "ok")

        store_factory.assert_called_once_with(db_path)
        args, kwargs = tick_mock.call_args
        self.assertEqual(len(args[1]), 1)
        self.assertIsInstance(args[1][0], HNAdapter)
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["retention_days"], 30)
        self.assertEqual(
            kwargs["public_snapshot"], os.path.join(out_dir, "summary.json"))

    @patch("builtins.print")
    @patch("run_barometer.tick")
    @patch("run_barometer.Store")
    def test_observation_status_reports_degraded_taps(
            self, store_factory, tick_mock, print_mock):
        tick_mock.return_value = {"tap_errors": ["HNAdapter: test failure"]}
        with tempfile.TemporaryDirectory() as d:
            out_dir = os.path.join(d, "public")
            run_barometer.main([
                "--observe-hn",
                "--db", os.path.join(d, "private", "trial.db"),
                "--out-dir", out_dir,
            ])
            with open(os.path.join(out_dir, "status.json"),
                      encoding="utf-8") as handle:
                status = json.load(handle)
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["report"]["tap_errors"],
                         ["HNAdapter: test failure"])

if __name__ == "__main__":
    unittest.main()
