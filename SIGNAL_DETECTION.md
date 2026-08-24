# Signal detection architecture

Status: design contract for the post-MVP detector. This document does not
describe the current keyword classifier as scientifically validated, and its
presence does not activate new collection, paid APIs, model calls, or scheduled
processing.

Phase 0 implementation now includes a validated append-only vocabulary ledger,
codable hierarchy edges, a structured observation contract, and an explicitly
synthetic evaluation fixture. Every seeded concept remains `provisional` and
non-publishable; detector v0 remains unchanged.

Barometer adapts ideas from spontaneous-report pharmacovigilance to public
reports of model behaviour. It does not claim that model reports are medical
cases, that the resulting instrument is regulator-validated, or that an
observational signal proves causality.

## The problem with taxonomy v0

The current classifier was useful for proving the pipeline, but its categories
mix several different kinds of information:

- `quality` is an appraisal triggered by words such as "worse", "nerfed",
  "degraded", and "off today". It does not identify the changed behaviour.
- `lazy` conflates truncation, under-elaboration, and failure to complete an
  instruction.
- `length` is directional, while `refusals` names a behaviour and `sluggish`
  names an experience.
- Positive observations fall into `other` because the intake and keyword list
  were designed around complaints.
- `other` is currently published like a behaviour even though it really means
  "not classified by this vocabulary".

These labels must remain tagged as `taxonomy-v0`. They are interface calibration
data, not a defensible historical baseline for the new detector. In particular,
legacy `quality` reports must not be silently reinterpreted as factual errors,
bad writing, coding failures, or misunderstanding.

## Load-bearing rules

1. Detect a reported behaviour, not a public mood or a verdict about a model.
2. Keep behaviour, current state, temporal change, reporter valence, evidence
   quality, and causality separate. None is a proxy for another.
3. Run signal detection without privileging negative valence. Unexpectedly good,
   bad, mixed, and neutral changes receive the same statistical opportunity.
4. Deduplicate cascades before counting reports or building comparator tables.
5. Count a report at most once for a given behaviour in a given analysis, even
   when the report contains the same idea repeatedly.
6. Preserve exact-model attribution. Family-wide reports never colour an exact
   model's weather.
7. Treat all anonymous user submissions as one source type. Volume from that
   lane cannot manufacture cross-source corroboration.
8. Keep arbitrary report language private. Public output receives only governed
   canonical concepts and aggregate counts.
9. Exclude synthetic, legacy, and test observations from production statistics.
10. State the denominator limitation everywhere it matters: report frequency is
    not incidence, prevalence, affected-user share, or model failure rate.
11. Describe public-source observations as reported or suspected unless an
    independent evidence tier supports stronger language. The system is partly
    a structured public vibe check, not a window into hidden serving machinery.
12. Ordinary-use signals exclude deliberate jailbreak, prompt-extraction, and
    adversarial elicitation. Such evidence may enter a separately governed
    security-evaluation lane, never the ordinary report comparator.

## Observation model

One incoming report is an envelope. Classification can produce zero or more
structured behaviour observations from that envelope.

### Report envelope

| Field | Meaning |
| --- | --- |
| `report_id` | Internal stable identifier; never public |
| `received_at` | Source publication or Barometer receipt time; the report's day zero |
| `claimed_onset_at` | Optional claimed behaviour-onset time; null when unknown |
| `onset_precision` | Exact, day, broad period, or unknown |
| `source_type` | HN, X, user, or another approved source class |
| `family` | Claude, GPT, Grok, Gemini, etc. |
| `variant` | Exact model only when explicitly attributable |
| `surface` | Web, mobile, desktop, API, unknown |
| `collection_lane` | Discovery sample, targeted probe, or user report |
| `query_version` | Versioned collection/probe definition, where applicable |
| `inclusion_probability` | Sampling probability where it is known |
| `text_private` | Retained only inside the existing private raw-data boundary |
| `synthetic` | Mandatory provenance flag |
| `cascade_id` | Deduplication cluster assigned before signal counting |

### Behaviour observation

| Field | Meaning |
| --- | --- |
| `behaviour_id` | Stable identifier from the governed vocabulary |
| `specificity` | `broad` or `specific`; a broad parent is a valid final code |
| `state` | For dimensions: present/absent, high/low, mixed, or uncertain |
| `change` | Separately: increase, decrease, new, ceased, stable, changed, or uncertain |
| `event_state` | For events: `occurred`, `new`, `ceased`, `recurred`, or `uncertain` |
| `valence` | `positive`, `negative`, `mixed`, `neutral`, or `unstated` |
| `claim_status` | `reported`, `corroborated`, or `attributed`; new public reports begin as reported |
| `suspected_layers` | Model, product, serving, tool, or unknown; more than one may remain plausible |
| `elicitation_context` | Ordinary, task-elicited, adversarial, or unknown |
| `qualifiers` | Governed optional detail, such as a specific apparent-affect label |
| `evidence_kind` | `assertion`, `example`, `reproduction`, `artifact`, or `quantitative` |
| `classification_source` | `reporter`, `rule`, `model`, or `human` |
| `classifier_version` | Reproducible rules/model/vocabulary version |
| `confidence` | Calibrated classification confidence, not signal confidence |
| `review_state` | `automatic`, `confirmed`, `corrected`, or `rejected` |

A report can legitimately contain more than one observation. For example,
"answers are faster but make more factual mistakes" may produce latency change
`decrease` with positive valence and factual-error change `increase` with
negative valence. The two observations remain linked to one report envelope.

Co-coding is normal. "Warmer, more playful, and slightly flirtatious" produces
three sibling observations rather than one winner. Hierarchy roll-up is not
co-coding: a factual-error child contributes to its correctness-problem parent
in a parent-level analysis but is counted only once in that analysis.

Valence records the reporter's appraisal, not an intrinsic moral property of a
behaviour. "More flirtatious" can therefore be positive, negative, mixed, or
unstated without changing its behaviour identifier.

A report does not need an event-onset date or an explicit "this changed today"
claim to be eligible. "Opus 5 is spiky" is a behaviour report received today
with onset unknown, state `present`, and change `uncertain`. `received_at`
anchors reporting-time analysis; it must never be relabelled as the behaviour's
onset. Missing onset reduces temporal-causality information but does not erase
the report.

Evidence kinds are descriptive rather than a permanent numeric hierarchy.
Weighting, if used, must be versioned and empirically tested; an attached image
is not automatically better evidence than a careful reproducible description.

## Governed behaviour vocabulary

The vocabulary is an append-only ledger. Every entry needs:

- a stable opaque ID;
- a short public label suitable for the cloud;
- a neutral definition and explicit exclusions;
- an organisational domain and reporting layer;
- a `broad` or `specific` coding scope;
- permitted current states, temporal changes, event states, and qualifiers;
- high-precision examples and counterexamples;
- creation time, status, and vocabulary version;
- provenance: seeded, discovered cluster, split, or superseded;
- aliases used for classification but never rendered publicly.

Meanings are never changed in place. A concept can be superseded by one or more
new concepts, but historical observations retain the ID and version under which
they were classified. Public labels can be corrected only when the definition is
unchanged and the change is recorded.

The ledger supports two concept shapes:

- **dimensions** describe behaviour that can vary, such as latency, warmth, or
  response length;
- **events** describe occurrences, such as leaking system-style text, adopting
  an unexpected persona, or producing a new class of malformed output.

Event concepts are first-class entries rather than awkwardly forced onto a
more/less scale. Their event state can be occurred, new, ceased, recurred, or
uncertain; their dimension state and change remain null.

### Hierarchy and unresolved specificity

A broad governed concept is a legitimate endpoint. "Keeps making mistakes" can
be coded to `correctness problem` without guessing factual versus reasoning
error. A narrower report is coded only to its supported child and rolled up for
parent-level analysis; storing both parent and child observations would double
count it. The current draft has codable broad parents for correctness problems,
unexpected sensitive-content intrusions, and product/delivery problems. Apparent
affect is also a broad open concept whose governed qualifier may remain absent.

Organisational siblings may overlap without being subtypes. Warmth, emotional
expressiveness, emotional attunement, playfulness, flirtation, and relational
intensity therefore remain co-codable siblings. The hierarchy must follow
meaning rather than forcing every correlated behaviour into a tree.

`unclassified` is a classification state, not a behaviour concept. It must never
appear in the public cloud as though it describes model behaviour.

### Provisional seed vocabulary

This is a design seed, not the final taxonomy:

| Dimension | Example canonical label | Typical change |
| --- | --- | --- |
| Responsiveness | latency | increase / decrease |
| Completion | truncation | increase / decrease |
| Elaboration | detail | increase / decrease |
| Instruction following | instruction completion | increase / decrease |
| Correctness | factual errors | increase / decrease |
| Reasoning | reasoning errors | increase / decrease |
| Coding | non-working code | increase / decrease |
| Writing | writing clarity | increase / decrease |
| Refusal behaviour | refusals | increase / decrease |
| Repetition | repetition | increase / decrease |
| Context handling | context loss | increase / decrease |
| Memory behaviour | remembered context | increase / decrease |
| Interaction style | warmth | increase / decrease |
| Interaction style | flirtation | increase / decrease |
| Agreement style | sycophancy | increase / decrease |
| Creativity | creative output | increase / decrease |
| Social perception | emotional attunement | state plus change |
| Affective presentation | emotional expressiveness / apparent affect | state plus change |
| Conversational style | formality / playfulness | state plus change |
| Interpersonal stance | assertiveness | state plus change |
| Task agency | initiative | state plus change |
| Relational behaviour | relational intensity | state plus change |

The governed ledger is definitive and additionally includes event concepts for
system-style text exposure, persona shift, moderation interception, unsolicited
sensitive content, platform availability, generation failure, suspected routing
mismatch, and tool/feature failure. Product and serving observations do not
silently increase a model-behaviour count.

Labels should describe the observable dimension. State and change supply
"present", "more", or "less" independently. This prevents us from creating a
permanently negative ontology while still allowing inherently concerning
observations such as factual errors to be named plainly.

### Conservative v0 migration

| v0 label | New treatment |
| --- | --- |
| `sluggish` | Map only high-precision latency language; otherwise unclassified |
| `lazy` | Do not map wholesale; reclassify as truncation, detail, or completion only when supported |
| `quality` | Unclassified appraisal unless the underlying report names a specific behaviour |
| `refusals` | Map to refusal behaviour when the text actually describes a refusal |
| `length` | Map to detail or response length with explicit state/change |
| `other` | Unclassified state |

Old aggregate counts remain viewable as preview history but never merge into a
new concept's statistical baseline merely because a rough mapping exists.

## Classification pipeline

Classification cannot become valence-balanced while ingestion remains
complaint-only. The current adapters call `looks_like_complaint()` and admit
negative hints such as "worse", "dumb", "lazy", and "broken" before the taxonomy
ever sees a report. Phase 1 therefore needs a valence-neutral candidate selector
for attributable model-behaviour observations, whether or not the post supplies
an onset date or explicitly claims a recent change. Comparative language,
temporal shifts, unexpectedly present or absent behaviour, and requests for
corroboration are useful signals of relevance, but they are not minimum validity
criteria.

### Development classifier and shadow mode

`structured-rules-v1-dev` implements the governed coding contract as a strict,
deterministic baseline. It is not wired into detector v0, storage, collection,
or public rendering. The read-only shadow runner evaluates the synthetic
development fixture and may inspect a retained SQLite database without writing
classified results back.

The reviewed fixture is a development contract, not a held-out accuracy set.
Matching it verifies implementation semantics only. A real performance estimate
requires newly sampled reports independently coded after the rules are frozen.

The shadow gate also audits attribution. A report naming multiple families or
exact models can carry valid behaviour observations but is not aggregate-ready
until each observation is tied to the correct model clause. Detector v0's
first-family routing is not inherited as ground truth.

Known and novel content may coexist in one report. Shadow output therefore keeps
private named novelty hints alongside governed observations rather than forcing
the entire report into either the known or unknown lane.

Behaviour-free praise and behaviour-free abuse are symmetrically outside the
weather stream. "Opus 5 is wonderful" and "Opus 5 is rubbish" contain valence
but no codable behaviour; "Opus 5 writes unusually clear prose" and "Opus 5 is
spiky" contain behaviour assertions even when onset is unknown. "Opus 5 wrote
this article" is ordinary chatter.

The internal name `Complaint` can remain temporarily for compatibility, but new
types and public methodology should use `ReportEnvelope` or `BehaviourReport`.
The migration must not pretend that renaming the class fixes the ingress bias.

The processing order is part of the scientific contract:

1. Route family and exact model conservatively.
2. Detect and collapse shared links, quoted text, near-duplicates, and repeated
   user submissions.
3. Extract candidate behaviour phrases inside the private boundary.
4. Map high-confidence candidates to governed behaviour IDs.
5. Extract current state and temporal change independently.
6. Classify reporter valence independently, including `mixed` and `unstated`.
7. Record evidence kind and classification provenance.
8. Send uncertain or novel candidates to the unclassified/novelty lane.
9. Build aggregate statistics from deduplicated structured observations.

The production classifier must support abstention. Forced classification is a
taxonomy-concealment mechanism: it makes the vocabulary appear complete by
putting uncertainty into the wrong buckets.

Classifier evaluation needs a human-labelled, versioned test set balanced across
models, sources, positive and negative valence, short and long texts, sarcasm,
quoted speech, and ambiguous comparison language. Report precision, recall,
abstention rate, and confusion by concept; one headline accuracy number is not
enough.

### Two collection lanes

Barometer uses discovery and surveillance as separate but connected lanes.
The operational sampling, probe-governance, cost, and counting contract is in
`PROBING_METHOD.md`. In particular, discovery chatter is bounded with sampling
and quotas rather than complaint-keyword preselection.

#### Discovery sample

Take a cost-capped, preferably stratified sample of model-related posts from each
approved source every day. Classify every sampled item as one of:

1. ordinary chatter with no model-behaviour observation;
2. a report mapped to an existing governed concept;
3. a report containing a candidate unknown concept;
4. ambiguous or insufficient for a decision.

Known concepts contribute observations with `received_at` set to the post/receipt
date and `claimed_onset_at` left null when the text gives no onset. Unknown
concepts enter the private novelty lane. One unfamiliar phrase can create a
candidate, but not a public bucket: promotion requires clustering or repeated
evidence, human definition and naming, and vocabulary-ledger entry.

#### Targeted probes

Run more frequent cost-capped searches from a versioned probe registry. Each
probe maps tested query terms to one or more governed concepts. Terms discovered
through sampling can be proposed for the registry only after review; a word such
as "spiky" must not be attached to refusals or temperament merely because the
first reviewer can imagine that meaning.

Targeted probes are efficient surveillance, but their raw counts do not estimate
general prevalence. Keep query version, sampling cap, inclusion probability where
known, and overlap provenance. Deduplicate the same source report across lanes
before counting it. A stable probe can support within-probe temporal comparisons;
combining discovery and targeted counts requires appropriate sampling treatment.

Multi-model comparisons are reviewed as linked source-by-target slices. The raw
source retains one provenance identity, but each explicitly mentioned model gets
independent behaviour, direction, and valence coding. Never copy a source-level
classification across all mentioned models: "A is warmer than B" is not the
same observation for A and B.

The feedback loop is:

`discovery sample → private candidate cluster → human promotion → probe registry → targeted surveillance`

### Ingress validation and collection health

Periodically hand-label random samples from the available source/query frame and
compare them with selector decisions. Monitor precision, sensitivity, abstention,
and admitted-versus-available valence distribution. Report these as collection
health alongside tap failures and saturation.

This audit describes only the material the source and our queries made available.
It cannot prove balance across posts an API never returned. Dashboard and methods
language must call it a coverage audit, not an unbiased sample of the platform.

X budgets are part of the method. Give discovery and targeted lanes explicit
daily allocations; fully collect narrow high-precision streams where affordable,
sample broad streams, and version the strata, caps, and weights. Ethical balance
that cannot survive its invoice is not an operational design.

## User-report intake

The current moderation and privacy boundary remains. The form should eventually
replace "problem category" with three separate structured questions:

1. What did you observe? — governed behaviour/event or "none of these".
2. Was it more, less, newly present, simply present, changed, or unsure?
3. How was that for you? — positive, negative, mixed, neutral, or prefer not to say.

Optional evidence questions can record whether the reporter has an example,
repeatable steps, an artifact, or a quantitative comparison. The free-text
description remains private and never becomes public cloud text.

Moderation must preserve both the reporter's original selections and the
reviewer's canonical coding. Corrections are appended; they do not silently
overwrite what the reporter submitted.

## Novel behaviour detection

Novelty has two linked alarms.

### Unclassified-share alarm

For every model and for the fleet, monitor:

`deduplicated unclassified reports / deduplicated eligible reports`

Compare that proportion with its own trailing baseline and the contemporaneous
fleet. Require minimum numerator, denominator, and reporter/source diversity
before alerting. A single unfamiliar phrase must enter review without producing
a public novelty signal.

The alarm means "our vocabulary may be missing something", not "the model has a
new behaviour". Classifier-version changes, new sources, and release-day media
attention are explicit confounders.

### Private open-vocabulary lane

Nightly, once there is enough volume:

1. redact obvious personal or sensitive material;
2. extract descriptive candidate phrases from unclassified reports;
3. embed locally or through a separately approved, privacy-reviewed processor;
4. cluster within a time window;
5. measure cluster growth, model specificity, source diversity, and cascade
   concentration;
6. place qualifying clusters in a private naming queue.

The queue shows aggregate diagnostics and deliberately accessed private examples.
It proposes a provisional definition, but a human keeper names or rejects the
concept. Naming creates a new vocabulary-ledger entry. Nothing from this lane is
published until promotion.

Raw phrases age out with the raw-report retention policy. If a cluster cannot be
reviewed before its evidence expires, the system may retain non-identifying
aggregate diagnostics and a centroid only if the privacy policy explicitly
allows it; it must not retain disguised quotations indefinitely.

Concept promotion adds reviewed probe candidates but does not automatically
activate or spend against them. Query precision, ambiguity, expected volume, and
cost are tested before a probe version becomes active.

## Statistical signal engine

Barometer needs two complementary axes.

### Temporal axis

Ask whether behaviour B is unusually reported for model M compared with M's own
history. The current burst detector is an MVP version of this axis. Its eventual
replacement must account for:

- sparse and changing baselines;
- release-day and media-stimulated reporting;
- source arrival patterns and collection outages;
- exact-model age and serving availability;
- classifier and vocabulary version changes.

### Cross-sectional axis

Ask whether behaviour B is disproportionately reported for model M compared with
other models during the same period. Build the report-level 2×2 table after
cascade deduplication:

| | Behaviour B present | Behaviour B absent |
| --- | ---: | ---: |
| Model M | a | b |
| Comparator fleet | c | d |

Each report contributes once to the table. Source, surface, language, geography
if ever collected, and model-release strata are possible confounders; subgrouping
must be justified empirically because sparse strata can worsen false positives.

PRR and ROR are useful transparent diagnostics, not final truth. Production
ranking should evaluate a shrinkage method such as BCPNN Information Component
or empirical-Bayes geometric mean so low-count cells are pulled toward the null.
The method and alert threshold will be chosen through replay evaluation, not by
aesthetic preference.

The fleet comparator cannot remove all discourse waves. Coverage differs by
model, platform, user population, and source query quality. Barometer must show
the observed and expected counts, uncertainty interval, contributing source
types, and low-diversity warnings beside any statistical score.

### Candidate signal gate

A behaviour/model pair enters human review only when it passes versioned rules
covering:

- minimum deduplicated report count;
- minimum source or reporter diversity;
- cross-sectional disproportionality with uncertainty;
- temporal elevation or a documented reason it is unavailable;
- cascade concentration and collection-health checks;
- synthetic/legacy exclusion.

A statistical signal is a review priority. It is not automatically public
weather and never automatically becomes a causal claim.

### Replay without pretending history is ground truth

Confirmed historical model-behaviour signals are too few and inconsistently
documented to calibrate the engine alone. Replay evaluation therefore injects
clearly flagged synthetic patterns into copies of real historical streams:

- isolated low-count reports;
- slow accumulation and sudden bursts;
- one cascade repeated across sources;
- genuinely independent cross-source reports;
- fleet-wide discourse waves;
- model-specific positive, negative, and mixed behaviours;
- event-shaped novelty clusters.

Vary size, duration, source diversity, query lane, and cascade structure, then
measure detection delay, sensitivity, false alarms, and ranking stability. The
synthetic provenance flag is mandatory and injected observations can never enter
production storage, baselines, public counts, or weather.

## Signal lifecycle and causality evidence

Use an auditable lifecycle:

`candidate → under review → validated signal → monitored → closed`

Validation records supporting and weakening evidence. Later phases can add:

- temporal alignment with releases, incidents, and serving changes;
- consistency across independent communities and surfaces;
- objective canary drift where the method is appropriate;
- provider acknowledgement;
- dechallenge: reports decay after a relevant intervention;
- rechallenge: the pattern returns after a later relevant change;
- plausible alternative explanations and collection artifacts.

WHO-UMC terminology is an inspiration for structured reasoning, not a label set
to copy onto models. Causality grading cannot turn uncertainty into certainty or
prove a connection from observational reports.

## Public weather and cloud contract

The public cloud receives only active governed concepts. It never receives raw
phrases, rejected concepts, cluster labels awaiting review, or `unclassified`.

- frequency controls text size, opacity, and centrality;
- valence distribution can later influence colour or warmth;
- behaviour detection remains valence-blind;
- mixed or low-count valence remains visually uncertain rather than averaged
  into a false verdict;
- weather describes a time-bounded aggregate signal, not model character;
- no reports in the selected window means no cloud and no words;
- a lack of unusual signal may be called clear weather even when ordinary reports
  exist, provided the UI distinguishes "no signal" from "no reports".
- every public view labels its attribution level as family-wide or exact-model;
  sparse variant weather must not look like a claim about the whole family, or
  vice versa.

Positive states can include warm front, clear spell, or sunny intervals. Negative
and mixed states can include rain, storm, fog, overcast, or changeable conditions.
The vocabulary and thresholds for these states must be documented separately
from the statistical detector.

## Privacy and publication thresholds

The existing private/public separation remains load-bearing:

- external raw posts follow the approved retention policy;
- user descriptions and moderation notes never enter detector text or public
  output;
- approved user reports contribute only reviewed structured observations;
- arbitrary novelty phrases remain private until governed promotion;
- public JSON contains aggregate counts and method metadata only;
- sparse aggregates are reviewed for re-identification risk before launch.

Canonical one-count concepts are safer than one-count quotations, but they can
still reveal information when combined with model, source, and exact time.
Whether a public count of one is acceptable is a launch privacy-policy decision,
not a conclusion embedded permanently in the cloud renderer.

## Delivery phases

### Phase 0 — contract and fixtures

- Approve this architecture and the initial vocabulary governance rules.
- Build a small human-labelled evaluation fixture containing positive, negative,
  mixed, ambiguous, undated-onset, chatter, and novel observations.
- Define the discovery sampling frame, targeted probe registry, coverage-audit
  sample, and per-lane X budget.
- Design synthetic-injection replay scenarios and metrics.
- Mark all retained synthetic/legacy data explicitly.

### Phase 1 — ethical structured core

- Add the versioned vocabulary ledger.
- Add structured behaviour, state, change, valence, attribution, evidence, and
  provenance types. **Implemented as a non-active coding contract.**
- Support both dimension and event concepts.
- Replace complaint-only source admission with valence-neutral behaviour-report
  selection and balanced fixtures.
- Add separate discovery-sample and targeted-probe provenance with deduplication.
- Add coverage-audit collection-health metrics.
- Introduce abstaining classification behind a feature flag.
- Add a deterministic classifier and read-only shadow evaluation before feature
  flag integration. **Implemented; not activated.**
- Update user intake and moderation without weakening the private boundary.
- Add unclassified-share metrics and positive/mixed weather states.
- Keep taxonomy-v0 as the default public detector until replay tests pass.

### Phase 2 — novelty lane

- Add private phrase extraction and local embedding evaluation.
- Add nightly clustering, growth/model-specificity diagnostics, and naming queue.
- Add append-only promotion, rejection, split, and supersession records.
- Add reviewed, cost-tested probe proposals from promoted concepts.

### Phase 3 — disproportionality

- Build deduplicated report-level comparator tables.
- Implement transparent PRR/ROR diagnostics.
- Compare BCPNN IC and EBGM-style shrinkage through historical/synthetic replay.
- Select calibrated thresholds and publish uncertainty and evidence diagnostics.

### Phase 4 — longitudinal assessment

- Add change-point detection and provider-event alignment.
- Track dechallenge/rechallenge and signal lifecycle.
- Add empirically validated evidence weighting and brigade-resistance indicators.

No phase is activated merely because its code exists. Live collection, paid
processing, and scheduling remain separate operational decisions.

## Acceptance criteria for the first implementation slice

1. `quality`, `lazy`, and `other` no longer enter new statistics as if they were
   precise behaviours.
2. Positive and mixed source fixtures pass candidate selection and classify
   without being forced into a complaint bucket.
3. A behaviour report with unknown onset remains eligible and retains distinct
   receipt and onset fields.
4. Behaviour and valence can disagree without data loss.
5. Dimension and event concepts both survive round-trip storage.
6. Reports can be co-coded, while hierarchy roll-up cannot double-count a child
   as a separately observed parent.
7. Broad parent coding is valid and never forces unsupported specificity.
8. Every new public-source observation begins as `reported`; causal layer may
   remain unknown.
9. Deliberately adversarial elicitation cannot enter ordinary-use signals.
10. Uncertain reports abstain and increase the unclassified-share metric.
11. Discovery and targeted duplicates count once and retain query provenance.
12. Existing raw user descriptions still cannot cross the moderation boundary.
13. Synthetic/legacy observations cannot enter the new baseline.
14. Public output contains governed IDs/labels and aggregates, never raw phrases.
15. The current detector remains available for side-by-side replay until the new
   pipeline is validated.

## Methodological references

- [EMA GVP Module IX: Signal management](https://www.ema.europa.eu/en/documents/scientific-guideline/guideline-good-pharmacovigilance-practices-gvp-module-ix-signal-management-rev-1_en.pdf)
- [EMA GVP Module IX Addendum I: Statistical signal detection from spontaneous reports](https://www.ema.europa.eu/en/documents/scientific-guideline/guideline-good-pharmacovigilance-practices-gvp-module-ix-addendum-i-methodological-aspects-signal_en.pdf)
- [Uppsala Monitoring Centre: Standardised case causality assessment](https://who-umc.org/media/u4gjgxvv/who-umc-causality-assessment_new-logo.pdf)
- [Bate et al.: Bayesian neural networks with confidence estimations applied to data mining](https://doi.org/10.1016/S0167-9473(99)00114-0)
- [DuMouchel: Bayesian data mining in large frequency tables](https://doi.org/10.1080/00031305.1999.10474456)
