"""Governed vernacular search terms linked to neutral behaviour concepts.

These are LLT-like retrieval terms, not classifier labels. A match retrieves a
candidate; it never determines coding, valence, or causality by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re

from .vocabulary import concepts_by_id


TERM_LEDGER_PATH = (
    Path(__file__).with_name("data") / "search_term_registry.ledger.json")
EVENT_ID = re.compile(r"^term_evt_[0-9]{4}$")
TERM_ID = re.compile(r"^llt_[0-9]{4}$")
PHRASE = re.compile(r"^[a-z0-9][a-z0-9 '\-]{0,59}$")
VALID_TERM_STATES = frozenset((
    "proposed", "offline-tested", "pilot", "active", "paused", "retired",
))
VALID_TERM_TRANSITIONS = {
    "proposed": frozenset(("offline-tested", "retired")),
    "offline-tested": frozenset(("pilot", "retired")),
    "pilot": frozenset(("active", "paused", "retired")),
    "active": frozenset(("paused", "retired")),
    "paused": frozenset(("pilot", "active", "retired")),
    "retired": frozenset(),
}


class SearchTermError(ValueError):
    pass


def normalise_search_phrase(value: str) -> str:
    """Validate a literal phrase before it can become query material."""
    phrase = _text(value, "search term phrase", 60).casefold()
    if not PHRASE.fullmatch(phrase):
        raise SearchTermError("search term phrase contains unsafe query syntax")
    return phrase


def _text(value, field: str, maximum: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchTermError(f"{field} must be non-empty text")
    result = " ".join(value.split())
    if len(result) > maximum:
        raise SearchTermError(f"{field} is too long")
    return result


def _text_list(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SearchTermError(f"{field} must be a list")
    result = tuple(_text(item, field) for item in value)
    if len({item.casefold() for item in result}) != len(result):
        raise SearchTermError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True)
class SearchTermDefinition:
    id: str
    definition_version: int
    phrase: str
    concept_id: str
    known_ambiguities: tuple[str, ...]
    origin: str
    lifecycle: str
    created_at: str


def validate_search_term(raw: dict) -> SearchTermDefinition:
    if not isinstance(raw, dict):
        raise SearchTermError("search term must be an object")
    term_id = raw.get("id")
    if not isinstance(term_id, str) or not TERM_ID.fullmatch(term_id):
        raise SearchTermError("search term id must match llt_NNNN")
    version = raw.get("definition_version")
    if not isinstance(version, int) or version < 1:
        raise SearchTermError("search term definition_version must be positive")
    phrase = normalise_search_phrase(raw.get("phrase"))
    concept_id = raw.get("concept_id")
    concepts = concepts_by_id()
    if concept_id not in concepts:
        raise SearchTermError("search term must name a governed concept")
    if concepts[concept_id].status == "superseded":
        raise SearchTermError("search term cannot target a superseded concept")
    lifecycle = raw.get("lifecycle")
    if lifecycle != "proposed":
        raise SearchTermError("new search terms must begin proposed")
    return SearchTermDefinition(
        id=term_id,
        definition_version=version,
        phrase=phrase,
        concept_id=concept_id,
        known_ambiguities=_text_list(
            raw.get("known_ambiguities", []), "known ambiguities"),
        origin=_text(raw.get("origin"), "search term origin", 120),
        lifecycle=lifecycle,
        created_at=_text(raw.get("created_at"), "search term created_at", 40),
    )


def validate_search_term_ledger(payload: dict) -> tuple[SearchTermDefinition, ...]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SearchTermError("search term ledger schema_version must be 1")
    events = payload.get("events")
    if not isinstance(events, list):
        raise SearchTermError("search term ledger events must be a list")
    versions: dict[tuple[str, int], SearchTermDefinition] = {}
    highest: dict[str, int] = {}
    event_ids: set[str] = set()
    phrases: dict[str, str] = {}
    last_number = 0
    for event in events:
        if not isinstance(event, dict):
            raise SearchTermError("search term event must be an object")
        event_id = event.get("event_id")
        if (not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id)
                or event_id in event_ids):
            raise SearchTermError("search term event IDs must be unique and valid")
        number = int(event_id.rsplit("_", 1)[1])
        if number <= last_number:
            raise SearchTermError("search term events must remain append-only")
        last_number = number
        event_ids.add(event_id)
        _text(event.get("recorded_at"), f"{event_id}.recorded_at", 40)
        kind = event.get("type")
        if kind == "search_term_version_created":
            term = validate_search_term(event.get("term"))
            key = (term.id, term.definition_version)
            if key in versions:
                raise SearchTermError("duplicate search term version")
            expected = highest.get(term.id, 0) + 1
            if term.definition_version != expected:
                raise SearchTermError("search term versions must be sequential")
            owner = phrases.get(term.phrase)
            if owner is not None and owner != term.id:
                raise SearchTermError("a phrase cannot belong to multiple LLT IDs")
            versions[key] = term
            highest[term.id] = term.definition_version
            phrases[term.phrase] = term.id
        elif kind == "search_term_lifecycle_changed":
            key = (event.get("term_id"), event.get("definition_version"))
            if key not in versions:
                raise SearchTermError("lifecycle event references unknown term")
            current = versions[key]
            before, after = event.get("from"), event.get("to")
            if before != current.lifecycle:
                raise SearchTermError("lifecycle event has stale from state")
            if after not in VALID_TERM_TRANSITIONS[before]:
                raise SearchTermError("invalid search term lifecycle transition")
            _text(event.get("rationale"), f"{event_id}.rationale")
            versions[key] = replace(current, lifecycle=after)
        else:
            raise SearchTermError(f"unsupported search term event type: {kind}")
    return tuple(versions[key] for key in sorted(versions))


def load_search_term_versions(
        path: str | Path = TERM_LEDGER_PATH) -> tuple[SearchTermDefinition, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_search_term_ledger(json.load(handle))


def latest_search_terms(
        path: str | Path = TERM_LEDGER_PATH,
        lifecycles: frozenset[str] | None = None,
) -> tuple[SearchTermDefinition, ...]:
    latest: dict[str, SearchTermDefinition] = {}
    for term in load_search_term_versions(path):
        if lifecycles is not None and term.lifecycle not in lifecycles:
            continue
        if term.definition_version > getattr(
                latest.get(term.id), "definition_version", 0):
            latest[term.id] = term
    terms = tuple(sorted(latest.values(), key=lambda item: item.id))
    return terms


def pilot_search_terms(
        path: str | Path = TERM_LEDGER_PATH) -> tuple[SearchTermDefinition, ...]:
    return latest_search_terms(path, frozenset(("pilot", "active")))
