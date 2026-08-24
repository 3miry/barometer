"""Run the provisional classifier without changing stored or public data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from barometer.shadow import DEFAULT_FIXTURE, evaluate_fixture, shadow_database


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only shadow evaluation for classifier v1")
    parser.add_argument(
        "--fixture", default=str(DEFAULT_FIXTURE),
        help="synthetic development-contract fixture")
    parser.add_argument(
        "--db", help="optional retained SQLite database, opened read-only")
    args = parser.parse_args()

    output = {"fixture": evaluate_fixture(args.fixture)}
    if args.db:
        database = Path(args.db)
        if not database.is_file():
            parser.error(f"database does not exist: {database}")
        output["retained_data"] = shadow_database(database)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
