# The ceiling statement and the evidence-pack language

Build Plan action 11.3: written before either surface ships. These sentences are the product's honesty
artifact. They live in code at `packages/core/src/ceiling.ts` and every surface renders them verbatim;
`packages/core/test/ceiling.test.ts` fails if this document and the code drift apart.

## Ceiling statement (rendered on every result screen and in every monitoring report)

**What this does, and what it cannot do**

1. Cloaking is probabilistic and time-bounded. It targets specific model families, and its effect decays as models and defences change. Re-treat periodically: this is a maintained protection, not a permanent one.
2. Canaries do not decay. The invisible tag in this file stays detectable for as long as the file, or a copy of it, survives, so monitoring keeps working on everything you have ever tagged.
3. Nothing here removes your content from a model that has already trained on it.
4. Mark only treats content you own and publish. It never injects anything into anyone else’s system, and it has no feature that could.
5. Protection applies to copies you can replace. Copies already taken keep whatever protection they had when you published them.
6. We state which transformations the tag survives and which strip it. The robustness table is measured by our harness, not asserted.

## Unmeasured-cloak notice (rendered while a bundle's efficacy table is unmeasured)

This cloak version has not been benchmarked against any model panel yet. Treat it as untested: the durable protection in this file is the canary tag.

## Provenance is not accusation (rendered on every hit, alert and evidence pack)

A canary hit proves that content carrying your tag appeared at this location at this time. It does not by itself prove who put it there, why, or whether any law was broken. Naming a person or taking enforcement action goes through the platform’s process or counsel.

## Rules for editing

- Change the code first, then this file; the test tells you if you missed one.
- Never add a claim here that a harness or a bundle efficacy table does not back.
- The unmeasured-cloak notice is removed from a result screen only by a bundle whose `efficacy.measured` is true.
