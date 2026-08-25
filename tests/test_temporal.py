"""Temporal review-priority tests."""
import unittest

from barometer.temporal import temporal_priority


class TemporalPriorityTests(unittest.TestCase):
    def test_combines_current_and_change_cues(self):
        priority = temporal_priority(
            "First time today I've seen Sol suddenly collapse in reasoning.")
        self.assertEqual(priority.score, 6)
        self.assertEqual(priority.band, "high")
        self.assertEqual(priority.cues, ("first time", "today"))

    def test_recent_update_and_change_language_combine(self):
        priority = temporal_priority(
            "Since the update, Opus has become much less warm recently.")
        self.assertEqual(priority.score, 5)
        self.assertEqual(priority.band, "high")
        self.assertEqual(priority.cues, ("since the update", "has become"))

    def test_undated_report_is_retained_at_zero(self):
        priority = temporal_priority("Opus 5 is a spiky model.")
        self.assertEqual(priority.score, 0)
        self.assertEqual(priority.band, "undated")
        self.assertEqual(priority.cues, ())

    def test_generic_comparison_is_not_mistaken_for_temporal_change(self):
        priority = temporal_priority("Gemini is better than ChatGPT at this.")
        self.assertEqual(priority.score, 0)


if __name__ == "__main__":
    unittest.main()
