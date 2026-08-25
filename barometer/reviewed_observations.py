"""Privacy-preserving bridge from human review into aggregate weather.

The source and review databases are opened read-only. Raw text, URLs, handles,
review notes, and source identifiers never become public report fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import MODEL_CATALOG
from .detect import Complaint
from .review_app import load_source_reports
from .reviews import load_review_decisions_read_only, source_fingerprint
from .vocabulary import concepts_by_id, validate_coded_observations


REVIEWED_STATUSES = frozenset(("approved", "corrected"))


@dataclass(frozen=True)
class PromotionResult:
    complaints: tuple[Complaint, ...]
    total_decisions: int
    promoted_slices: int
    promoted_observations: int
    provisional_observations_included: int
    withheld_unpublishable_observations: int
    stale_decisions: int
    invalid_decisions: int
    ignored_decisions: int
    outside_window: int

    def public_report(self) -> dict:
        """Operational counts only; contains no private source information."""
        return {
            "total_decisions": self.total_decisions,
            "promoted_slices": self.promoted_slices,
            "promoted_observations": self.promoted_observations,
            "provisional_observations_included": (
                self.provisional_observations_included),
            "withheld_unpublishable_observations": (
                self.withheld_unpublishable_observations),
            "stale_decisions": self.stale_decisions,
            "invalid_decisions": self.invalid_decisions,
            "ignored_decisions": self.ignored_decisions,
            "outside_window": self.outside_window,
        }


def _valid_variant(family: str, variant: str | None) -> bool:
    if variant is None:
        return True
    return any(
        item["key"] == variant
        for item in MODEL_CATALOG[family].get("tracked_variants", ())
    )


def _unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value is not None))


def load_reviewed_observations(
    source_db: str | Path,
    review_db: str | Path,
    *,
    since: float = 0.0,
    include_provisional: bool = False,
) -> PromotionResult:
    """Revalidate completed human decisions and return aggregate-only reports.

    Production/default promotion requires an active, publishable concept.
    ``include_provisional`` is an explicit local-preview gate and never changes
    vocabulary governance state.
    """
    source_rows = {
        row["id"]: row for row in load_source_reports(source_db)
    }
    decisions = load_review_decisions_read_only(review_db)
    concepts = concepts_by_id()

    complaints: list[Complaint] = []
    promoted_observations = 0
    provisional_included = 0
    withheld = 0
    stale = 0
    invalid = 0
    ignored = 0
    outside_window = 0

    for decision in decisions.values():
        if decision.get("status") not in REVIEWED_STATUSES:
            ignored += 1
            continue
        row = source_rows.get(decision.get("source_report_id"))
        if row is None:
            stale += 1
            continue
        expected_fingerprint = source_fingerprint(row["id"], row["text"])
        if decision.get("source_fingerprint") != expected_fingerprint:
            stale += 1
            continue
        if float(row["ts"]) < since:
            outside_window += 1
            continue
        family = decision.get("target_family")
        variant = decision.get("target_variant")
        if family not in MODEL_CATALOG or not _valid_variant(family, variant):
            invalid += 1
            continue
        try:
            observations = validate_coded_observations(
                decision.get("observations", []))
        except (TypeError, ValueError, KeyError):
            invalid += 1
            continue
        if not observations:
            ignored += 1
            continue

        allowed = []
        for observation in observations:
            concept = concepts[observation.concept_id]
            production_ready = (
                concept.status == "active" and concept.publishable)
            preview_ready = (
                include_provisional and concept.status == "provisional")
            if production_ready or preview_ready:
                allowed.append((observation, concept))
                if preview_ready:
                    provisional_included += 1
            else:
                withheld += 1
        if not allowed:
            continue

        complaints.append(Complaint(
            ts=float(row["ts"]),
            source=str(row["source"]).split("/", 1)[0],
            model=family,
            text="Human-reviewed structured observation.",
            variant=variant,
            governed_themes=_unique(
                concept.public_label for _, concept in allowed),
            governed_valences=_unique(
                observation.valence for observation, _ in allowed),
            governed_changes=_unique(
                observation.change or observation.event_state
                for observation, _ in allowed),
            dedup_key=str(row["id"]),
        ))
        promoted_observations += len(allowed)

    return PromotionResult(
        complaints=tuple(complaints),
        total_decisions=len(decisions),
        promoted_slices=len(complaints),
        promoted_observations=promoted_observations,
        provisional_observations_included=provisional_included,
        withheld_unpublishable_observations=withheld,
        stale_decisions=stale,
        invalid_decisions=invalid,
        ignored_decisions=ignored,
        outside_window=outside_window,
    )
