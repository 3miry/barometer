"""Run one capped X identity sample into a private classifier-candidate DB.

This script does not render or alter public weather. It retains both relevant
reports and chatter so the classifier can be evaluated without selection bias.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from barometer.adapters import XAdapter
from barometer.sampling_controls import SamplingControlStore
from barometer.store import Store
from barometer.temporal import temporal_priority


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="observation/private/x_classifier_candidates.db")
    parser.add_argument("--daily-read-limit", type=int, default=60)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument(
        "--controls-db",
        default="observation/private/sampling_controls.db")
    args = parser.parse_args(argv)
    if args.daily_read_limit < 10:
        parser.error("--daily-read-limit must be at least 10")
    if not 1 <= args.window_days <= 7:
        parser.error("--window-days must be between 1 and 7")
    if not os.environ.get("X_BEARER_TOKEN"):
        parser.error("X_BEARER_TOKEN is required")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with SamplingControlStore(args.controls_db) as controls:
        suppressed_authors = controls.active("x")
    with Store(str(path)) as store:
        adapter = XAdapter(
            store,
            os.environ["X_BEARER_TOKEN"],
            daily_read_limit=args.daily_read_limit,
            retain_filter=lambda _text: True,
            suppressed_authors=suppressed_authors,
        )
        candidates = adapter.fetch(now - args.window_days * 86400)
        new_candidates = 0
        for batch in adapter.collection_batches:
            new_candidates += store.add_complaints(
                list(batch.complaints), batch.run, list(batch.result_ranks))
        store.prune_complaints(now - 30 * 86400)
        output = {
            "private_classifier_sample": True,
            "public_weather_updated": False,
            "unique_candidates_returned_to_sampler": len(candidates),
            "new_private_candidates": new_candidates,
            "temporal_priority": {
                band: sum(
                    temporal_priority(item.text).band == band
                    for item in candidates
                )
                for band in ("very high", "high", "medium", "undated")
            },
            "errors": adapter.errors,
            "usage": adapter.usage_report(),
        }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
