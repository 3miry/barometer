"""Versioned governed behaviour concepts; not connected to detector v0 yet."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re


LEDGER_PATH = Path(__file__).with_name("data") / "behaviour_vocabulary.ledger.json"

CONCEPT_ID = re.compile(r"^beh_[0-9]{4}$")
SLUG = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
EVENT_ID = re.compile(r"^vocab_evt_[0-9]{4}$")

VALID_SHAPES = frozenset(("dimension", "event"))
VALID_STATUSES = frozenset(("provisional", "active", "superseded", "retired"))
VALID_REPORTING_LAYERS = frozenset((
    "model", "interaction", "product", "cross_layer",
))
VALID_STATES = frozenset((
    "present", "absent", "high", "low", "mixed", "uncertain",
))
VALID_CHANGES = frozenset((
    "increase", "decrease", "new", "ceased", "stable", "changed", "uncertain",
))
VALID_EVENT_STATES = frozenset((
    "occurred", "new", "ceased", "recurred", "uncertain",
))
VALID_VALENCES = frozenset((
    "positive", "negative", "mixed", "neutral", "unstated",
))
VALID_CLAIM_STATUSES = frozenset(("reported", "corroborated", "attributed"))
VALID_SUSPECTED_LAYERS = frozenset((
    "model", "product", "serving", "tool", "unknown",
))
VALID_ELICITATION_CONTEXTS = frozenset((
    "ordinary", "task_elicited", "adversarial", "unknown",
))
VALID_SPECIFICITIES = frozenset(("broad", "specific"))
VALID_CODING_SCOPES = VALID_SPECIFICITIES
RESERVED_LEGACY_LABELS = frozenset(("quality", "lazy", "other"))


class VocabularyError(ValueError):
    pass


@dataclass(frozen=True)
class BehaviourConcept:
    id: str
    slug: str
    public_label: str
    shape: str
    reporting_layer: str
    coding_scope: str
    parent: str
    definition: str
    exclusions: tuple[str, ...]
    allowed_states: tuple[str, ...]
    allowed_changes: tuple[str, ...]
    allowed_event_states: tuple[str, ...]
    allowed_qualifiers: tuple[str, ...]
    candidate_phrases: tuple[str, ...]
    examples: tuple[dict, ...]
    counterexamples: tuple[str, ...]
    status: str
    publishable: bool
    definition_version: int
    origin: str


@dataclass(frozen=True)
class HierarchyEdge:
    broader_id: str
    narrower_id: str


@dataclass(frozen=True)
class CodedObservation:
    concept_id: str
    specificity: str
    state: str | None
    change: str | None
    event_state: str | None
    valence: str
    claim_status: str
    suspected_layers: tuple[str, ...]
    elicitation_context: str
    qualifiers: tuple[str, ...]


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
    cleaned = tuple(_nonempty_text(item, f"{field} item") for item in value)
    if required and not cleaned:
        raise VocabularyError(f"{field} must not be empty")
    if len({item.casefold() for item in cleaned}) != len(cleaned):
        raise VocabularyError(f"{field} contains duplicate text")
    return cleaned


def _enum_list(value, field: str, valid: frozenset[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise VocabularyError(f"{field} must be a non-empty list")
    values = tuple(value)
    if not set(values) <= valid:
        raise VocabularyError(f"{field} contains an unrecognised value")
    if len(set(values)) != len(values):
        raise VocabularyError(f"{field} contains duplicates")
    return values


def _validate_example(example, concept_id: str, shape: str, index: int) -> dict:
    field = f"{concept_id}.examples[{index}]"
    if not isinstance(example, dict):
        raise VocabularyError(f"{field} must be an object")
    text = _nonempty_text(example.get("text"), f"{field}.text")
    valence = example.get("valence", "unstated")
    if valence not in VALID_VALENCES:
        raise VocabularyError(f"{field}.valence is not recognised")
    state = example.get("state")
    change = example.get("change")
    event_state = example.get("event_state")
    if shape == "dimension":
        if (state not in VALID_STATES or change not in VALID_CHANGES
                or event_state is not None):
            raise VocabularyError(
                f"{field} needs valid state/change and no event_state")
    elif (event_state not in VALID_EVENT_STATES or state is not None
          or change is not None):
        raise VocabularyError(
            f"{field} needs a valid event_state and no state/change")
    return {
        "text": text,
        "state": state,
        "change": change,
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
    reporting_layer = raw.get("reporting_layer")
    if reporting_layer not in VALID_REPORTING_LAYERS:
        raise VocabularyError(f"{concept_id}.reporting_layer is not recognised")
    coding_scope = raw.get("coding_scope")
    if coding_scope not in VALID_CODING_SCOPES:
        raise VocabularyError(f"{concept_id}.coding_scope is not recognised")
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
    states = tuple(raw.get("allowed_states", ()))
    changes = tuple(raw.get("allowed_changes", ()))
    event_states = tuple(raw.get("allowed_event_states", ()))
    if shape == "dimension":
        if (not states or not set(states) <= VALID_STATES
                or not changes or not set(changes) <= VALID_CHANGES
                or event_states):
            raise VocabularyError(
                f"{concept_id} dimension needs states/changes and no event states")
    elif (not event_states or not set(event_states) <= VALID_EVENT_STATES
          or states or changes):
        raise VocabularyError(
            f"{concept_id} event needs event states and no states/changes")
    if len(set(states)) != len(states):
        raise VocabularyError(f"{concept_id}.allowed_states has duplicates")
    if len(set(changes)) != len(changes):
        raise VocabularyError(f"{concept_id}.allowed_changes has duplicates")
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
        reporting_layer=reporting_layer,
        coding_scope=coding_scope,
        parent=_nonempty_text(raw.get("parent"), f"{concept_id}.parent", 60),
        definition=_nonempty_text(raw.get("definition"), f"{concept_id}.definition"),
        exclusions=_text_list(
            raw.get("exclusions"), f"{concept_id}.exclusions", required=True),
        allowed_states=states,
        allowed_changes=changes,
        allowed_event_states=event_states,
        allowed_qualifiers=_text_list(
            raw.get("allowed_qualifiers", []),
            f"{concept_id}.allowed_qualifiers",
        ),
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
        origin=_nonempty_text(raw.get("origin"), f"{concept_id}.origin", 160),
    )


def _validate_hierarchy(raw_edges, concepts: tuple[BehaviourConcept, ...]) -> tuple[HierarchyEdge, ...]:
    if not isinstance(raw_edges, list):
        raise VocabularyError("hierarchy_edges must be a list")
    by_id = {concept.id: concept for concept in concepts}
    edges: list[HierarchyEdge] = []
    seen: set[tuple[str, str]] = set()
    children: dict[str, set[str]] = {concept_id: set() for concept_id in by_id}
    for index, raw in enumerate(raw_edges):
        field = f"hierarchy_edges[{index}]"
        if not isinstance(raw, dict):
            raise VocabularyError(f"{field} must be an object")
        broader_id = raw.get("broader_id")
        narrower_id = raw.get("narrower_id")
        if broader_id not in by_id or narrower_id not in by_id:
            raise VocabularyError(f"{field} references an unknown concept")
        if broader_id == narrower_id:
            raise VocabularyError(f"{field} cannot be self-referential")
        if by_id[broader_id].shape != by_id[narrower_id].shape:
            raise VocabularyError(f"{field} must connect concepts of the same shape")
        if by_id[broader_id].coding_scope != "broad":
            raise VocabularyError(f"{field} broader concept must have broad coding_scope")
        edge_key = (broader_id, narrower_id)
        if edge_key in seen:
            raise VocabularyError(f"duplicate hierarchy edge: {edge_key}")
        seen.add(edge_key)
        children[broader_id].add(narrower_id)
        edges.append(HierarchyEdge(*edge_key))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(concept_id: str) -> None:
        if concept_id in visiting:
            raise VocabularyError("hierarchy must not contain a cycle")
        if concept_id in visited:
            return
        visiting.add(concept_id)
        for child_id in children[concept_id]:
            visit(child_id)
        visiting.remove(concept_id)
        visited.add(concept_id)

    for concept_id in by_id:
        visit(concept_id)
    return tuple(edges)


def _raw_hierarchy_edges(payload: dict) -> list[dict]:
    return [
        {
            "broader_id": event.get("broader_id"),
            "narrower_id": event.get("narrower_id"),
        }
        for event in payload["events"]
        if event.get("type") == "hierarchy_edge_created"
    ]


def validate_ledger(payload: dict) -> tuple[BehaviourConcept, ...]:
    if not isinstance(payload, dict):
        raise VocabularyError("vocabulary ledger must be an object")
    if payload.get("schema_version") != 2:
        raise VocabularyError("unsupported vocabulary schema version")
    if payload.get("ledger_id") != "barometer-behaviour-vocabulary":
        raise VocabularyError("unexpected vocabulary ledger id")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise VocabularyError("vocabulary ledger must contain events")
    concepts = []
    concept_indexes: dict[str, int] = {}
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
        event_type = event.get("type")
        _nonempty_text(event.get("recorded_at"), f"{event_id}.recorded_at", 40)
        if event_type == "concept_created":
            concept = _validate_concept(event.get("concept"))
            if concept.id in concept_ids:
                raise VocabularyError(f"duplicate concept id: {concept.id}")
            if concept.slug in slugs:
                raise VocabularyError(f"duplicate concept slug: {concept.slug}")
            concept_ids.add(concept.id)
            slugs.add(concept.slug)
            concept_indexes[concept.id] = len(concepts)
            concepts.append(concept)
        elif event_type == "concept_superseded":
            concept_id = event.get("concept_id")
            replacement_id = event.get("replacement_id")
            if concept_id not in concept_ids or replacement_id not in concept_ids:
                raise VocabularyError(
                    f"{event_id} supersession references an unknown concept")
            if concept_id == replacement_id:
                raise VocabularyError(
                    f"{event_id} concept cannot supersede itself")
            index = concept_indexes[concept_id]
            concept = concepts[index]
            replacement_concept = concepts[concept_indexes[replacement_id]]
            if concept.status == "superseded":
                raise VocabularyError(
                    f"{event_id} concept is already superseded")
            if concept.shape != replacement_concept.shape:
                raise VocabularyError(
                    f"{event_id} replacement must have the same shape")
            _nonempty_text(event.get("rationale"), f"{event_id}.rationale")
            concepts[index] = replace(
                concept, status="superseded", publishable=False)
        elif event_type != "hierarchy_edge_created":
            raise VocabularyError(f"unsupported ledger event type: {event_type}")
    validated = tuple(concepts)
    _validate_hierarchy(_raw_hierarchy_edges(payload), validated)
    return validated


def load_vocabulary(path: str | Path = LEDGER_PATH) -> tuple[BehaviourConcept, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_ledger(json.load(handle))


def load_hierarchy(path: str | Path = LEDGER_PATH) -> tuple[HierarchyEdge, ...]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    concepts = validate_ledger(payload)
    return _validate_hierarchy(_raw_hierarchy_edges(payload), concepts)


def concepts_by_id(path: str | Path = LEDGER_PATH) -> dict[str, BehaviourConcept]:
    return {concept.id: concept for concept in load_vocabulary(path)}


def concept_replacements(path: str | Path = LEDGER_PATH) -> dict[str, str]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_ledger(payload)
    return {
        event["concept_id"]: event["replacement_id"]
        for event in payload["events"]
        if event.get("type") == "concept_superseded"
    }


def narrower_concept_ids(
    concept_id: str,
    path: str | Path = LEDGER_PATH,
) -> frozenset[str]:
    concepts = concepts_by_id(path)
    if concept_id not in concepts:
        raise VocabularyError(f"unknown concept id: {concept_id}")
    children: dict[str, set[str]] = {item: set() for item in concepts}
    for edge in load_hierarchy(path):
        children[edge.broader_id].add(edge.narrower_id)
    result: set[str] = set()
    pending = list(children[concept_id])
    while pending:
        child_id = pending.pop()
        if child_id in result:
            continue
        result.add(child_id)
        pending.extend(children[child_id])
    return frozenset(result)


def validate_coded_observation(
    raw: dict,
    path: str | Path = LEDGER_PATH,
) -> CodedObservation:
    if not isinstance(raw, dict):
        raise VocabularyError("coded observation must be an object")
    concepts = concepts_by_id(path)
    concept_id = raw.get("concept_id")
    if concept_id not in concepts:
        raise VocabularyError(f"coded observation references unknown concept: {concept_id}")
    concept = concepts[concept_id]
    expected_specificity = concept.coding_scope
    specificity = raw.get("specificity")
    if specificity not in VALID_SPECIFICITIES:
        raise VocabularyError("coded observation specificity is not recognised")
    if specificity != expected_specificity:
        raise VocabularyError(
            f"{concept_id} specificity must be {expected_specificity}")
    state = raw.get("state")
    change = raw.get("change")
    event_state = raw.get("event_state")
    if concept.shape == "dimension":
        if (state not in concept.allowed_states
                or change not in concept.allowed_changes
                or event_state is not None):
            raise VocabularyError(
                f"{concept_id} observation needs valid state/change and no event_state")
    elif (event_state not in concept.allowed_event_states or state is not None
          or change is not None):
        raise VocabularyError(
            f"{concept_id} observation needs valid event_state and no state/change")
    valence = raw.get("valence")
    if valence not in VALID_VALENCES:
        raise VocabularyError("coded observation valence is not recognised")
    claim_status = raw.get("claim_status")
    if claim_status not in VALID_CLAIM_STATUSES:
        raise VocabularyError("coded observation claim_status is not recognised")
    suspected_layers = _enum_list(
        raw.get("suspected_layers"),
        "coded observation suspected_layers",
        VALID_SUSPECTED_LAYERS,
    )
    if "unknown" in suspected_layers and len(suspected_layers) != 1:
        raise VocabularyError("unknown suspected layer cannot be combined with another layer")
    elicitation_context = raw.get("elicitation_context")
    if elicitation_context not in VALID_ELICITATION_CONTEXTS:
        raise VocabularyError("coded observation elicitation_context is not recognised")
    qualifiers = _text_list(raw.get("qualifiers", []), "coded observation qualifiers")
    if not set(qualifiers) <= set(concept.allowed_qualifiers):
        raise VocabularyError(f"{concept_id} observation contains an unsupported qualifier")
    return CodedObservation(
        concept_id=concept_id,
        specificity=specificity,
        state=state,
        change=change,
        event_state=event_state,
        valence=valence,
        claim_status=claim_status,
        suspected_layers=suspected_layers,
        elicitation_context=elicitation_context,
        qualifiers=qualifiers,
    )


def validate_coded_observations(
    raw_observations: list[dict],
    path: str | Path = LEDGER_PATH,
) -> tuple[CodedObservation, ...]:
    """Validate co-coding without storing duplicates or parent/child twice."""
    if not isinstance(raw_observations, list):
        raise VocabularyError("coded observations must be a list")
    observations = tuple(
        validate_coded_observation(raw, path) for raw in raw_observations)
    concept_ids = [observation.concept_id for observation in observations]
    if len(set(concept_ids)) != len(concept_ids):
        raise VocabularyError("a report cannot store the same concept more than once")
    selected = set(concept_ids)
    for concept_id in selected:
        overlap = narrower_concept_ids(concept_id, path) & selected
        if overlap:
            raise VocabularyError(
                "a report cannot store both a broad parent and its narrower child")
    return observations


def ordinary_use_signal_eligible(observation: CodedObservation) -> bool:
    """Unknown or adversarial elicitation needs review outside ordinary signals."""
    return observation.elicitation_context in {"ordinary", "task_elicited"}
