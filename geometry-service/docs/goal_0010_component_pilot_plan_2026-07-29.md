# Goal 0010 — solitaire configurator and premium 3D product preview

**Status:** complete (2026-07-30)

## Working definition

This document carries the completed product-facing Goal 0010 contract. The
user supplied the Blue Nile solitaire configurator as a visual
and interaction reference:
<https://www.bluenile.com/engagement-rings/custom-engagement-rings/solitaire-engagement-ring-4-prong-nouveau-head-item-293922>.
It is reference-only; MyJewl must not copy retailer assets, IDs, names,
dimensions, copy, or product data.

This is a bounded MyJewl internal catalog revision plus a visual-quality and
provenance gate. It is not a retailer-catalog import and not a
manufacturing-certification milestone.

## Objective

Build a MyJewl-owned solitaire configurator surface with the same level of
clarity and polish as a premium jewelry product page:

- a three-step journey for setting, diamond, and completed ring;
- a large editorial 3D preview with drag/360 interaction and stable framing;
- visual tiles for diamond shape, internal head families, and internal band
  families;
- typed metal colour, purity, and finish controls;
- deterministic compatibility filtering and canonical mapping; and
- a final design summary that can be reviewed or explicitly queued for
  operator-controlled Rhino/Azure geometry work.

The bounded shape set for this phase is round, oval, cushion, pear, emerald,
princess, and radiant. Additional shapes require an explicit contract and a
shape-appropriate versioned preview profile before exposure.

## Evidence boundary

The current Azure Rhino master under
`output/goal_0009_photoreal_solitaire_20260729/azure_v2/` is derived evidence
only. Its receipt, OBJ, STL, GLB, and Cycles renders are comparison inputs; they
are not a source of authoritative product geometry and do not certify
manufacturability.

Goal 0010 must not:

- promote the Goal 0009 v1/v2 geometry without explicit visual approval;
- copy a retailer's model, dimensions, asset, or product specification;
- use Blender, GLB, or browser meshes as manufacturing authority;
- start Azure, RunPod, or paid authoring/rendering without fresh operator
  approval; or
- mark any geometry gate `pass` without a calibrated shop profile and jeweler
  review.

The browser preview is non-certifying evidence. Rhino/Grasshopper remains the
sole geometry authority. Blue Nile is a visual reference, not a source of
MyJewl catalog data.

## Delivered product and retained pilot evidence

- `manual/tools/build_goal0010_component_pilot_ghx.py` is a locally authored
  RhinoCommon candidate source with a lower/open basket, shoulder sweeps that
  begin at the shank contact, and curved tapered prongs. It preserves the
  fixed 20-part taxonomy and the stable `RH_IN`/`RH_OUT` contract.
- `manual/rhino_compute_bridge/goal0010_component_pilot_v1.ghx` is the locally
  generated GHX derivative of that source. It is a candidate, not an accepted
  visual master.
- `scripts/goal0010_component_pilot.py` performs a local provenance and
  component-identity audit over a Rhino-derived Goal 0009 receipt/master,
  including receipt-listed STL existence/hash checks and fixed-pose render
  receipt provenance.
- The audit reports missing/extra named parts, receipt closure metadata,
  material-role separation, artifact hash status, and the exact
  external-authoring blocker. It emits `visual_gate: not_run` and
  `manufacturing_gates:
  pending_not_run_needs_calibration` deliberately.
- `tests/test_goal0010_component_pilot.py` protects the non-certifying report
  contract and the bounded part taxonomy.
- The candidate was executed on the approved Azure Rhino Core VM and the VM
  was confirmed `deallocated` after the run. The received receipt contains 20
  named parts; all 20 receipt-listed STL hashes match and all 20 parts are
  closed after the stone-mesh repair.
- `output/goal_0010_component_pilot_20260730/cycles_reference/` contains a
  fixed-pose 768 px Cycles render plus LOD GLB comparison outputs. These are
  derived review evidence only.
- `fast_mvp/manual_configurator.py` now exposes catalog revision
  `modular-rings-v3`. All seven bounded shapes resolve to compatible internal
  heads and bands with explicit IDs, compatibility metadata, and canonical
  mappings. The exhaustive contract test accepts all 50 returned
  shape/head/band combinations without substitution.
- `fast_mvp/static/preview_profiles_v2.js` adds seven versioned, visual-only
  shape profiles and purity-aware material presets without changing the
  published Goal 0008 v1 profile contract.
- `fast_mvp/static/ring3d.js` uses typed L x W x D proportions for the WebGL
  and deterministic 2D paths, bounds the camera from the rendered ring,
  supports pointer and keyboard rotation plus opt-in continuous auto-rotation,
  and exposes read-only diagnostics for browser verification.
- `fast_mvp/static/studio.html`, `studio.js`, and `studio.css` deliver one
  semantic three-stage journey, responsive selectable tiles, selection
  persistence, current-step error surfaces, material controls, and a final
  non-certifying design brief.
- `tests/test_goal0010_configurator_catalog.py` and
  `tests/test_goal0010_product_preview.py` protect the seven-shape catalog,
  geometry/material identity boundary, renderer, fallback, interaction,
  accessibility, and product-flow contracts.

## Acceptance record

1. **Met.** The Studio presents a premium product-configurator layout with a clear
   three-step progress model, editorial preview stage, responsive option
   tiles, and an obvious final review action.
2. **Met.** All seven bounded diamond shapes are selectable and produce distinct,
   shape-appropriate preview silhouettes; no supported shape is marked “soon”.
3. **Met.** Compatible internal heads and bands are selectable for the bounded shapes,
   with explicit style IDs, compatibility filtering, canonical mappings, and
   no retailer catalog data.
4. **Met.** Metal colour, purity, and finish update appearance without changing
   geometry identity; selected choices remain visible in the final summary.
5. **Met.** The 3D preview supports drag/360 interaction, stable product framing,
   settled-quality rendering, and a coherent fallback; browser preview remains
   non-certifying.
6. **Met.** Desktop and mobile browser verification covers the shape/head/band matrix,
   selection persistence, API errors, preview fallback, and clean console
   output.
7. **Met.** The full 410-test unittest suite passes; no paid cloud resource is running.
   Azure is used only after fresh operator approval and is deallocated after
   every run.

## Browser verification — 2026-07-30

- Desktop at 1280 x 720 completed a Radiant / crown six-prong / half-pave flow. All seven
  WebGL shapes produced distinct geometry keys and versioned profile IDs;
  selections survived backtracking. White 14k polished, white 18k polished,
  and white 18k brushed shared one geometry key and produced three material
  signatures. Pointer drag, arrow-key rotation, default-off auto-rotation,
  bounded-sphere framing, and settled quality were observed. The final sample
  settled in 911 ms at 120 FPS; the warning/error console was empty.
- A second desktop pass traversed every compatible head and band exposed by
  the compact configurator: 4 heads / 8 pairs for round and 3 heads / 7 pairs
  for each other bounded shape, for 50 selectable UI pairs total. The
  exhaustive backend contract independently configured the same 50 pairs
  through the canonical gate.
- Forced `?fallback=1` exercised all seven shapes through the shared v2
  profiles. Every shape used `fallback-2d`, deterministic 2D framing, and a
  distinct geometry key; the warning/error console remained empty.
- Mobile at 390 x 844 completed Pear / low bezel / classic band and a second
  validation-recovery flow. The document had no horizontal overflow, the
  preview and content surfaces did not overlap, the current-step validation
  alert was visible, and the final status reset to “Design brief validated.
  Rhino geometry is pending operator review.”
- The compact reviewed-certificate path displayed the server's HTTP 400
  review error, and a direct local API check also returned HTTP 400 for an
  unsupported shape. Browser tabs were finalized and the local test server was
  stopped after verification.

## Remaining separate geometry decision

Goal 0010 is complete without promoting the prior Azure component pilot. That
pilot remains derived comparison evidence, not a product master. The realism
correction, professional Rhino/Grasshopper source decision, fixed-pose visual
approval, and differential authoritative geometry proof are carried as
Goal 0011 in
`docs/goal_0011_realism_first_rhino_master_plan_2026-07-30.md`. They still
require explicit source approval and fresh operator approval before any paid
Azure run.
