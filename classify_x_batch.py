"""Freeze blind OpenRouter predictions for the private X review batch."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from barometer.adjudicator import (
    ADJUDICATOR_CONTRACT_VERSION,
    adjudicate_target,
    build_adjudication_request,
)
from barometer.openrouter_classifier import (
    DEFAULT_CLASSIFIER_MODEL,
    OpenRouterTransport,
)
from barometer.review_app import build_review_items
from barometer.reviews import load_review_decisions_read_only


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-db",
        default="observation/private/x_classifier_candidates.db")
    parser.add_argument(
        "--review-db",
        default="observation/private/x_classifier_reviews.db")
    parser.add_argument(
        "--output",
        default="observation/private/x_classifier_predictions.json")
    parser.add_argument("--model", default=DEFAULT_CLASSIFIER_MODEL)
    parser.add_argument("--max-cost-usd", type=float, default=0.75)
    parser.add_argument(
        "--execute", action="store_true",
        help="make paid model calls; without this flag, print a dry-run estimate")
    args = parser.parse_args(argv)
    if not 0 < args.max_cost_usd <= 5:
        parser.error("--max-cost-usd must be greater than 0 and no more than 5")
    if args.execute and not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("OPENROUTER_API_KEY is required with --execute")
    return args


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


def _empty_output(model: str) -> dict:
    return {
        "schema_version": 1,
        "evaluation_kind": "blind_held_out_predictions",
        "adjudicator_contract_version": ADJUDICATOR_CONTRACT_VERSION,
        "model": model,
        "predictions": {},
        "failures": {},
        "usage": {},
    }


def main(argv=None) -> None:
    args = parse_args(argv)
    source = Path(args.source_db)
    review = Path(args.review_db)
    output_path = Path(args.output)
    if not source.exists():
        raise SystemExit(f"source database does not exist: {source}")
    reviewed = load_review_decisions_read_only(review)
    if reviewed:
        raise SystemExit(
            "blind prediction refused: this batch already has human decisions")
    items = build_review_items(source, review, decisions={})
    transport = OpenRouterTransport(
        os.environ.get("OPENROUTER_API_KEY") or "dry-run-placeholder",
        model=args.model,
    )
    requests = [
        build_adjudication_request(
            item["text"], item["seed_family"], item["seed_variant"])
        for item in items
    ]
    if output_path.exists():
        with output_path.open(encoding="utf-8") as handle:
            output = json.load(handle)
        if output.get("model") != args.model:
            raise SystemExit("existing predictions use a different model")
    else:
        output = _empty_output(args.model)
    completed = set(output["predictions"]) | set(output["failures"])
    pending = [
        request for item, request in zip(items, requests)
        if item["review_unit_id"] not in completed
    ]
    projected = sum(
        transport.estimated_cost_upper_bound(request) for request in pending)
    previous_cost = float(
        (output.get("usage") or {}).get("reported_cost_usd") or 0)
    summary = {
        "model": args.model,
        "target_slices": len(items),
        "pending_target_slices": len(pending),
        "previous_reported_cost_usd": previous_cost,
        "projected_remaining_cost_upper_bound_usd": round(projected, 4),
        "projected_total_cost_ceiling_usd": round(previous_cost + projected, 4),
        "local_cost_ceiling_usd": args.max_cost_usd,
        "execute": args.execute,
        "human_decisions_present": False,
    }
    if previous_cost + projected > args.max_cost_usd:
        raise SystemExit(json.dumps({
            **summary,
            "refused": "projected upper bound exceeds local cost ceiling",
        }, indent=2, sort_keys=True))
    if not args.execute:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    for item, request in zip(items, requests):
        unit_id = item["review_unit_id"]
        if unit_id in output["predictions"] or unit_id in output["failures"]:
            continue
        if transport.reported_cost_usd >= args.max_cost_usd:
            raise SystemExit("local cost ceiling reached before batch completion")
        try:
            result = adjudicate_target(
                item["text"], item["seed_family"], item["seed_variant"],
                transport=transport,
            )
        except Exception as exc:
            output["failures"][unit_id] = {
                "source_report_id": item["report_id"],
                "source_fingerprint": item["source_fingerprint"],
                "target_family": item["seed_family"],
                "target_variant": item["seed_variant"],
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
                "usage": dict(transport.last_usage),
            }
        else:
            classification = result.classification.as_dict()
            if classification["abstention_reason"] is not None:
                # A model may quote source language in a free-text rationale.
                # Persist only the fact of abstention; source text stays in the
                # separately retained candidate database.
                classification["abstention_reason"] = "model_abstained"
            output["predictions"][unit_id] = {
                "source_report_id": item["report_id"],
                "source_fingerprint": item["source_fingerprint"],
                "target_family": result.target_family,
                "target_variant": result.target_variant,
                "target_supported": result.target_supported,
                "classification": classification,
                "usage": dict(transport.last_usage),
            }
        output["usage"] = transport.usage_summary()
        _atomic_json(output_path, output)

    print(json.dumps({
        **summary,
        "predictions": len(output["predictions"]),
        "failures": len(output["failures"]),
        "usage": output["usage"],
        "raw_report_text_persisted_in_predictions": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
