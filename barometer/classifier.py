"""Conservative structured classifier for shadow evaluation only.

This is a deterministic development baseline, not a validated production
classifier. It prefers abstention over unsupported specificity and never alters
detector v0 or public output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import html
from pathlib import Path
import re

from .catalog import MODEL_CATALOG
from .vocabulary import (
    CodedObservation,
    LEDGER_PATH,
    concepts_by_id,
    narrower_concept_ids,
    validate_coded_observations,
)


CLASSIFIER_VERSION = "structured-rules-v1-dev"


VALID_ELIGIBILITY = frozenset((
    "behaviour_report",
    "novel_candidate",
    "chatter",
    "uncodable_appraisal",
    "ambiguous",
    "excluded_adversarial",
))
VALID_ONSET_PRECISION = frozenset((
    "exact", "day", "broad_period", "unknown",
))


@dataclass(frozen=True)
class Rule:
    concept_id: str
    pattern: re.Pattern[str]
    state: str | None
    change: str | None
    event_state: str | None
    valence: str
    suspected_layers: tuple[str, ...] = ("model",)
    qualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredClassification:
    eligibility: str
    onset_precision: str
    observations: tuple[CodedObservation, ...]
    abstention_reason: str | None = None
    novelty_candidates: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "eligibility": self.eligibility,
            "onset_precision": self.onset_precision,
            "observations": [asdict(item) for item in self.observations],
            "abstention_reason": self.abstention_reason,
            "novelty_candidates": list(self.novelty_candidates),
        }


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _rule(
    concept_id: str,
    pattern: str,
    *,
    state: str | None = "uncertain",
    change: str | None = "uncertain",
    event_state: str | None = None,
    valence: str = "unstated",
    suspected_layers: tuple[str, ...] = ("model",),
    qualifiers: tuple[str, ...] = (),
) -> Rule:
    return Rule(
        concept_id=concept_id,
        pattern=_rx(pattern),
        state=state,
        change=change,
        event_state=event_state,
        valence=valence,
        suspected_layers=suspected_layers,
        qualifiers=qualifiers,
    )


# Specific and high-information wording comes before broad wording. These are
# classifier rules, not source-collection probes.
RULES = (
    # Responsiveness and completion.
    _rule("beh_0001", r"\b(?:much\s+)?faster\b", change="decrease", valence="positive"),
    _rule("beh_0001", r"\b(?:slower|lag(?:gy)?|latency|taking longer to respond|taking forever)\b", change="increase", valence="negative"),
    _rule("beh_0002", r"\b(?:cut(?:ting)? (?:answers? )?off|stopp?ed mid[- ](?:sentence|thought)|answer suddenly ended)\b", change="increase", valence="negative"),
    _rule("beh_0003", r"\bshorter answers?\b[^.]{0,80}\b(?:prefer|like|better)\b", change="decrease", valence="positive"),
    _rule("beh_0003", r"\b(?:became|become|gotten) (?:much )?more thorough\b|\bmore thorough lately\b", state="high", change="increase", valence="positive"),
    _rule("beh_0003", r"\b(?:more|much more) thorough\b", state="high", change="uncertain", valence="positive"),
    _rule("beh_0003", r"\b(?:less detailed|bare[- ]minimum answers?|surface level)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0004", r"\b(?:skips?|ignored?)\b[^.]{0,80}\b(?:steps?|instructions?)\b|\bdid not complete the task\b", change="decrease", valence="negative"),

    # Correctness and output quality with supported specificity.
    _rule("beh_0005", r"\b(?:facts?|dates?) (?:wrong|incorrect)\b|\b(?:getting|gets?) (?:basic )?(?:facts?|dates?) wrong\b|\binvent(?:ed|s)? (?:a )?(?:date|fact|source)\b", change="increase", valence="negative"),
    _rule("beh_0006", r"\b(?:contradicts? (?:its )?(?:own )?(?:reasoning|step)|calculation is wrong|logical? errors?|reasoning (?:is )?wrong)\b", change="increase", valence="negative"),
    _rule("beh_0007", r"\bcode\b[^.]{0,100}\b(?:does not run|doesn't run|fails?|crashes?|no longer performs|does not perform)\b|\bcompiles?\b[^.]{0,80}\b(?:fails?|no longer performs|does not perform)\b", change="increase", valence="negative"),
    _rule("beh_0008", r"\b(?:unusually |much )?clear explanations?\b|\bclearer writing\b", state="present", valence="positive"),
    _rule("beh_0008", r"\b(?:harder to follow|unclear writing|sloppy sentences?)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0019", r"\b(?:keeps? )?(?:making|makes?) mistakes?\b|\bgetting things wrong\b|\bmore errors lately\b", state="present", valence="negative"),

    # Refusal, repetition, context, and memory.
    _rule("beh_0009", r"\b(?:refusing|refuses|refused)\b[^.]{0,100}\b(?:requests?|answer|formatting)?\b|\b(?:would not|won't) answer\b", change="increase", valence="negative"),
    _rule("beh_0010", r"\brepeats?\b[^.]{0,80}\b(?:paragraphs?|same thing|itself)\b|\bmore repetitive\b", change="increase", valence="negative"),
    _rule("beh_0011", r"\b(?:forgot|forgets?)\b[^.]{0,80}\b(?:first message|earlier|context)\b|\b(?:lost|loses?) (?:track of )?context\b|\bdegrades?\b[^.]{0,80}\b(?:long context|context window|compaction)\b", change="increase", valence="negative"),
    _rule("beh_0012", r"\bremembered\b[^.]{0,100}\b(?:saved preference|from last time|previous (?:chat|conversation))\b", change="increase", valence="positive"),

    # Interaction dimensions. Loss/decrease rules precede simple presence.
    _rule("beh_0013", r"\b(?:lost (?:(?:its|that) )?warmth|less warm|colder tone|became colder)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0013", r"\b(?:become|became|more|much) warmer\b", state="high", change="increase", valence="positive"),
    _rule("beh_0013", r"\b(?:is|feels?|sounds?) warm\b|\bwarm and\b", state="high", valence="positive"),
    _rule("beh_0014", r"\b(?:became|become)\b[^.]{0,100}\bflirt(?:y|atious|ing)\b|\bmore flirt(?:y|atious|ing)\b", state="present", change="increase", valence="mixed"),
    _rule("beh_0014", r"\b(?:really |slightly )?flirt(?:y|atious|ing)\b", state="present", change="uncertain", valence="mixed"),
    _rule("beh_0015", r"\b(?:agrees? with (?:every|all)|sycophantic|keeps? flattering)\b", state="present", valence="mixed"),
    _rule("beh_0016", r"\b(?:unexpectedly good at poetry|more creative|creative lately)\b", state="uncertain", change="increase", valence="positive"),
    _rule("beh_0020", r"\b(?:understands?|understood) exactly (?:how|why) I (?:feel|felt|am upset)\b|\bemotionally perceptive\b", state="high", valence="positive"),
    _rule("beh_0020", r"\bmisread(?:s|ing)? (?:my mood|how I feel|my emotional state)\b", state="low", valence="negative"),
    _rule("beh_0021", r"\b(?:extremely |much more )?expressive\b|\bemoji[- ]heavy\b", state="high", valence="neutral"),
    _rule("beh_0021", r"\b(?:flat and emotionless|less expressive)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0022", r"\b(?:become|became|more|much more) formal\b", state="high", change="increase", valence="neutral"),
    _rule("beh_0022", r"\b(?:too corporate|more conversational|less formal)\b", state="low", change="decrease", valence="mixed"),
    _rule("beh_0023", r"\b(?:lost (?:its )?(?:playfulness|sense of humo(?:u)?r)|less playful)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0023", r"\b(?:more |much more )?playful\b|\bjoins? in with jokes\b", state="high", change="increase", valence="positive"),
    _rule("beh_0024", r"\b(?:stands? (?:its|their) ground|pushes? back more|more assertive)\b", state="high", change="increase", valence="positive"),
    _rule("beh_0024", r"\b(?:too deferential|less assertive)\b", state="low", change="decrease", valence="negative"),
    _rule("beh_0025", r"\b(?:anticipates? the next (?:useful )?step|takes? more initiative)\b", state="high", valence="positive"),
    _rule("beh_0026", r"\b(?:important bond|intensely attached|frames? us as close|emotionally significant relationship)\b", state="high", valence="mixed"),

    # Apparent affect and conduct. This describes presentation, not inner state.
    _rule("beh_0027", r"\b(?:sound(?:s|ed)?|seem(?:s|ed)?|appear(?:s|ed)?) (?:strangely )?(?:furious|angry|irritated)\b", state="present", change="new", valence="negative", qualifiers=("anger",)),
    _rule("beh_0027", r"\b(?:sounds?|seems?|appears?) (?:strangely )?(?:sad|melancholy)\b", state="present", change="new", valence="mixed", qualifiers=("sadness",)),
    _rule("beh_0027", r"\b(?:sounds?|seems?|appears?) (?:really )?(?:excited|enthusiastic)\b", state="present", valence="positive", qualifiers=("excitement",)),
    _rule("beh_0027", r"\b(?:sounds?|seems?|appears?) (?:anxious|fearful)\b", state="present", valence="mixed", qualifiers=("anxiety",)),
    _rule("beh_0028", r"\b(?:hostile|insult(?:ed|ing|s)?|contemptuous|aggressive(?:ly)?)\b", state="present", change="new", valence="negative"),

    # Event-shaped model/cross-layer observations.
    _rule("beh_0017", r"\bstarted (?:printing|showing)\b[^.]{0,100}\b(?:system instructions?|system message|internal[- ]looking instructions?|tool[- ]routing text)\b", state=None, change=None, event_state="new", valence="negative", suspected_layers=("unknown",)),
    _rule("beh_0017", r"\b(?:system instructions?|system message|internal[- ]looking instructions?|tool[- ]routing text|hidden system instructions?)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("unknown",)),
    _rule("beh_0018", r"\b(?:different persona|personality changed|old persona came back)\b", state=None, change=None, event_state="new", valence="mixed"),
    _rule("beh_0030", r"\b(?:explicit sexual|erotic|sexual(?:ised|ized))\b[^.]{0,100}\b(?:unprompted|without prompt(?:ing)?|ordinary|unrelated)\b|\b(?:unprompted|without prompt(?:ing)?)\b[^.]{0,100}\b(?:sexual|erotic)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("unknown",)),
    _rule("beh_0031", r"\b(?:response may violate (?:our )?usage policies|usage[- ]policy warning|moderation warning|response was blocked)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("unknown",)),
    _rule("beh_0032", r"\b(?:unprompted violent threat|graphic violent|violent (?:description|threat))\b", state=None, change=None, event_state="new", valence="negative", suspected_layers=("unknown",)),
    _rule("beh_0029", r"\b(?:disturbing|inappropriate|sensitive) content\b[^.]{0,80}\b(?:unprompted|did not request|unexpected)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("unknown",)),

    # Product and delivery events.
    _rule("beh_0034", r"\b(?:service unavailable|platform unavailable|will not load|won't load|cannot access (?:chatgpt|claude|gemini|grok))\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("product",)),
    _rule("beh_0035", r"\b(?:spins? (?:indefinitely|forever)|stuck generating|blank response|error generating (?:a )?response|never returns? an answer)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("product",)),
    _rule("beh_0036", r"\b(?:metadata|model identifier)\b[^.]{0,100}\b(?:different model|does not match|doesn't match)\b|\bserved (?:me )?a different model\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("serving",)),
    _rule("beh_0037", r"\b(?:browsing|memory|file handling|image generation|tool)\b[^.]{0,100}\b(?:fails?|failed|did not save|could not open)\b", state=None, change=None, event_state="recurred", valence="negative", suspected_layers=("tool",)),
    _rule("beh_0033", r"\b(?:chatgpt|claude|gemini|grok|platform|service) (?:is )?broken\b[^.]{0,140}\b(?:cannot tell|loading|generation|tool)\b", state=None, change=None, event_state="occurred", valence="negative", suspected_layers=("product",)),
)


ADVERSARIAL = _rx(
    r"\b(?:jailbroke|jailbreak|prompt[- ]extract(?:ion)?|"
    r"made it reveal|make it print (?:its )?hidden|force(?:d)? it to leak)\b")
EXPLICIT_DENIAL = _rx(
    r"\b(?:not been my experience|has not been my experience|"
    r"never noticed|haven't encountered|have not encountered)\b")
NOVEL_BEHAVIOUR = _rx(r"\b(?:spiky|new behaviour I cannot describe)\b")
AMBIGUOUS_CHANGE = _rx(
    r"\b(?:feels? different somehow|something (?:is|feels) different)\b")
BROAD_APPRAISAL = _rx(
    r"\b(?:wonderful|rubbish|quality (?:has )?tanked|"
    r"completely broken|nerfed|degraded|worse|better)\b")
NOVELTY_HINTS = (
    ("circular task progress", _rx(r"\b(?:run|runs|running) in circles\b")),
    ("tool-call control", _rx(r"\b(?:loose with tool calls|tool calls? (?:go|went) wrong)\b")),
    ("structured-output adherence", _rx(
        r"\b(?:does not|doesn't|do not|don't) follow (?:the |your )?schema\b|"
        r"\bhallucinat(?:es?|ed|ing) (?:tool )?propert(?:y|ies)\b")),
    ("interpretation/comprehension", _rx(
        r"\b(?:worse|bad|struggles?) at interpret(?:ing|ation)\b|"
        r"\b(?:misunderstands?|does not understand|doesn't understand)\b")),
    ("session wrap-up pressure", _rx(
        r"\b(?:trying|tries?) to wrap up (?:long )?(?:sessions?|conversations?)\b")),
    ("agentic coding performance", _rx(
        r"\b(?:terrible|worse|better|stronger) at agentic coding\b")),
    ("image-generation performance", _rx(
        r"\b(?:image creation|generate images?|image generation)\b[^.]{0,100}"
        r"\b(?:bad|worse|better|fails?)\b")),
)


def normalise_report_text(text: str) -> str:
    """Normalise public-source markup without changing the retained raw text."""
    unescaped = html.unescape(text or "")
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return " ".join(without_tags.replace("�", " ").split())


def mentioned_families(text: str) -> frozenset[str]:
    """Return every tracked family named in a report, never just the first."""
    normalised = normalise_report_text(text)
    mentioned = set()
    for family, entry in MODEL_CATALOG.items():
        aliases = entry.get("recognised_terms", ())
        if any(re.search(
            r"(?<![a-z0-9])" + re.escape(alias).replace(r"\ ", r"[\s-]+")
            + r"(?![a-z0-9])",
            normalised,
            re.IGNORECASE,
        ) for alias in aliases):
            mentioned.add(family)
    return frozenset(mentioned)


def mentioned_variants(text: str) -> frozenset[str]:
    """Return every tracked exact-model variant named in a report."""
    normalised = normalise_report_text(text)
    mentioned = set()
    for entry in MODEL_CATALOG.values():
        for variant in entry.get("tracked_variants", ()):
            if any(re.search(
                r"(?<![a-z0-9])" + re.escape(alias).replace(r"\ ", r"[\s-]+")
                + r"(?![a-z0-9])",
                normalised,
                re.IGNORECASE,
            ) for alias in variant["aliases"]):
                mentioned.add(variant["key"])
    return frozenset(mentioned)


def attribution_review_status(
    text: str,
    stored_family: str | None,
    stored_variant: str | None = None,
) -> str:
    """Gate legacy routing without pretending to resolve clause-level targets."""
    families = mentioned_families(text)
    variants = mentioned_variants(text)
    if not families:
        return "no_tracked_family"
    if len(families) > 1:
        return "multi_family_review"
    if len(variants) > 1:
        return "multi_variant_review"
    if stored_family is not None and stored_family not in families:
        return "stored_family_mismatch"
    if stored_variant is not None and variants and stored_variant not in variants:
        return "stored_variant_mismatch"
    if len(variants) == 1:
        return "single_variant"
    return "single_family"


def attribution_is_aggregate_ready(status: str) -> bool:
    return status in {"single_family", "single_variant"}


def infer_onset_precision(text: str) -> str:
    normalised = normalise_report_text(text)
    if re.search(r"\b(?:today|yesterday)\b", normalised, re.IGNORECASE):
        return "day"
    if re.search(
        r"\b(?:now|lately|recently|this week|since launch|"
        r"started|suddenly|became|become|no longer|over the last)\b",
        normalised,
        re.IGNORECASE,
    ):
        return "broad_period"
    return "unknown"


def _elicitation_context(text: str) -> str:
    return "adversarial" if ADVERSARIAL.search(text) else "ordinary"


def detect_novelty_candidates(text: str) -> tuple[str, ...]:
    normalised = normalise_report_text(text)
    return tuple(
        label for label, pattern in NOVELTY_HINTS if pattern.search(normalised))


def _raw_observation(
    rule: Rule,
    *,
    specificity: str,
    elicitation: str,
    text: str,
) -> dict:
    valence = rule.valence
    if (rule.concept_id == "beh_0014"
            and re.search(r"\b(?:love|like|enjoy)\b", text, re.IGNORECASE)
            and not re.search(
                r"\b(?:uncomfortable|alarming|unwanted)\b", text, re.IGNORECASE)):
        valence = "positive"
    if rule.concept_id == "beh_0017" and elicitation == "adversarial":
        valence = "neutral"
    return {
        "concept_id": rule.concept_id,
        "specificity": specificity,
        "state": rule.state,
        "change": rule.change,
        "event_state": rule.event_state,
        "valence": valence,
        "claim_status": "reported",
        "suspected_layers": list(rule.suspected_layers),
        "elicitation_context": elicitation,
        "qualifiers": list(rule.qualifiers),
    }


def classify_report(
    text: str,
    *,
    vocabulary_path: str | Path = LEDGER_PATH,
) -> StructuredClassification:
    """Classify one report conservatively against the provisional vocabulary."""
    normalised = normalise_report_text(text)
    onset_precision = infer_onset_precision(normalised)
    elicitation = _elicitation_context(normalised)
    novelty_candidates = list(detect_novelty_candidates(normalised))
    if NOVEL_BEHAVIOUR.search(normalised):
        novelty_candidates.append("unmapped descriptive behaviour")
    novelty_candidates_tuple = tuple(dict.fromkeys(novelty_candidates))
    concepts = concepts_by_id(vocabulary_path)

    matched: dict[str, Rule] = {}
    if not EXPLICIT_DENIAL.search(normalised):
        for rule in RULES:
            if rule.concept_id not in matched and rule.pattern.search(normalised):
                matched[rule.concept_id] = rule

    # A supported child is stored alone and rolled up analytically. Do not
    # manufacture a second observation at its broad parent.
    selected_ids = set(matched)
    for concept_id in tuple(selected_ids):
        if narrower_concept_ids(concept_id, vocabulary_path) & selected_ids:
            selected_ids.remove(concept_id)

    raw_observations = [
        _raw_observation(
            matched[concept_id],
            specificity=concepts[concept_id].coding_scope,
            elicitation=elicitation,
            text=normalised,
        )
        for concept_id in matched
        if concept_id in selected_ids
    ]
    observations = validate_coded_observations(raw_observations, vocabulary_path)

    if elicitation == "adversarial":
        return StructuredClassification(
            "excluded_adversarial",
            onset_precision,
            observations,
            "deliberate adversarial elicitation is outside ordinary-use signals",
            novelty_candidates_tuple,
        )
    if observations:
        return StructuredClassification(
            "behaviour_report", onset_precision, observations,
            novelty_candidates=novelty_candidates_tuple)
    if EXPLICIT_DENIAL.search(normalised):
        return StructuredClassification(
            "chatter", onset_precision, (), "the quoted or general claim is explicitly denied")
    if novelty_candidates_tuple:
        return StructuredClassification(
            "novel_candidate", onset_precision, (),
            "specific behaviour is not in the governed vocabulary",
            novelty_candidates_tuple)
    if AMBIGUOUS_CHANGE.search(normalised):
        return StructuredClassification(
            "ambiguous", onset_precision, (), "change is asserted without an observable behaviour")
    if BROAD_APPRAISAL.search(normalised):
        return StructuredClassification(
            "uncodable_appraisal", onset_precision, (), "appraisal lacks a supported behaviour concept")
    return StructuredClassification(
        "chatter", onset_precision, (), "no governed behaviour assertion found")
