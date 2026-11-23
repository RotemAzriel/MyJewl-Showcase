# Prong wire — live capture sheet

**Date:** 2026-08-18 · **Goal 0028** · **For:** a call or in-person conversation with an
experienced person (bench jeweler, caster, modelist) about how thin our metal is.

> **This is not a brief.** [`geometry_research_brief_2026-08-12.md`](geometry_research_brief_2026-08-12.md)
> is the brief, and it is written to be pasted into a research tool — seven multi-part
> prose questions you cannot read aloud. This page is the **paper form of the intake
> file**: every box maps 1:1 to a field in
> `profiles/manufacturing_evidence_undeclared_template_v0_1.json`, so transcribing
> afterwards is mechanical rather than interpretive.

## Before you start

- **Do not lead with our number.** Ask theirs first, then show ours. If you say
  "we're at 0.6 mm, is that OK?" you will get agreement, not information.
- **Their standing is part of the evidence.** Get their role and years — the intake
  refuses an unattributed answer, and "someone in the trade says" cannot be followed up.
- **You are collecting testimony, not calibration.** Nothing said here will turn a
  MyJewl gate green. It becomes one more *candidate* number for a casting shop to react
  to. That is not a limitation to apologise for; it is what keeps the brand rule intact.

---

## Q1 — Prong base diameter *(the single highest-value number)*

**Ask:** *"For a four-prong solitaire head holding a one-carat round — about a 6.5 mm
stone — what wire diameter would you expect at the base of each prong, where it meets
the gallery?"*

Let them answer before showing anything. Then:

**Show:** we are at **0.600 mm** there. We have seen **1.0–1.3 mm** quoted as the trade
standard at 1 ct, from a single unverified source. Our prong base does not reach 1.0 mm
until the centre stone is **10.8 mm (~4.5 ct)**.

**Follow up:** *"Is that a continuous function of stone size, or do you pick from stock
wire sizes?"*

| Capture | Value |
|---|---|
| Number or range (mm) | |
| Their exact words | |

→ `findings.min_prong_base_diameter_mm.range_mm` (or `.value_mm`) and `.verbatim`

---

## Q2 — Minimum castable wire, anywhere

**Ask:** *"Independent of prongs — what is the thinnest wire or section you can cast
reliably and still finish?"*

**Show the range table** ([`envelope_wire_sections_2026-08-17.md`](envelope_wire_sections_2026-08-17.md)) and ask
the Goal 0021 question: ***"which of these can you cast?"*** — not *"is 0.6 mm OK?"*

| Centre stone Ø | Prong wire | Prong base | Thinnest section |
|---|---|---|---|
| 3 mm *(our minimum)* | 0.157 | 0.277 | 0.144 |
| 6.5 mm *(reference, ~1 ct)* | 0.340 | 0.600 | 0.312 |
| 11 mm *(our maximum)* | 0.575 | 1.015 | 0.527 |

**The finding that may matter most:** the whole range sits below 1.0–1.3 mm. If the
standard holds, our problem is not the small end — it is all of it.

| Capture | Value |
|---|---|
| Number or range (mm) | |
| Their exact words | |

→ `findings.min_castable_wire_diameter_mm`

---

## Q3 — Minimum separation before features fuse

**Ask:** *"How close can two separate features sit before they run together in casting
and you lose the gap?"* Relevant to our four basket petals and the prong spacing.

| Capture | Value |
|---|---|
| Number (mm) | |
| Their exact words | |

→ `findings.min_feature_separation_mm`

---

## The four scope questions — ask these for EVERY number above

**These are not paperwork.** The intake refuses a number that lacks them, because
without them the figure is not comparable to what we ship. If you get one number and
these four qualifiers, that is a better result than five bare numbers.

| # | Ask | Field | Refusal if missing |
|---|---|---|---|
| 1 | *"Is that for **cast** work or **fabricated**?"* | `process_scope` | `process_scope_unknown` |
| 2 | *"Is that **before or after finishing**?"* — we ship finished-nominal, no shrinkage or stock allowance | `measurement_basis` | `measurement_basis_unknown` |
| 3 | *"Does it change by **alloy** — platinum vs 14k vs 18k?"* | `alloy_scope` | *(recorded as-is; empty means they did not distinguish)* |
| 4 | *"Is that a published standard, your shop's rule, or your own judgement?"* | `evidence_class` | `evidence_class_unknown` |

Plus, for the record as a whole: **their name and role**, and **today's date**.
Missing either is refused as `attribution_missing` / `undated_evidence`.

---

## If they contradict us

The brief's own rule (Part 3, item 4): anything contradicting §1.1 should **trigger
re-measuring, not quiet reconciliation**. If they say 1.2 mm and we ship 0.6 mm, the
answer is not to nudge a constant — it is that F3 is confirmed and the geometry
re-solve (Goals 0019/0022, both deferred and batched on purpose) has its input.

## Afterwards — transcribing

```bash
cp profiles/manufacturing_evidence_undeclared_template_v0_1.json /private/evidence.json
# fill in the findings you got; set the record status AND each answered finding's
# status to "declared"; leave the ones you did not ask as "undeclared"
MYJEWL_MANUFACTURING_EVIDENCE=/private/evidence.json python3 -m scripts.sweep_envelope
```

A declared finding appears as a new **uncalibrated** row in the floor-sensitivity
table, carrying who said it and when. It does not calibrate a threshold, does not
supersede a shop profile, and moves no gate — `manufacturing_evidence.CALIBRATES` is
`False` and a test asserts there is no path around it.

**Leave unanswered findings `undeclared`.** A blank is a true statement about what you
learned; a guess is not.
