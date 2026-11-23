# MyJewl human-in-the-loop review protocol

**Status:** specified, not yet run. Written 2026-08-17 (Goal 0020) from the principles the operator set
that day. Data shape: [`schemas/review_record_v0_1.schema.json`](../schemas/review_record_v0_1.schema.json).

> **Rewrite this document in place** as stages are reached. It is a specification, not a log.

## The problem this solves

MyJewl offers **four** ring configurations. Not because the master can only build four — it can build
the entire envelope, and `master_parameters.derive_parameters` proves it — but because **promotion
requires a declared human visual review**, and there is exactly one reviewer.

That gate is correct and must not be weakened. `scripts/goal0015_promote_v5.py` refuses to promote a
configuration with no record in `fast_mvp/preview_assets/goal0014_v5_visual_reviews.json`, and that
refusal is the honest part of the system. The question is not how to remove the human. It is **how the
role scales, and how we know when it can be narrowed.**

## Five principles

1. **The reviewer never touches geometry.** They adjust *parameters* or reject *with a reason*. A
   correction expressed as "seat depth should be 0.4, not 0.3" is structured data the pipeline consumes
   and measures. A nudged surface in Rhino is knowledge that leaves with the person.
2. **Every decision is captured as data.** An un-instrumented human step means hiring modelists
   forever instead of building toward automation.
3. **The graduation metric is the correction rate.** If reviewers change nothing on 99% of rings in a
   class, that class is ready to automate. At 30%, it is not — and *what* they change is the
   prioritised fix list for the generator. This is what turns "are we ready?" into a number.
4. **Design against the rubber stamp.** A 100% approval rate means either a perfect generator or a
   reviewer who has stopped looking, and no amount of approval data distinguishes them. Seed known-bad
   configurations and confirm they are caught. **An uncalibrated reviewer is worse than none, because it
   manufactures false confidence.**
5. **Review measures form, never manufacturability.** `certifies_manufacturing` is `const: false` in the
   schema. No number of approvals becomes a manufacturing verdict; that still needs a calibrated shop
   profile *and* a jeweler.

## The four stages

Each stage narrows what the human looks at. **A stage is entered on evidence, not on schedule** — the
entry criterion is a measured correction rate on the stage below it, with probe calibration intact.

### Stage 1 — per configuration (where we are)
The reviewer judges one ring against a checklist, **with the measured number beside each item**. Every
promoted configuration needs its own record, or an explicitly recorded `inherited_from`.

- Baseline checklist: the seven Goal 0011/0014 checks (silhouette finished, no unconnected vertical
  rods, stone visibly seated and retained, shoulders/head/gallery read as one object, gallery negative
  space plausible, highlights preserve form, contact shadow believable).
- Cost: one review per configuration. **This is the bottleneck** — it is why coverage is 4.
- Exit criterion: correction rate below **10%** across at least **20** reviewed configurations spanning
  the envelope, with probe catch rate at or above **80%**.

### Stage 2 — approve the envelope
The reviewer approves **a parameter envelope** rather than every point in it: the two extremes, plus a
monotonicity check that nothing misbehaves in between.

**This is the stage that takes coverage from 4 configurations to all of them,** and it is the highest-
value unbuilt thing in the product. Goal 0014 already did the hard half — it reviewed both extremes
(US 4 / 5.00 mm and US 9 / 8.00 mm) and passed all seven checks on each. What is missing is the
monotonicity instrument: proof that every intermediate configuration interpolates without a defect
appearing.

- What must be built: a monotonicity check over sampled in-envelope configurations using
  `geometry_metrics` (wall thickness, volume, genus, orientation), asserting no non-monotonic excursion
  and no threshold crossing between reviewed extremes. **This is Goal 0022, deliberately DEFERRED** —
  it needs both a shop floor and the modelist's checklist, since the checklist determines what
  monotonicity has to check.
- Recorded as a `stage_2_envelope` record whose `configuration_id` names the envelope.
- **Caveat that must not be lost:** F3 means wall thickness scales linearly toward zero with stone
  radius, with **no floor anywhere**. Envelope approval is only sound once a floor exists. The envelope's
  small end is **0.157 mm** at a 3 mm stone (0.262 mm at the live `us4_round5`), and even its **maximum**
  is only **0.575 mm** — below the 1.0–1.3 mm trade standard the research brief flags. Approving that
  envelope would launder a known defect into blanket coverage. **Stage 2 is blocked on a shop-supplied
  minimum castable wire diameter.**

### Stage 3 — exceptions only
The reviewer sees only configurations the instrument flags. Requires that the instrument's flags
correlate with human rejections — measured, not assumed, by tracking how often a human rejects
something the instrument passed (false negatives are the dangerous direction).

### Stage 4 — automated in-envelope, human on edge cases
In-envelope configurations promote without review. Edge cases, new archetypes, and probe batches stay
human. Probes never stop.

## The metrics

Computed from review records, per `(template_id, stage, reviewer.role)` — rates are not comparable
across those axes:

| Metric | Definition | Meaning |
|---|---|---|
| **Correction rate** | reviews with ≥1 correction ÷ total reviews | the graduation number (principle 3) |
| **Rejection rate** | `rejected` ÷ total | how often the generator is not merely off but wrong |
| **Probe catch rate** | `probe_caught: true` ÷ seeded probes | whether the reviewer is still looking (principle 4) |
| **Parameter-gap rate** | corrections with `requires_new_parameter: true` ÷ corrections | the generator's missing degrees of freedom — **the most valuable entries in the dataset** |
| **Correction histogram** | count by `parameter` | the prioritised fix list |

**A correction rate is only readable next to a probe catch rate.** A 2% correction rate with a 20% probe
catch rate does not mean the generator is good; it means the reviewer stopped looking. Report them
together, always.

## Probe design

- Seed known-bad configurations into review batches at roughly **1 in 8**, **blinded**.
- Draw defects from findings we have actually measured, so probes test real failure modes: prong wire
  below the floor (F3), inverted normals (F1), un-unioned parts (F2), a stone seat that ignores the
  certificate (F5).
- Score `probe_caught` after the fact. A reviewer who misses probes has their approvals **discounted,
  not deleted** — the records stay, flagged.
- Probes are never promoted. A seeded probe must be refused by the promotion gate regardless of its
  review outcome.

## How this extends what exists

`goal0014_v5_visual_reviews.json` is a **manifest**: configuration → which review approved it. It stays
exactly as it is, and remains what the promoter reads.

This protocol adds the **record** the manifest points at. Today those targets are ad-hoc
`visual_review.json` files with a flat `checks` object and a boolean per item — enough to promote, not
enough to compute a correction rate, because there is nowhere to put a correction. The new schema is a
superset: the seven checks become `checklist` items that can now carry the measured number, and
`corrections`, `calibration`, and `stage` are the additions that make the metrics computable.

Migration is additive and needs no rewrite of history: existing records are readable as stage-1
approvals with an empty `corrections` array and `is_seeded_probe: false`.

## Then take it to the modelist

The question for the modelist is **"what would you check, in what order, and what would make you reject
it?"** That answer is simultaneously:

- the reviewer's checklist,
- the reviewer's training material,
- the instrument's requirements (each check needs a measurable number beside it),
- and the eventual automated gate.

It is asked in [`modelist_brief_2026-08-12.md`](modelist_brief_2026-08-12.md). Note what the schema
records honestly: **`reviewer.role` has never once been `modelist`.** Every approval MyJewl holds is
`operator`. That is the gap this conversation closes.
