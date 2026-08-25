"""Compare frozen private predictions with completed human review labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from barometer.blind_evaluation import evaluate_blind_predictions


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predictions",
        default="observation/private/x_classifier_predictions.json")
    parser.add_argument(
        "--review-db",
        default="observation/private/x_classifier_reviews.db")
    parser.add_argument(
        "--output",
        help="optional private JSON output path; stdout is always aggregate")
    return parser.parse_args(argv)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False,
        prefix=path.name + ".", suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv=None) -> None:
    args = parse_args(argv)
    result = evaluate_blind_predictions(args.predictions, args.review_db)
    if args.output:
        _atomic_json(Path(args.output), result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
