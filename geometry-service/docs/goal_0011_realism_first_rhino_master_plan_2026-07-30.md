# Goal 0011 — realism-first Rhino ring master

**Status: complete (2026-07-31).** Receipt-backed V4 is the approved bounded
visual master, its V2→V4 differential is proved, and its exact receipt-backed
mesh derivative is available in the Studio. Manufacturing
review remains pending.

## Progress — 2026-07-31

- The operator approved the V2 head **design direction** from the local
  source-intent close-ups: split shoulders converge into the lower gallery
  collar, remain below the diamond, and leave the four prongs as the only
  members continuing into the stone-retention zone.
- This approval authorizes preparation of the V2 Rhino candidate. It does not
  close the receipt-backed visual gate and is not a manufacturing decision.
- The V2 Azure path is revision-isolated under template
  `goal0011-realism-first-round-solitaire-master-v2` and job
  `goal0011-realism-master-v2`. The explicitly approved bounded solve completed
  on 2026-07-31; all 20 named parts are closed and hash-matched, and the run
  report records `confirmed_deallocated: true` / `VM deallocated`.
- Three receipt-linked 2048 px / 128-sample fixed views were rendered under
  `output/goal_0011_realism_master_20260730/cycles_fixed_v2/`. The
  operator found the visible result too similar to V1, so V2 was not approved
  as the visual master.
- V3 is a free local source-intent revision that moves the split-shoulder
  endpoint from the upper collar (`z=10.90`) to the lower basket rail
  (`z=9.70`). Its local close-ups are pending operator direction. No V3 cloud
  solve has been authorized or started.
- V4 is the recommended midpoint candidate. Each split shoulder terminates on
  a mid-height gallery petal at `z=10.38`, below the seat rail and diamond,
  preserving cathedral flow without the perched V3 silhouette. Its free local
  close-up and whole-ring previews were approved by the operator on
  2026-07-31. The operator then authorized one bounded V4 Azure Rhino solve.
  It completed under template
  `goal0011-realism-first-round-solitaire-master-v4` and job
  `goal0011-realism-master-v4`; the final report confirms
  `confirmed_deallocated: true` / `VM deallocated`.
- The V4 receipt-backed candidate contains all 20 expected named parts; all 20
  are closed and every declared STL hash matches. Three receipt-linked 2048 px
  / 128-sample views are ready under
  `output/goal_0011_realism_master_20260730/cycles_fixed_v4/`.
- The operator explicitly approved receipt-backed V4 on 2026-07-31. The
  approval pins the receipt and fixed-view evidence hashes and all seven visual
  checks.
- The V2→V4 receipt comparison proves exactly the four split shoulders and
  four basket petals changed; shank, rails, diamond, four prongs, and four tips
  stayed byte-identical.
- Catalog revision `modular-rings-v4` exposes one exact round 6.5 mm / US 6.5
  V4 reference pair. Its WebGL path loads a deterministic hash-pinned mesh
  derived directly from the approved receipt-linked Rhino OBJ; Rhino source
  remains authoritative and manufacturing remains non-certifying.

## Why this goal exists

Goal 0010 delivered the internal shape/head/band configurator and a polished
browser preview surface. It did not create a new professional Rhino product
master. The Azure Rhino result from the prior component pilot is visibly more
realistic than the browser-only preview and must become the next geometry
reference.

The baseline evidence is:

- `output/goal_0010_component_pilot_20260730/azure/received/` — receipt-backed
  Rhino-derived named parts and OBJ/3DM evidence;
- `output/goal_0010_component_pilot_20260730/cycles_reference/` — fixed-pose
  comparison render; and
- `output/goal_0010_component_pilot_20260730/azure/candidate_audit.json` —
  provenance and part/hash audit.

These files are derived evidence, not manufacturing authority.

## Realism-first objective

Produce one visually credible MyJewl-owned solitaire master for the bounded
round 6.5 mm / US 6.5 reference before adding more visual catalog breadth.
The result must read as a coherent jewelry object, not as disconnected pipes
around a stone.

The first revision must specifically address:

- head members that currently read as long vertical rods below the head;
- prong tips that do not visibly seat the stone naturally;
- shoulder-to-head transitions that look mechanical or layered;
- gallery/basket support continuity and plausible negative space;
- excessive highlight clipping and hot exposure in the fixed render; and
- believable stone scale, contact shadow, metal reflections, and camera framing.

## Required workflow

1. Inspect the current Azure-derived render and named-part receipt.
2. Revise the human-maintained RhinoCommon/GH candidate or use an approved
   professional Rhino authoring source. Rhino is the geometry authority.
3. Run the bounded solve on Azure Rhino Core only after fresh operator approval.
   Keep the API key in the VM environment; never place credentials in argv,
   source, reports, or commits. Deallocate the VM in the same run.
4. Audit receipt closure, named parts, roles, and hashes.
5. Render fixed front three-quarter, side, and top/stone-seating Cycles views at
   2048 px. Use the browser mesh only as comparison evidence.
6. Record a human visual decision. Do not mark manufacturing pass/fail.
7. Only after visual approval, prove one or more differential variations for
   selected internal head/band/shape fields and promote the approved target to
   the browser preview.

## Acceptance checklist

- No head member reads as an unconnected vertical rod in the fixed views.
- The stone is visibly seated and retained by plausible prongs or a continuous
  bezel; gallery supports connect into the shoulders/head.
- Front, side, and top silhouettes read as one finished ring.
- Metal and diamond highlights are controlled enough to preserve form.
- The render has a grounded contact shadow and no obvious floating components.
- Receipt-backed named parts remain deterministic and hash-auditable.
- Differential geometry proof is recorded for any promoted variation.
- Browser/configurator changes follow the approved realism target rather than
  replacing it.
- Geometry/manufacturing gates remain non-certifying:
  `pending`, `not_run`, or `needs_calibration`.

## Explicit non-goals

- No retailer assets, product IDs, names, dimensions, or copy.
- No manufacturing certification.
- No browser-only geometry promoted as Rhino authority.
- No Azure/RunPod execution without fresh operator approval.

## Local source-preflight record — 2026-07-30

- MyJewl-owned RhinoCommon revision:
  `manual/tools/build_goal0011_realism_master_ghx.py`.
- Generated GHX derivative:
  `manual/rhino_compute_bridge/goal0011_realism_master_v1.ghx`.
- Ownership/licensing boundary:
  `docs/goal_0011_source_ownership_2026-07-30.md`.
- The shoulder roots moved from the visibly defective mid-ring height
  (`z=0.72 mm`) to the upper shank (`z=7.72 mm`) and now terminate on the
  upper seat rail. Basket petals connect the lower and upper rails. Prongs run
  from the lower basket through the seat and turn inward over the crown.
- A free local 512 px / 24-sample Blender 5.2 source-intent preflight produced
  all three required camera views under
  `output/goal_0011_realism_master_20260730/local_source_intent/`. It is
  generated comparison evidence only, not Rhino geometry and not the required
  2048 px approval set.
- Eight focused Goal 0011 tests cover the owned source, removal of the long
  shoulder path, inward prong seating, GH input/output contract, receipt/hash
  audit, human visual-gate semantics, cloud approval/credential boundaries,
  and fixed-view renderer contract.
- At the time of this local preflight, Azure execution was still unapproved
  and had not started. The later receipt-backed run is recorded below.

## Receipt-backed master run and review status — 2026-07-30

- The operator approved one bounded Azure Rhino.Compute solve. The solve
  completed with 20 receipt-listed parts. Two transfer-orchestration defects
  were corrected without running a second solve; every attempt deallocated the
  VM, and the final report confirms `VM deallocated`.
- All 20 parts are closed, every receipt-listed STL hash matches, roles are
  exactly `metal` and `stone`, and the source-ownership marker matches the
  documented MyJewl source.
- Three receipt-linked 2048 px / 128-sample Blender 5.2 Cycles views now exist
  under `output/goal_0011_realism_master_20260730/cycles_fixed/`.
- Highlight clipping fell from 52.947% of baseline pixels to at most 0.459%
  across the three fixed views. This is presentation evidence, not visual or
  manufacturing approval.
- This subsection records the earlier V1 run. Its human visual review remained
  unapproved; the selected receipt-backed visual master is V4, recorded below.

## Receipt-backed V4 midpoint run — 2026-07-31

- One explicitly approved bounded V4 solve completed under isolated V4
  template, job, archive, receipt, and output identifiers. The run report is
  `output/goal_0011_realism_master_20260730/azure_v4/azure_master_run_report.json`
  and confirms the Azure VM reached `VM deallocated`.
- Receipt SHA-256:
  `1cb1c04a8994f7710ca17d567f32e7fc14f5169352a4ffa06bedac3031ea43a5`.
- The V4 audit reports 20/20 expected parts, 20 closed parts, no missing,
  duplicate, or extra parts, exact `metal`/`stone` roles, exact V4 template and
  ownership markers, and matching hashes for every receipt-listed STL.
- Three receipt-linked 2048 px / 128-sample Blender 5.2 Cycles views exist
  under `output/goal_0011_realism_master_20260730/cycles_fixed_v4/`. Their
  evidence SHA-256 is
  `0e1791720d960abb880b1ea92a9aedd8a1ceb5fb75ad8a051d0f3c0c7ff684e4`.
- Maximum fixed-view luminance clipping is `0.500%`, versus `52.947%` in the
  presentation baseline. This passes the controlled-highlight presentation
  check only.
- Receipt-backed human visual approval is recorded in
  `output/goal_0011_realism_master_20260730/azure_v4/visual_review.json`; it
  pins the receipt and render evidence and records all seven visual checks as
  approved.
- `output/goal_0011_realism_master_20260730/azure_v4/differential_v2_to_v4.json`
  proves the exact intended V2→V4 scope: eight changed parts (four split
  shoulders and four basket petals) and twelve byte-identical protected parts.
- Catalog revision `modular-rings-v4` adds one exact bounded V4 reference pair
  for round 6.5×6.5×3.9 mm / US 6.5. Browser scene evidence is recorded in
  `output/goal_0011_realism_master_20260730/azure_v4/ui_promotion_evidence.json`.
  The exact WebGL asset contains the OBJ's 20 named parts, 78,281 vertices, and
  149,064 triangles with pinned receipt, OBJ, and binary hashes. It remains a
  browser derivative and does not replace Rhino authoring authority.
- The combined candidate audit reports `master_visual_ready: true`,
  differential `ready`, and browser promotion `ready`. Manufacturing remains
  `pending_not_run_needs_calibration`.
