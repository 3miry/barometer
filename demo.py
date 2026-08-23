"""Demo: a fortnight of ordinary weather, then a real event — rendered."""
import random
from barometer import *
from barometer.detect import HOUR, Complaint, CanaryReading, ProviderEvent
from tests.test_barometer import quiet_fortnight, T0, DAY

cs = quiet_fortnight(rate_per_day=7, seed=3)
burst_t = T0 + 13*DAY
rng = random.Random(9)
voices = ["heron dropping whole paragraphs mid-answer since ~9am",
          "is heron quantized?? my perplexity canary just jumped",
          "long-context recall notably worse today on heron",
          "heron rewriting my code in a different style suddenly",
          "getting bizarre tone shifts from heron this afternoon",
          "heron eval suite at work dipped 4 points overnight",
          "my heron agent started timing out on tool calls",
          "answers half the usual length, anyone else",
          "heron misreading tables it handled fine last week",
          "something is off with heron, subtle but consistent",
          "heron forgot formatting rules it always followed",
          "regression on my heron regression tests, ironically"]
for i, v in enumerate(voices):
    cs.append(Complaint(burst_t + rng.uniform(0, 3*HOUR),
                        ["x","reddit","hn","discord"][i % 4], "heron", v))
pre  = CanaryReading(burst_t - DAY, "heron", [-1.3,-0.7,-2.2,-0.5,-1.6,-0.9], "fp_2026_07a")
post = CanaryReading(burst_t + HOUR, "heron", [-1.9,-0.3,-2.8,-1.2,-1.0,-1.5], "fp_2026_07a")
events = []
bursts = detect_bursts(cs, events)
assessments = [classify_tier(b, [pre, post], events) for b in bursts]
render_dashboard("heron", cs, assessments, "/mnt/user-data/outputs/barometer_demo.html")
for a in assessments: print(a.summary, "| drift:", round(a.drift,3))
