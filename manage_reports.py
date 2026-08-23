"""Review Barometer's private pending user-report queue."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time

from barometer.submissions import SubmissionStore


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="observation/private/user_reports.db")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--status", default="pending",
                         choices=("pending", "approved", "rejected"))
    listing.add_argument("--limit", type=int, default=50)
    show = commands.add_parser("show")
    show.add_argument("report_id")
    for name in ("approve", "reject"):
        moderation = commands.add_parser(name)
        moderation.add_argument("report_id")
        moderation.add_argument("--note", default="")
    prune = commands.add_parser("prune")
    prune.add_argument("--days", type=int, default=30)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    with SubmissionStore(args.db) as store:
        if args.command == "list":
            rows = store.list(args.status, args.limit)
            for row in rows:
                row["created_at"] = datetime.fromtimestamp(
                    row["created_at"], tz=timezone.utc).isoformat()
            print(json.dumps(rows, indent=2))
        elif args.command == "show":
            report = store.get(args.report_id)
            if report is None:
                raise SystemExit("report not found")
            print(json.dumps(report, indent=2))
        elif args.command in {"approve", "reject"}:
            status = "approved" if args.command == "approve" else "rejected"
            if not store.moderate(args.report_id, status, args.note):
                raise SystemExit("pending report not found")
            print(f"{args.report_id}: {status}")
        elif args.command == "prune":
            if args.days < 1:
                raise SystemExit("--days must be at least 1")
            count = store.prune(time.time() - args.days * 86400)
            print(f"pruned {count} report(s)")


if __name__ == "__main__":
    main()
