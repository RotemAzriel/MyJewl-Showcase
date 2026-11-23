# The quote basis — which volume MyJewl prices, and why

**Goal 0023, 2026-08-17.** Spend **$0**. No geometry, GHX, or `master_parameters` change.

Until today `fast_mvp/typed_inputs.py:quote_basis` returned `pending_geometry` for **every**
configuration, so MyJewl could not state a price at all. It now states one for the four promoted
configurations and only those. This document records the decision that makes that number defensible,
because the number is easy and the decision is the hard part.

---

## The one-line rule

> **The quoted volume is the measured mesh boolean union of the exact metal STL set the promoted asset
> was built from.** Never the naive parts sum. Never a neighbouring configuration's volume. Never a
> number supplied by the caller.

Declared once, in data: `fast_mvp/preview_assets/measured_volumes_v0_1.json` carries
`quote_volume_basis: "measured_mesh_union"`, and `schemas/measured_volume_table_v0_1.schema.json` pins
that with `const`. `fast_mvp/quote_volumes.py` maps exactly one basis name to exactly one field, so
`parts_sum_mm3` is not reachable as a basis by configuration — only by editing code on purpose.

## The three candidate numbers

For the promoted control configuration (US 6.5, round 6.50 × 6.50 × 3.90 mm):

| Candidate | Value | Verdict |
|---|---|---|
| Naive sum of the 19 metal parts | **173.186 mm³** | **Rejected.** Over-counts by **6.75%** |
| Mesh boolean union (manifold3d) | **162.234 mm³** | **This is the basis** |
| Rhino NURBS boolean union | *does not exist for any promoted asset* | Unavailable — see below |

### Why not the parts sum

The parts interpenetrate by design — that is how the ring is built — so summing them double-counts metal
at every overlap. The over-count runs **6.716% – 6.969%** across the four promoted configurations, and
`manufacturing_assumptions.CONVERGENCE_TOLERANCES` sets the agreed `metal_volume_mm3` tolerance at
**±3%**. The naive sum is therefore *out of tolerance on every configuration we ship*, and it errs in the
direction that costs the customer money: at 18k yellow the control ring's over-count is **0.170 g of
metal that does not exist.**

This is not a hypothetical. Before today no quote existed, so nothing was mis-priced — but the parts sum
is the number that falls out of a bundle most easily, and it is the number a future contributor would
reach for. Hence the schema `const` and the test that asserts the basis is never the sum.

### Why not Rhino's NURBS union

Rhino is the geometry authority and its NURBS boolean is, in principle, the more accurate figure. It is
not the basis for one decisive reason: **it does not exist for any promoted asset.** The four production
assets are V4 and V5, and neither definition contains a boolean operation anywhere — that absence is
finding **F2**. Their receipts carry no volume field at all.

Adopting the NURBS union would therefore mean quoting a V5 ring from a **V6** number. V6 is measured,
correct, and sitting on disk unpromoted (Goal 0019, deferred). Quoting one definition version from
another's geometry is the same class of substitution Goal 0014 closed for previews, and it is worse for a
price than for a picture.

Where both numbers exist — the V6 control bundle — they agree closely:

| | Value | Delta |
|---|---|---|
| Rhino NURBS union (V6 receipt) | 163.283 mm³ | — |
| manifold3d mesh union (measured) | 162.234 mm³ | **+0.647%** |

Both are comfortably inside ±3%, so the choice is **not material to price** — it is material to
reproducibility, which is exactly why it has to be declared rather than left to whichever number happened
to be loaded.

### Why the mesh union is the *conservative* choice

Tessellation inscribes the NURBS surface: a triangle chord cuts inside a convex curve, so the mesh volume
is a **lower bound** on the true solid. The +0.647% gap above is that bias, not disagreement. A lower
volume is a lower weight is a lower price, so any residual error is **in the customer's favour** — the
exact opposite of the parts sum.

It also has three practical properties the NURBS number does not:

1. **It exists for every promoted asset**, present and future.
2. **It is reproducible offline for $0**, by us in a test and by anyone else with the bundle.
3. **It measures the bytes we actually ship.** The generator verifies every metal STL against the
   receipt's recorded `sha256` before measuring, so the volume cannot describe a file Rhino did not emit
   and the registry did not promote. All four records report `part_bytes_verified == metal_part_count`.

**When V6 is promoted this should be revisited, not inherited.** V6 emits a real `castable_solid`, so a
Rhino union will exist for a served asset for the first time. The record already has a
`rhino_nurbs_union_mm3` slot to carry it as a cross-check; promoting it to the basis is a deliberate
change to `quote_volumes.QUOTE_VOLUME_BASIS` and the schema `const`, not a silent one.

## The measured table

`fast_mvp/preview_assets/measured_volumes_v0_1.json`, regenerable in about ten seconds for **$0**:

```bash
python3 -m scripts.goal0023_measure_quote_volumes            # dry run, prints the table
python3 -m scripts.goal0023_measure_quote_volumes --write    # write it
```

| Asset | Typed configuration | Union (basis) | Parts sum | Over-count |
|---|---|---|---|---|
| `goal0011-v4-full-ring` | US 6.5 · round 6.50 mm | **162.207 mm³** | 173.158 mm³ | +6.751% |
| `goal0014-v5-us4_round5` | US 4 · round 5.00 mm | **140.652 mm³** | 150.454 mm³ | +6.969% |
| `goal0014-v5-us6p5_round6p5` | US 6.5 · round 6.50 mm | **162.234 mm³** | 173.186 mm³ | +6.751% |
| `goal0014-v5-us9_round8` | US 9 · round 8.00 mm | **185.935 mm³** | 198.423 mm³ | +6.716% |

Every union is watertight and a **single body**, so the ring would cast as one piece. The V4 and V5
US 6.5 rings differ by **0.027 mm³ (0.017%)**, an independent confirmation that Goal 0014's parametric
reproduction of the approved V4 shape landed where it claimed.

Resulting metal weights, for reference:

| Alloy | US 4 / 5 mm | US 6.5 / 6.5 mm | US 9 / 8 mm |
|---|---|---|---|
| 14k yellow (13.1 g/cm³) | 1.843 g | 2.125 g | 2.436 g |
| 18k yellow (15.5 g/cm³) | 2.180 g | 2.515 g | 2.882 g |
| Platinum 950 (20.7 g/cm³) | 2.912 g | 3.358 g | 3.849 g |

Metal is a typed input that never touches geometry (settled decision 6): the same volume is quoted for
every alloy, and only the density changes. A test pins that.

## The refusals

`quote_volumes.volume_for_manifest` returns `None` — leaving the quote at `pending_geometry` — unless
**all** of the following hold. Each has an adversarial test in
`tests/test_goal0023_quote_basis.py`.

| Condition | Why |
|---|---|
| `preview_status == "exact"` | An `estimated` preview approximates a *picture*. Approximating a *price* is a different and worse promise, so `estimated` is excluded even though production currently holds zero of them. |
| The geometry signature is in the table **verbatim** | No nearest-neighbour, no interpolation between solved sizes, no default. US 5 sits between two solved sizes and inside the master's envelope; it is not quoted from either. |
| The selected asset's **id and version** match the record | A table that has drifted behind the registry quotes nothing rather than pricing a retired asset. |
| A table exists | A missing table quotes nothing and does not raise. |

**Promoting an asset does not make it quotable.** The two artefacts are separate on purpose — the
registry says what can be *shown*, the table says what can be *priced* — so a promotion without a
measurement previews correctly and stays `pending_geometry`. Two things keep that from being silent:
`scripts/goal0015_promote_v5.py` now reports `assets_without_a_measured_quote_volume`, and a test asserts
the table covers exactly the promoted approved exact assets, so a promotion without a regeneration turns
the suite red.

## A volume can never come from the request

`/api/propose` and `/api/propose-from-image` previously accepted `ring_volume_mm3` **from the request
body** and priced the ring from it. That is worse than substituting a neighbour's volume, because it is
fabricated rather than merely adjacent. Both now ignore it.

The quote is re-derived at the same authoritative point that already recomputes the PreviewManifest —
`workflow.persist_proposal` — for the same reason: a client must not choose which asset represents its
ring, and it must certainly not choose the number that becomes a price. Everything a caller writes into
the quote is discarded there except `metal_price_per_gram`, which is a commercial input the caller
legitimately owns.

## What is still missing, and stated as missing

- **No alloy price ships.** `metal_content_cost` stays `null` and the quote says
  *"the metal content is unpriced because no alloy spot price was supplied. No price was invented."*
  A caller-supplied `metal_price_per_gram` prices the measured weight immediately; a dated, declared
  price sheet is the obvious next step and deliberately not invented here.
- **Metal content only.** Stone, labour, finishing, setting, and margin are excluded, and the exclusion
  list travels in every quote.
- **Densities are nominal trade figures**, not a shop's alloy recipe. Stated in the quote.
- **The quote describes the exported geometry**, which is what a shop would receive. Finding **F6** — the
  prong tip bead exports as a uniform sphere rather than the flattened bead the source builds — therefore
  affects the *intended* ring's volume, not this measurement. When F6 is fixed the exports change and the
  table must be regenerated.

## What this does not do

A price is a commercial estimate from measured geometry. It is **not** a manufacturing verdict.

- No geometry gate moves. Every quote carries
  `manufacturing_state: "pending_not_run_needs_calibration"`, and the volume evidence carries
  `authority: "measurement_only_not_manufacturing_certification"`.
- **F3 is untouched and unaffected.** Wire sections are still below the trade band across the whole
  envelope ([`envelope_wire_sections_2026-08-17.md`](envelope_wire_sections_2026-08-17.md)). A ring can be
  measured, weighed, and priced and still be a ring nobody should cast. Volume and section are different
  questions, and only the shop and the modelist can close the second one.
- **F1 and F2 do not invalidate this.** They are orientation and topology defects; the union volume is
  correct regardless of which way the normals face. `geometry_metrics._union` repairs winding on an
  in-memory copy purely so the boolean is computable.
