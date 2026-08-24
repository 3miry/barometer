"""Offline contracts for collection provenance and governed query probes.

Nothing in this module activates a collector. Probe definitions are append-only
method records; collection runs are receipts supplied by an explicitly enabled
adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re

from .catalog import MODEL_CATALOG
from .vocabulary import concepts_by_id


PROBE_LEDGER_PATH = Path(__file__).with_name("data") / "probe_registry.ledger.json"
EVENT_ID = re.compile(r"^probe_evt_[0-9]{4}$")
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
VALID_COLLECTION_LANES = frozenset((
    "discovery", "targeted", "user_report", "legacy_unknown",
))
VALID_PROBE_STATES = frozenset((
    "proposed", "offline-tested", "pilot", "active", "paused", "retired",
))
VALID_PROBE_TRANSITIONS = {
    "proposed": frozenset(("offline-tested", "retired")),
    "offline-tested": frozenset(("pilot", "retired")),
    "pilot": frozenset(("active", "paused", "retired")),
    "active": frozenset(("paused", "retired")),
    "paused": frozenset(("pilot", "active", "retired")),
    "retired": frozenset(),
}


class ProbeRegistryError(ValueError):
    pass


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProbeRegistryError(f"{field} must be non-empty text")
    return value.strip()


def _text_list(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProbeRegistryError(f"{field} must be a list")
    result = tuple(_text(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ProbeRegistryError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True)
class ProbeDefinition:
    id: str
    definition_version: int
    source: str
    model_families: tuple[str, ...]
    exact_query: str
    intended_concept_ids: tuple[str, ...]
    known_ambiguities: tuple[str, ...]
    exclusions: tuple[str, ...]
    returned_item_cap: int
    cost_ceiling_usd: float | None
    lifecycle: str
    created_at: str


def validate_probe(raw: dict) -> ProbeDefinition:
    if not isinstance(raw, dict):
        raise ProbeRegistryError("probe must be an object")
    probe_id = _text(raw.get("id"), "probe.id")
    if not STABLE_ID.fullmatch(probe_id):
        raise ProbeRegistryError("probe.id must be a stable dotted slug")
    version = raw.get("definition_version")
    if not isinstance(version, int) or version < 1:
        raise ProbeRegistryError("probe.definition_version must be positive")
    source = _text(raw.get("source"), "probe.source")
    families = _text_list(raw.get("model_families"), "probe.model_families")
    if not families or any(item not in MODEL_CATALOG for item in families):
        raise ProbeRegistryError("probe.model_families must be tracked families")
    concept_ids = _text_list(
        raw.get("intended_concept_ids"), "probe.intended_concept_ids")
    concepts = concepts_by_id()
    if not concept_ids or any(item not in concepts for item in concept_ids):
        raise ProbeRegistryError(
            "probe.intended_concept_ids must name governed concepts")
    if any(concepts[item].status == "superseded" for item in concept_ids):
        raise ProbeRegistryError("probe cannot target a superseded concept")
    item_cap = raw.get("returned_item_cap")
    if not isinstance(item_cap, int) or item_cap < 1:
        raise ProbeRegistryError("probe.returned_item_cap must be positive")
    cost = raw.get("cost_ceiling_usd")
    if cost is not None and (
            not isinstance(cost, (int, float)) or isinstance(cost, bool)
            or cost < 0):
        raise ProbeRegistryError(
            "probe.cost_ceiling_usd must be non-negative or null")
    lifecycle = raw.get("lifecycle")
    if lifecycle not in VALID_PROBE_STATES:
        raise ProbeRegistryError("probe.lifecycle is invalid")
    if lifecycle != "proposed":
        raise ProbeRegistryError(
            "new probe versions must begin in the proposed state")
    return ProbeDefinition(
        id=probe_id,
        definition_version=version,
        source=source,
        model_families=families,
        exact_query=_text(raw.get("exact_query"), "probe.exact_query"),
        intended_concept_ids=concept_ids,
        known_ambiguities=_text_list(
            raw.get("known_ambiguities"), "probe.known_ambiguities"),
        exclusions=_text_list(raw.get("exclusions"), "probe.exclusions"),
        returned_item_cap=item_cap,
        cost_ceiling_usd=float(cost) if cost is not None else None,
        lifecycle=lifecycle,
        created_at=_text(raw.get("created_at"), "probe.created_at"),
    )


def validate_probe_ledger(payload: dict) -> tuple[ProbeDefinition, ...]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProbeRegistryError("probe ledger schema_version must be 1")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ProbeRegistryError("probe ledger events must be a list")
    event_ids: set[str] = set()
    versions: dict[tuple[str, int], ProbeDefinition] = {}
    highest_version: dict[str, int] = {}
    last_number = 0
    for event in events:
        if not isinstance(event, dict):
            raise ProbeRegistryError("probe ledger event must be an object")
        event_id = _text(event.get("event_id"), "event_id")
        if not EVENT_ID.fullmatch(event_id) or event_id in event_ids:
            raise ProbeRegistryError("probe event IDs must be unique and valid")
        number = int(event_id.rsplit("_", 1)[1])
        if number <= last_number:
            raise ProbeRegistryError("probe events must remain append-only")
        last_number = number
        event_ids.add(event_id)
        _text(event.get("recorded_at"), f"{event_id}.recorded_at")
        kind = event.get("type")
        if kind == "probe_version_created":
            probe = validate_probe(event.get("probe"))
            key = (probe.id, probe.definition_version)
            if key in versions:
                raise ProbeRegistryError("duplicate probe definition version")
            expected = highest_version.get(probe.id, 0) + 1
            if probe.definition_version != expected:
                raise ProbeRegistryError(
                    "probe definition versions must be sequential")
            versions[key] = probe
            highest_version[probe.id] = probe.definition_version
        elif kind == "probe_lifecycle_changed":
            probe_id = _text(event.get("probe_id"), f"{event_id}.probe_id")
            version = event.get("definition_version")
            key = (probe_id, version)
            if key not in versions:
                raise ProbeRegistryError("lifecycle event references unknown probe")
            current = versions[key]
            before, after = event.get("from"), event.get("to")
            if before != current.lifecycle:
                raise ProbeRegistryError("lifecycle event has stale from state")
            if after not in VALID_PROBE_TRANSITIONS[before]:
                raise ProbeRegistryError("invalid probe lifecycle transition")
            _text(event.get("rationale"), f"{event_id}.rationale")
            versions[key] = replace(current, lifecycle=after)
        else:
            raise ProbeRegistryError(f"unsupported probe event type: {kind}")
    return tuple(versions[key] for key in sorted(versions))


def load_probe_versions(
        path: str | Path = PROBE_LEDGER_PATH) -> tuple[ProbeDefinition, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_probe_ledger(json.load(handle))


def latest_probes(
        path: str | Path = PROBE_LEDGER_PATH) -> dict[str, ProbeDefinition]:
    latest: dict[str, ProbeDefinition] = {}
    for probe in load_probe_versions(path):
        if probe.definition_version > getattr(
                latest.get(probe.id), "definition_version", 0):
            latest[probe.id] = probe
    return latest


@dataclass(frozen=True)
class CollectionRun:
    run_id: str
    source: str
    lane: str
    query_id: str
    query_version: int
    started_at: float
    completed_at: float
    returned_candidates: int
    retained_candidates: int
    item_cap: int | None = None
    saturated: bool = False
    cost_units: int | None = None
    cost_usd_upper_bound: float | None = None
    frame_note: str = ""

    def __post_init__(self) -> None:
        for field in ("run_id", "source", "query_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"collection {field} must be non-empty")
        if self.lane not in VALID_COLLECTION_LANES:
            raise ValueError("invalid collection lane")
        if not isinstance(self.query_version, int) or self.query_version < 1:
            raise ValueError("collection query_version must be positive")
        if self.completed_at < self.started_at:
            raise ValueError("collection run cannot finish before it starts")
        for field in ("returned_candidates", "retained_candidates"):
            value = getattr(self, field)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"collection {field} must be non-negative")
        if self.retained_candidates > self.returned_candidates:
            raise ValueError("retained candidates cannot exceed returned candidates")
        if self.item_cap is not None and (
                not isinstance(self.item_cap, int) or self.item_cap < 1):
            raise ValueError("collection item_cap must be positive or null")
        if self.cost_units is not None and (
                not isinstance(self.cost_units, int) or self.cost_units < 0):
            raise ValueError("collection cost_units must be non-negative or null")
        if self.cost_usd_upper_bound is not None and (
                not isinstance(self.cost_usd_upper_bound, (int, float))
                or isinstance(self.cost_usd_upper_bound, bool)
                or self.cost_usd_upper_bound < 0):
            raise ValueError(
                "collection cost_usd_upper_bound must be non-negative or null")
