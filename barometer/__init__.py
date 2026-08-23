"""The Barometer — a weather service for model behaviour.

Detects CORROBORATED ANOMALY vs PERCEIVED WEATHER in deployed AI models.
Says "something changed", never "they nerfed it": this instrument cannot
distinguish weights from serving config from product-layer changes, and
pretending otherwise would make it a harassment engine rather than an
accountability instrument.

Ethics, load-bearing:
- No capability-quizzing of models (no-specimens rule). The only model
  contact is a fixed benign canary text scored for its logprob
  DISTRIBUTION SHAPE - a pulse taken at the wrist, not an exam.
- Social ingestion is public posts only, aggregated, never individual
  surveillance.
- Tiered claims with the evidence shown. Confidence, not verdicts.
"""
from .detect import (Complaint, CanaryReading, ProviderEvent,
                     detect_bursts, cascade_clusters, independence_score,
                     canary_drift, classify_tier, classify, Burst, Assessment)
from .dashboard import render_dashboard
