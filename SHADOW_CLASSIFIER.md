# Shadow classifier

Status: development-only, deterministic, read-only, and disconnected from the
public detector. Running it performs no collection, model calls, API spending,
scheduling, database writes, or dashboard changes.

## What it does

`structured-rules-v2-neutral-axes` applies the provisional vocabulary to one
report and returns:

- eligibility: behaviour report, novel candidate, chatter, uncodable appraisal,
  ambiguous, or excluded adversarial evidence;
- zero or more co-coded governed observations;
- separate current state, temporal change, and reporter valence;
- broad versus specific coding;
- reported/corroborated/attributed claim status;
- suspected model, product, serving, tool, or unknown layer;
- elicitation context;
- private novelty hints that can coexist with known observations.

It deliberately abstains from broad claims such as “quality has tanked.” A broad
neutral parent such as “general correctness” is still a valid code when a report
asserts mistakes but does not support factual versus reasoning specificity.

## Attribution gate

The old router selected the first recognised family. The shadow classifier does
not trust that decision for aggregation. Reports naming multiple families or
multiple exact models, or whose stored family conflicts with the text, require
attribution review. Their behaviour concepts remain inspectable but do not enter
the aggregate-ready concept counts.

Clause-level observation-to-model attribution is not implemented yet. Until it
is, conservative review is safer than colouring the wrong model card.

## Run it

Synthetic development contract only:

```powershell
python shadow_classifier.py
```

Also classify a retained local database through a SQLite read-only connection:

```powershell
python shadow_classifier.py --db barometer.db
```

Output is JSON on stdout. Raw retained text is not included in the summary.

## Interpreting the score

The 40 synthetic cases are the development contract used to write the rules.
A perfect match proves that the implementation follows those reviewed examples;
it is not a held-out accuracy estimate. Real-world evaluation needs a fresh,
randomly sampled, independently human-coded set that was not used to design the
rules or vocabulary.
