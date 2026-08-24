"""Read-only aggregate analysis of a completed classifier review batch."""
from __future__ import annotations

import argparse
import json

from barometer.review_analysis import analyze_review_batch


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default="barometer.db")
    parser.add_argument(
        "--review-db", default="observation/private/classifier_reviews.db")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    print(json.dumps(
        analyze_review_batch(args.source_db, args.review_db),
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
