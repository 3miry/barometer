"""Create and operate one isolated, auditable X classifier batch.

Each action is explicit. ``plan`` is offline; ``collect --execute`` may spend X
credit; ``classify --execute`` may spend OpenRouter credit. Human review remains
separate so predictions are frozen before the reviewer sees the batch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

from barometer.adapters import X_QUERY_VERSION
from barometer.reviews import ReviewStore


DEFAULT_ROOT = Path("observation/private/x_batches")
BATCH_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-v[0-9]+-[0-9]{2}$")


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


def next_batch_id(root: Path, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    prefix = f"{now.date().isoformat()}-v{X_QUERY_VERSION}-"
    used = {
        int(path.name.rsplit("-", 1)[1])
        for path in root.glob(prefix + "[0-9][0-9]")
        if BATCH_ID.fullmatch(path.name)
    } if root.exists() else set()
    number = next((value for value in range(1, 100) if value not in used), None)
    if number is None:
        raise SystemExit("no batch identifier remains for this UTC day")
    return f"{prefix}{number:02d}"


def batch_paths(root: Path, batch_id: str) -> dict[str, Path]:
    if not BATCH_ID.fullmatch(batch_id):
        raise SystemExit("batch id must look like YYYY-MM-DD-vN-NN")
    directory = root / batch_id
    return {
        "directory": directory,
        "manifest": directory / "batch.json",
        "source_db": directory / "candidates.db",
        "predictions": directory / "predictions.json",
        "review_db": directory / "reviews.db",
        "evaluation": directory / "evaluation.json",
    }


def create_batch(
    root: Path,
    batch_id: str,
    daily_read_limit: int,
    max_classifier_cost_usd: float,
) -> tuple[dict, dict[str, Path]]:
    paths = batch_paths(root, batch_id)
    manifest_path = paths["manifest"]
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("batch_id") != batch_id:
            raise SystemExit("batch manifest identity does not match its directory")
        return manifest, paths
    if paths["directory"].exists() and any(paths["directory"].iterdir()):
        raise SystemExit("refusing to adopt a non-empty directory without a manifest")
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query_version": X_QUERY_VERSION,
        "daily_read_limit": daily_read_limit,
        "max_classifier_cost_usd": max_classifier_cost_usd,
        "public_weather_updated": False,
        "paths": {
            key: str(value.as_posix())
            for key, value in paths.items() if key != "directory"
        },
    }
    _atomic_json(manifest_path, manifest)
    return manifest, paths


def load_batch(root: Path, batch_id: str) -> tuple[dict, dict[str, Path]]:
    paths = batch_paths(root, batch_id)
    if not paths["manifest"].is_file():
        raise SystemExit("batch does not exist; create it with the plan action")
    with paths["manifest"].open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("batch_id") != batch_id:
        raise SystemExit("batch manifest identity does not match its directory")
    return manifest, paths


def _read_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if exists is None:
            return 0
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        connection.close()


def batch_status(manifest: dict, paths: dict[str, Path]) -> dict:
    predictions = {}
    if paths["predictions"].exists():
        with paths["predictions"].open(encoding="utf-8") as handle:
            predictions = json.load(handle)
    return {
        "batch_id": manifest["batch_id"],
        "query_version": manifest["query_version"],
        "daily_read_limit": manifest["daily_read_limit"],
        "source_reports": _read_count(paths["source_db"], "complaints"),
        "collection_runs": _read_count(paths["source_db"], "collection_runs"),
        "frozen_predictions": len(predictions.get("predictions") or {}),
        "prediction_failures": len(predictions.get("failures") or {}),
        "human_decisions": _read_count(
            paths["review_db"], "classifier_review_units"),
        "evaluation_exists": paths["evaluation"].exists(),
        "public_weather_updated": False,
    }


def ensure_empty_review_db(path: Path) -> None:
    """Create only the private schema required for a still-blind batch."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with ReviewStore(str(path)):
        pass


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("plan", "collect", "classify", "review", "evaluate", "status"))
    parser.add_argument("--batch-id")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--daily-read-limit", type=int, default=80)
    parser.add_argument("--max-classifier-cost-usd", type=float, default=0.75)
    parser.add_argument("--max-new-calls", type=int)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    args = parser.parse_args(argv)
    if args.action != "plan" and not args.batch_id:
        parser.error("--batch-id is required after planning a batch")
    if args.daily_read_limit < 10:
        parser.error("--daily-read-limit must be at least 10")
    if not 0 < args.max_classifier_cost_usd <= 5:
        parser.error("--max-classifier-cost-usd must be greater than 0 and at most 5")
    if args.max_new_calls is not None and args.max_new_calls < 1:
        parser.error("--max-new-calls must be positive")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.action == "plan":
        batch_id = args.batch_id or next_batch_id(args.root)
        manifest, paths = create_batch(
            args.root, batch_id, args.daily_read_limit,
            args.max_classifier_cost_usd,
        )
    else:
        batch_id = args.batch_id
        manifest, paths = load_batch(args.root, batch_id)
    if manifest["query_version"] != X_QUERY_VERSION:
        raise SystemExit("batch query version does not match the current collector")

    if args.action == "plan":
        print(json.dumps({
            **batch_status(manifest, paths),
            "paths": manifest["paths"],
            "next": (
                f"python run_x_batch.py collect --batch-id {batch_id} --execute"
            ),
        }, indent=2, sort_keys=True))
        return
    if args.action == "status":
        print(json.dumps(batch_status(manifest, paths), indent=2, sort_keys=True))
        return
    if args.action == "collect":
        if not args.execute:
            raise SystemExit("collection is paid; repeat with --execute")
        if batch_id[:10] != datetime.now(timezone.utc).date().isoformat():
            raise SystemExit("collection batch id must name the current UTC day")
        if not os.environ.get("X_BEARER_TOKEN"):
            raise SystemExit("X_BEARER_TOKEN is not loaded in this process")
        if (_read_count(paths["source_db"], "collection_runs") and not args.resume):
            raise SystemExit("batch already has collection runs; use --resume deliberately")
        _run([
            sys.executable, "sample_x_classifier.py",
            "--db", str(paths["source_db"]),
            "--daily-read-limit", str(manifest["daily_read_limit"]),
        ])
        return
    if args.action == "classify":
        ensure_empty_review_db(paths["review_db"])
        command = [
            sys.executable, "classify_x_batch.py",
            "--source-db", str(paths["source_db"]),
            "--review-db", str(paths["review_db"]),
            "--output", str(paths["predictions"]),
            "--max-cost-usd", str(manifest["max_classifier_cost_usd"]),
        ]
        if args.max_new_calls is not None:
            command.extend(("--max-new-calls", str(args.max_new_calls)))
        if args.retry_failures:
            command.append("--retry-failures")
        if args.execute:
            if not os.environ.get("OPENROUTER_API_KEY"):
                raise SystemExit("OPENROUTER_API_KEY is not loaded in this process")
            command.append("--execute")
        _run(command)
        return
    if args.action == "review":
        _run([
            sys.executable, "review_classifier.py",
            "--source-db", str(paths["source_db"]),
            "--review-db", str(paths["review_db"]),
            "--controls-db", "observation/private/sampling_controls.db",
            "--port", str(args.port),
        ])
        return
    _run([
        sys.executable, "evaluate_blind_predictions.py",
        "--predictions", str(paths["predictions"]),
        "--review-db", str(paths["review_db"]),
        "--output", str(paths["evaluation"]),
    ])


if __name__ == "__main__":
    main()
