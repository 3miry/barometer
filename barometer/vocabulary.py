"""Versioned governed behaviour concepts; not connected to detector v0 yet."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


LEDGER_PATH = Path(__file__).with_name("data") / "behaviour_vocabulary.ledger.json"

CONCEPT_ID = re.compile(r"^beh_[0-9]{4}$")
SLUG = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
EVENT_ID = re.compile(r"^vocab_evt_[0-9]{4}$")

VALID_SHAPES = frozenset(("dimension", "event"))
VALID_STATUSES = frozenset(("provisional", "active", "superseded", "retired"))
VALID_DIRECTIONS = frozenset((
    "increase", "decrease", "present", "changed", "uncertain",
))
VALID_EVENT_STATES = frozenset((
    "occurred", "new", "ceased", "recurred", "uncertain",
))
VALID_VALENCES = frozenset((
    "positive", "negative", "mixed", "neutral", "unstated",
))
RESERVED_LEGACY_LABELS = frozenset(("quality", "lazy", "other"))


class VocabularyError(ValueError):
    pass


@dataclass(frozen=True)
class BehaviourConcept:
    id: str
    slug: str
    public_label: str
    shape: str
    parent: str
    definition: str
    exclusions: tuple[str, ...]
    allowed_directions: tuple[str, ...]
    allowed_event_states: tuple[str, ...]
    candidate_phrases: tuple[str, ...]
    examples: tuple[dict, ...]
    counterexamples: tuple[str, ...]
    status: str
    publishable: bool
    definition_version: int
    origin: str


def _nonempty_text(value, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VocabularyError(f"{field} must be non-empty text")
    cleaned = " ".join(value.split())
    if len(cleaned) > maximum:
        raise VocabularyError(f"{field} must be {maximum} characters or fewer")
    return cleaned


def _text_list(value, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VocabularyError(f"{field} must be a list")
    cleaned = tuple(
        _nonempty_text(item, f"{field} item") for item in value
    )
    if required and not cleaned:
        raise VocabularyError(f"{field} must not be empty")
    if len({item.casefold() for item in cleaned}) != len(cleaned):
        raise VocabularyError(f"{field} contains duplicate text")
    return cleaned


def _validate_example(example, concept_id: str, shape: str, index: int) -> dict:
    field = f"{concept_id}.examples[{index}]"
    if not isinstance(example, dict):
        raise VocabularyError(f"{field} must be an object")
    text = _nonempty_text(example.get("text"), f"{field}.text")
    valence = example.get("valence", "unstated")
    if valence not in VALID_VALENCES:
        raise VocabularyError(f"{field}.valence is not recognised")
    direction = example.get("direction")
    event_state = example.get("event_state")
    if shape == "dimension":
        if direction not in VALID_DIRECTIONS or event_state is not None:
            raise VocabularyError(
                f"{field} needs a valid direction and no event_state")
    elif event_state not in VALID_EVENT_STATES or direction is not None:
        raise VocabularyError(
            f"{field} needs a valid event_state and no direction")
    return {
        "text": text,
        "direction": direction,
        "event_state": event_state,
        "valence": valence,
    }


def _validate_concept(raw: dict) -> BehaviourConcept:
    if not isinstance(raw, dict):
        raise VocabularyError("concept must be an object")
    concept_id = raw.get("id")
    if not isinstance(concept_id, str) or not CONCEPT_ID.fullmatch(concept_id):
        raise VocabularyError("concept id must match beh_NNNN")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise VocabularyError(f"{concept_id}.slug is not valid")
    public_label = _nonempty_text(
        raw.get("public_label"), f"{concept_id}.public_label", 40)
    if public_label.casefold() in RESERVED_LEGACY_LABELS:
        raise VocabularyError(
            f"{concept_id}.public_label uses a reserved legacy appraisal")
    shape = raw.get("shape")
    if shape not in VALID_SHAPES:
        raise VocabularyError(f"{concept_id}.shape is not recognised")
    status = raw.get("status")
    if status not in VALID_STATUSES:
        raise VocabularyError(f"{concept_id}.status is not recognised")
    publishable = raw.get("publishable")
    if not isinstance(publishable, bool):
        raise VocabularyError(f"{concept_id}.publishable must be boolean")
    if status != "active" and publishable:
        raise VocabularyError(
            f"{concept_id} cannot be publishable before activation")
    definition_version = raw.get("definition_version")
    if not isinstance(definition_version, int) or definition_version < 1:
        raise VocabularyError(
            f"{concept_id}.definition_version must be a positive integer")
    directions = tuple(raw.get("allowed_directions", ()))
    event_states = tuple(raw.get("allowed_event_states", ()))
    if shape == "dimension":
        if not directions or not set(directions) <= VALID_DIRECTIONS or event_states:
            raise VocabularyError(
                f"{concept_id} dimension needs directions and no event states")
    elif not event_states or not set(event_states) <= VALID_EVENT_STATES or directions:
        raise VocabularyError(
            f"{concept_id} event needs event states and no directions")
    if len(set(directions)) != len(directions):
        raise VocabularyError(f"{concept_id}.allowed_directions has duplicates")
    if len(set(event_states)) != len(event_states):
        raise VocabularyError(f"{concept_id}.allowed_event_states has duplicates")
    examples_raw = raw.get("examples")
    if not isinstance(examples_raw, list) or not examples_raw:
        raise VocabularyError(f"{concept_id}.examples must not be empty")
    examples = tuple(
        _validate_example(example, concept_id, shape, index)
        for index, example in enumerate(examples_raw)
    )
    return BehaviourConcept(
        id=concept_id,
        slug=slug,
        public_label=public_label,
        shape=shape,
        parent=_nonempty_text(raw.get("parent"), f"{concept_id}.parent", 60),
        definition=_nonempty_text(
            raw.get("definition"), f"{concept_id}.definition"),
        exclusions=_text_list(
            raw.get("exclusions"), f"{concept_id}.exclusions", required=True),
        allowed_directions=directions,
        allowed_event_states=event_states,
        candidate_phrases=_text_list(
            raw.get("candidate_phrases"),
            f"{concept_id}.candidate_phrases",
            required=True,
        ),
        examples=examples,
        counterexamples=_text_list(
            raw.get("counterexamples"),
            f"{concept_id}.counterexamples",
            required=True,
        ),
        status=status,
        publishable=publishable,
        definition_version=definition_version,
        origin=_nonempty_text(raw.get("origin"), f"{concept_id}.origin", 120),
    )


def validate_ledger(payload: dict) -> tuple[BehaviourConcept, ...]:
    if not isinstance(payload, dict):
        raise VocabularyError("vocabulary ledger must be an object")
    if payload.get("schema_version") != 1:
        raise VocabularyError("unsupported vocabulary schema version")
    if payload.get("ledger_id") != "barometer-behaviour-vocabulary":
        raise VocabularyError("unexpected vocabulary ledger id")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise VocabularyError("vocabulary ledger must contain events")
    concepts = []
    event_ids = set()
    concept_ids = set()
    slugs = set()
    last_event_sequence = 0
    for event in events:
        if not isinstance(event, dict):
            raise VocabularyError("ledger event must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            raise VocabularyError("ledger event id must match vocab_evt_NNNN")
        if event_id in event_ids:
            raise VocabularyError(f"duplicate ledger event id: {event_id}")
        event_sequence = int(event_id.rsplit("_", 1)[1])
        if event_sequence <= last_event_sequence:
            raise VocabularyError("ledger event ids must be append-only and increasing")
        last_event_sequence = event_sequence
        event_ids.add(event_id)
        if event.get("type") != "concept_created":
            raise VocabularyError(f"unsupported ledger event type: {event.get('type')}")
        _nonempty_text(event.get("recorded_at"), f"{event_id}.recorded_at", 40)
        concept = _validate_concept(event.get("concept"))
        if concept.id in concept_ids:
            raise VocabularyError(f"duplicate concept id: {concept.id}")
        if concept.slug in slugs:
            raise VocabularyError(f"duplicate concept slug: {concept.slug}")
        concept_ids.add(concept.id)
        slugs.add(concept.slug)
        concepts.append(concept)
    return tuple(concepts)


def load_vocabulary(path: str | Path = LEDGER_PATH) -> tuple[BehaviourConcept, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_ledger(json.load(handle))


def concepts_by_id(path: str | Path = LEDGER_PATH) -> dict[str, BehaviourConcept]:
    return {concept.id: concept for concept in load_vocabulary(path)}
