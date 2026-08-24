# Probing method: wide enough to discover, narrow enough to operate

Status: design contract only. This document does not activate a source, make an
API request, schedule collection, or authorise spend.

## The central constraint

There is no single clever query that is both high precision and open to unknown
behaviour. Adding more complaint words reduces chatter by teaching the collector
what the answer must look like; removing them finds novelty but returns ordinary
model discussion as well.

Barometer therefore does not make one stream serve both purposes. It uses a
wide, bounded **discovery sample** and a narrow, versioned **targeted-surveillance
lane**. They may find the same report, but their collection provenance and
denominators remain separate.

Chatter in discovery is expected sampling cost, not a classifier failure. The
way to bound it is to cap and stratify the sample, not to pre-filter away unknown
behaviour.

## Lane A: discovery sampling

Discovery queries use only governed model and product identity aliases wherever
the source permits it. They do not require words such as `bad`, `broken`,
`refusal`, or any existing behaviour label.

For each approved source and collection period:

1. Define the available frame: source, query version, model aliases, language,
   time range, and any source-imposed ranking or pagination limit.
2. Allocate a hard returned-item cap across model families and time strata.
   Sample within each stratum where the API permits; otherwise record that the
   source supplied ranked results rather than calling them random.
3. Deduplicate before classification while retaining every query/lane that found
   the item.
4. Classify each retained item as governed behaviour, novel candidate, chatter,
   or abstention/insufficient evidence.
5. Give reviewed governed observations a received date even when the claimed
   behaviour onset is unknown. Unknown onset is not a reason to discard a
   spontaneous report.

A small per-family floor protects quieter models from being crowded out by the
most-discussed one. Unused quota can be redistributed only under a versioned
rule; silent opportunistic redistribution would change the sample over time.

Within that budget, exact release aliases and model-line aliases may receive a
larger share than broad product names when reviewed yield supports it. Standalone
lab/company names should not be used merely to increase volume. A small capped
broad-product stratum remains necessary for coverage and novelty discovery; an
exact-model-only frame would preferentially sample technical users and silently
miss reports from people who know only the product name. The allocation is a
versioned, measured sampling choice rather than an undocumented query tweak.

Discovery supports novelty detection and a sampled estimate of the share of
available model conversation containing reportable observations. It does not
represent the whole platform when the source API exposes only a ranked or
otherwise restricted frame.

## Lane B: targeted surveillance

Targeted queries come from a governed probe registry. Each probe has:

- a stable ID and version;
- source and model aliases;
- the exact query or query family;
- governed concept IDs it is intended to retrieve;
- known ambiguity and exclusions;
- activation and retirement dates;
- returned-item cap and cost ceiling;
- review-sample precision, overlap, and saturation measurements;
- lifecycle state: proposed, offline-tested, pilot, active, paused, or retired.

Probes should use behaviour language in both directions where applicable: for
example, warmth gained and warmth lost, not a negative-only list. A candidate
phrase such as `spiky` is never silently mapped to temperament or refusals. It
first goes through human coding and offline review.

Targeted results are query-conditioned surveillance. They can show change within
the same stable probe over time, but their raw counts are not prevalence and
must not be compared casually with counts from another concept that had a
different number or breadth of probes.

## The safe adaptive loop

The collector does not automatically turn every novel phrase into a paid query.
The promotion loop is:

`bounded discovery → private phrase candidate → human review → retrospective`
` test on retained samples → small supervised pilot → versioned active probe`

The retrospective test asks whether the candidate retrieves genuine reports,
ordinary chatter, quotations, fandom slang, or unrelated meanings. A pilot has
a fixed cap and expiry. Activation requires an explicit decision; classifier
output alone cannot spend money or alter the measurement frame.

This middle step lets the targeted lane learn new language without allowing one
odd post to rewrite collection policy.

## Narrowing chatter without closing the net

Apply these controls in order:

1. **Cap discovery volume.** Preserve breadth, limit cost.
2. **Stratify by family and time.** Prevent one launch or popular model from
   consuming the whole sample.
3. **Classify locally after retrieval.** API query syntax remains valence-neutral.
4. **Deduplicate query overlap and cascades.** More matching probes do not create
   more reports.
5. **Spend the remaining budget on proven probes.** Precision belongs in the
   surveillance lane, not as a gate on discovery.
6. **Audit the exclusions.** Human-review samples from chatter and abstentions,
   not only admitted reports, so classifier blind spots remain visible.

An initial supervised pilot may reserve roughly one third of returned items for
discovery and two thirds for targeted probes, with human audit drawn from both.
That is a starting hypothesis, not a production constant. Adjust it using yield,
novelty discovery, saturation, and cost rather than aesthetic preference.

## Metrics that decide whether the balance is working

Record by source, family, lane, and query version:

- returned candidates and unique candidates after overlap deduplication;
- governed-report, novelty, chatter, and abstention shares;
- human-reviewed precision and false-exclusion rate;
- new candidate clusters per 100 discovery items;
- cost per unique governed report and per reviewed novel candidate;
- query saturation and unavailable-result warnings;
- family, valence, and concept mix before and after classification;
- cross-lane overlap and cascade collapse;
- age of the last manually audited sample.

High discovery chatter is tolerable when novelty yield and audit coverage justify
it. Near-zero discovery chatter is suspicious: it may mean the supposedly open
sample has become an undocumented complaint query.

## Counting rules

- One source report keeps one stable provenance identity after stable-ID and
  cascade handling, even if multiple lanes or probes retrieved it.
- A comparison may create one coding slice per explicitly mentioned model. Each
  slice can carry different behaviour direction and valence. It contributes at
  most once to each model/behaviour cell; fleet-wide unique-source totals count
  the shared source once rather than summing its model slices.
- Keep all collection provenance on the deduplicated record.
- Never add targeted and discovery counts and present the sum as platform
  prevalence.
- Do not compare models without showing material differences in source coverage,
  caps, saturation, and probe breadth.
- A missing onset date remains `unknown`; receipt date is day zero.
- Synthetic, legacy, and development fixtures never enter live baselines.

## Implementation order

1. Finish the human review queue and label a real, bounded retained sample.
   **Completed for the first development batch.**
2. Add collection-provenance fields and a versioned probe-registry schema, still
   disconnected from live adapters. **Implemented as an inactive storage and
   governance contract; the checked-in registry contains no probes.**
3. Replay retained data to estimate discovery yield, probe overlap, and chatter.
   **Aggregate review replay is implemented. Legacy rows lack query-run
   provenance, so their chatter/yield is measurable but discovery attribution
   and overlap correctly remain unavailable.**
4. Propose a costed per-source pilot budget for explicit approval.
5. Only then add supervised live collection flags; scheduling remains a separate
   decision.
