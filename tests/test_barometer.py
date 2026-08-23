"""Torture suite: an instrument that cries wolf is worse than no instrument.
Named test data per house tradition."""
import random
import unittest
from barometer import (Complaint, CanaryReading, ProviderEvent,
                       detect_bursts, cascade_clusters, independence_score,
                       canary_drift, classify_tier, classify)

HOUR = 3600
DAY = 24 * HOUR
T0 = 1_780_000_000  # arbitrary epoch anchor

def quiet_fortnight(model="heron", rate_per_day=6, seed=42):
    """Background grumble: independent, low-rate, multi-source."""
    rng = random.Random(seed)
    srcs = ["reddit", "x", "hn", "discord"]
    out = []
    for d in range(14):
        for _ in range(rate_per_day):
            out.append(Complaint(
                ts=T0 + d*DAY + rng.uniform(0, DAY),
                source=rng.choice(srcs), model=model,
                text=f"model felt a bit off today honestly {rng.random():.6f} "
                     f"unique grumble about {rng.choice(['maths','tone','code','poems'])}"))
    return out

class CascadeTests(unittest.TestCase):
    def test_viral_post_counts_once(self):
        viral = "is it just me or did heron get incredibly dumb overnight??"
        cs = [Complaint(T0+i*60, s, "heron", viral, seed_url="x.com/viral/1")
              for i, s in enumerate(["x", "reddit", "hn", "discord"]*5)]
        clusters = cascade_clusters(cs)
        self.assertEqual(len(clusters), 1, "one viral seed must be ONE datum")
        self.assertEqual(len(independence_score(cs)), 1)

    def test_distinct_grumbles_stay_distinct(self):
        cs = quiet_fortnight()[:20]
        self.assertGreater(len(cascade_clusters(cs)), 15)

class BurstTests(unittest.TestCase):
    def test_quiet_weather_no_alarm(self):
        bursts = detect_bursts(quiet_fortnight(), events=[])
        self.assertEqual(bursts, [], "baseline grumble must not alarm")

    def test_cascade_alone_does_not_earn_tier1(self):
        cs = quiet_fortnight()
        viral = "heron is nerfed into the ground, unusable, spread the word"
        burst_t = T0 + 13*DAY
        cs += [Complaint(burst_t + i*300, s, "heron", viral, seed_url="x.com/nerf/9")
               for i, s in enumerate(["x","reddit","hn","discord","x","x","reddit","hn"])]
        bursts = detect_bursts(cs, events=[])
        # dedup collapses the cascade to one cluster -> too few events to burst
        self.assertEqual(bursts, [], "a cascade must not manufacture a burst")

    def test_genuine_multisource_burst_is_tier1(self):
        cs = quiet_fortnight()
        burst_t = T0 + 13*DAY
        rng = random.Random(7)
        for i in range(14):
            cs.append(Complaint(
                ts=burst_t + rng.uniform(0, 3*HOUR),
                source=["x","reddit","hn","discord"][i % 4], model="heron",
                text=f"independent report {i}: responses suddenly much shorter and "
                     f"lazier since this morning, distinct wording {rng.random():.6f}"))
        bursts = detect_bursts(cs, events=[])
        self.assertEqual(len(bursts), 1)
        a = classify_tier(bursts[0], readings=[], events=[])
        self.assertEqual(a.tier, 1)
        self.assertGreaterEqual(bursts[0].independent_sources, 2)

    def test_release_day_needs_double_evidence(self):
        cs = quiet_fortnight()
        burst_t = T0 + 13*DAY
        rng = random.Random(11)
        moans = ["heron keeps forgetting my instructions since this morning",
                 "anyone else finding heron weirdly terse right now",
                 "code answers from heron missing imports today, unusual",
                 "heron hallucinating citations again, three in a row",
                 "response quality tanked for me on long prompts"]
        for i, moan in enumerate(moans):   # moderate spike, one tight cluster
            cs.append(Complaint(burst_t + i * 60,
                                ["x","reddit"][i % 2], "heron", moan))
        release = [ProviderEvent(burst_t - 6*HOUR, "heron", "release", "heron-2 launch")]
        without = detect_bursts(cs, events=[])
        with_rel = detect_bursts(cs, events=release)
        self.assertEqual(len(without), 1, "moderate spike alarms on an ordinary day")
        self.assertEqual(with_rel, [], "same spike near a release is expected weather")

class TierTests(unittest.TestCase):
    def _burst(self):
        cs = quiet_fortnight()
        burst_t = T0 + 13*DAY
        rng = random.Random(5)
        for i in range(14):
            cs.append(Complaint(burst_t + rng.uniform(0, 3*HOUR),
                                ["x","reddit","hn"][i % 3], "heron",
                                f"quality clearly degraded, again distinct {i} {rng.random():.6f}"))
        return detect_bursts(cs, events=[])[0], burst_t

    def test_canary_drift_upgrades_to_tier2(self):
        burst, t = self._burst()
        pre  = CanaryReading(t - DAY, "heron", [-1.2, -0.8, -2.1, -0.4, -1.7])
        post = CanaryReading(t + HOUR, "heron", [-1.9, -0.2, -2.9, -1.1, -0.9])
        self.assertGreater(canary_drift(pre, post), 0.15)
        a = classify_tier(burst, [pre, post], events=[])
        self.assertEqual(a.tier, 2)

    def test_stable_canary_stays_tier1(self):
        burst, t = self._burst()
        pre  = CanaryReading(t - DAY, "heron", [-1.2, -0.8, -2.1, -0.4, -1.7])
        post = CanaryReading(t + HOUR, "heron", [-1.21, -0.79, -2.12, -0.41, -1.69])
        a = classify_tier(burst, [pre, post], events=[])
        self.assertEqual(a.tier, 1, "perception without corroboration stays T1")

    def test_fingerprint_change_or_ack_is_tier3(self):
        burst, t = self._burst()
        pre  = CanaryReading(t - DAY, "heron", [-1.2]*5, fingerprint="fp_alpha")
        post = CanaryReading(t + HOUR, "heron", [-1.2]*5, fingerprint="fp_beta")
        a = classify_tier(burst, [pre, post], events=[])
        self.assertEqual(a.tier, 3)
        ack = [ProviderEvent(t + DAY, "heron", "acknowledgment", "serving bug confirmed")]
        b = classify_tier(burst, [], events=ack)
        self.assertEqual(b.tier, 3)

class TaxonomyTests(unittest.TestCase):
    def test_classification(self):
        self.assertIn("lazy", classify("it truncated half the answer, so lazy now"))
        self.assertIn("sluggish", classify("latency is awful, taking forever"))
        self.assertEqual(classify("I love this model actually"), ["other"])

if __name__ == "__main__":
    unittest.main()
