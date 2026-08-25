"""OpenRouter classifier transport remains strict, private, and budgetable."""
from __future__ import annotations

import json
import unittest

from barometer.adjudicator import build_adjudication_request
from barometer.openrouter_classifier import (
    DEFAULT_CLASSIFIER_MODEL,
    OpenRouterTransport,
    build_openrouter_payload,
)


class OpenRouterClassifierTests(unittest.TestCase):
    def test_payload_enforces_schema_and_private_provider_routing(self):
        request = build_adjudication_request(
            "Opus 5 seems much warmer.", "claude", "claude-opus-5")
        payload = build_openrouter_payload(request)
        self.assertEqual(payload["model"], DEFAULT_CLASSIFIER_MODEL)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        observation = (
            payload["response_format"]["json_schema"]["schema"]
            ["properties"]["observations"]["items"])
        self.assertEqual(
            observation["properties"]["claim_status"]["enum"], ["reported"])
        self.assertEqual(payload["provider"], {
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
        })
        self.assertIn("Opus 5 seems", payload["messages"][1]["content"])

    def test_transport_does_not_put_key_in_body_and_tracks_usage(self):
        captured = {}

        def fake_post(url, headers, payload):
            captured.update(url=url, headers=headers, payload=payload)
            return {
                "choices": [{"message": {"content": json.dumps({
                    "target_supported": True,
                    "eligibility": "chatter",
                    "onset_precision": "unknown",
                    "observations": [],
                    "novelty_candidates": [],
                    "abstention_reason": "No behaviour assertion.",
                })}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "cost": 0.00032,
                },
            }

        transport = OpenRouterTransport(
            "secret-test-key", http_post=fake_post)
        request = build_adjudication_request(
            "GPT-5.6 wrote this article.", "gpt", "gpt-5.6")
        content = transport(request)
        self.assertIn('"eligibility": "chatter"', content)
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer secret-test-key")
        self.assertNotIn("secret-test-key", json.dumps(captured["payload"]))
        self.assertEqual(transport.usage_summary()["reported_cost_usd"], 0.00032)
        self.assertGreater(transport.estimated_cost_upper_bound(request), 0)


if __name__ == "__main__":
    unittest.main()
