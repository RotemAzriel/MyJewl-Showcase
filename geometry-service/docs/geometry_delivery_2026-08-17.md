# What MyJewl delivers as geometry, and everything it does not

**Goal 0025 · 2026-08-17 · decision document.** Read this before touching
`fast_mvp/geometry_delivery.py`, the delivery table, or `POST /api/deliver-geometry`.

MyJewl is Geometry-as-a-Service. Until this goal there was **no path by which anyone received the
geometry it can prove**: `/api/generate-stl` returned a procedural draft built by `trimesh`, the four
promoted assets shipped only as Three.js browser meshes, the receipt-backed Rhino STLs sat in a
gitignored directory served to nobody, and `/studio`'s downloads panel had been an empty `<div>` since
Goal 0008.

This document says which bytes may be handed over, under what conditions, and — because the answer today
is *none of them* — exactly which conditions are unmet and who has to answer them.

---

## 1. The three candidates

The promoted V4/V5 exports are **19 loose interpenetrating metal solids, 12 of them inside-out** (findings
F1 and F2). Yet `measured_volumes_v0_1.json` records `union_watertight: true` and `union_body_count: 1`
for all four assets, because `geometry_metrics` computes that union *locally, with manifold3d*, after
repairing winding **on a copy, in memory**. Goal 0023 priced the ring from exactly that number. So there
were three things we could have delivered.

### Candidate 1 — the receipt-backed Rhino part bytes, verbatim · **CHOSEN**

What Rhino actually produced, hash-verifiable against the receipt, carrying F1 and F2 as **declared
defects** rather than silently-repaired ones.

**Why it wins:** it is the only candidate that is Rhino's own output. Every other option requires MyJewl
to author geometry, and the standing invariant is that Rhino/Grasshopper is the sole geometry authority —
the model orchestrates and emits structured parameters, it never hand-authors geometry. Delivering the
exact bytes a receipt vouches for keeps the chain from typed request → derived parameters → Rhino solve →
receipt → customer unbroken and auditable end to end. If the geometry is wrong, the fix belongs in the
GHX, not in a post-processing step nobody can see.

### Candidate 2 — the locally-computed union mesh · **REFUSED**

Watertight, one body, outward — everything a caster wants — and **not Rhino's output**.

It is tempting precisely because Goal 0023 already relies on it, and that is the trap. **Measuring a
derived mesh is a different act from shipping one.** Goal 0023 used the union to answer *how much metal is
in this ring*, a question about a quantity, where a lower bound that errs in the customer's favour is a
defensible commercial estimate. Delivering it answers *this is the thing to cast* — a claim about the
object itself, and a materially stronger one. Between those two sits every difference that matters:

- the union is produced by `trimesh.boolean.union` under manifold3d, a tessellated approximation of the
  NURBS bodies, not the bodies;
- it is built from meshes whose winding this repository **inverted in memory** to make the boolean
  computable. Shipping it would mean shipping a silent repair of F1 while F1 stays open in the source —
  the geometry a customer holds would no longer be the geometry any receipt describes;
- the parts carry names (`prong_01`, `basket_petal_03`, …) that the union destroys. Those names are how
  Blender assigns mask IDs and how `geometry_metrics` measures per-part sections; a bench reads them too;
- V6 already emits a **real** `Brep.CreateBooleanUnion` (`castable_solid`) from Rhino. A derived union
  would be a workaround for not having promoted V6 — and it would make promoting V6 *look* unnecessary,
  which is the opposite of what this goal is for.

Nothing here says the union number is untrustworthy. It agrees with Rhino's own NURBS union to 0.647% on
the V6 control. It is simply not a thing Rhino ever produced, and the invariant is not about accuracy.

### Candidate 3 — deliver nothing until V6 is promoted · **REFUSED**

Correct about today, wrong as an engineering answer. Not building the path leaves the policy unwritten and
the refusal unspoken: a customer sees an empty panel and cannot tell whether the geometry is withheld, not
yet built, or simply forgotten. It also leaves the promotion decision (Goal 0019) unforced, which is the
condition it has been stuck in.

**We build the mechanism and let it refuse**, loudly and specifically, for exactly as long as the promoted
assets deserve refusing. The refusal is the product surface: it names the defect, names the asset, and
names the person whose answer would change it.

---

## 2. What makes a bundle deliverable

Seven conditions, all of which must hold. Each failure has its own name and its own message; there is no
generic "unavailable".

| # | Condition | Refusal when it fails |
|---|---|---|
| 1 | A delivery table is loaded | `no_delivery_table` |
| 2 | The preview resolved **`exact`** | `preview_not_exact` |
| 3 | The configuration's geometry signature is in the table **verbatim**, and the selected asset's id **and** version match the record | `no_deliverable_record` |
| 4 | The export bundle is present on this machine, at a path inside the repository | `bundle_not_present` · `bundle_path_invalid` |
| 5 | Every file we are about to serve hash-matches the receipt, byte for byte, **at request time** | `bytes_do_not_match_receipt` |
| 6 | A **calibrated, unexpired** shop profile declares its whole `mesh` block | `mesh_policy_undeclared` |
| 7 | What we have satisfies what that shop accepts | `shop_requires_outward_normals` · `shop_rejects_intersecting_solids` · `single_solid_required_but_absent` · `shop_requires_watertight_parts` · `triangle_budget_exceeded` · `preferred_format_unavailable` · `units_mismatch` |

Conditions 2 and 3 are deliberately identical in shape to `quote_volumes.volume_for_manifest`. Delivering a
neighbouring size's STL is the same failure class as quoting a neighbouring size's volume, and worse: a
price that is 4% wrong is an argument, a ring that is one size wrong is a return.

Condition 5 is re-checked on **every** request rather than trusted from the table. The bundles live in
gitignored `output/`, outside version control, on a developer's laptop. A table built in August is not
evidence about the bytes on disk in November.

### The delivered set is exactly what the receipt vouches for — no more, and no less

The receipt lists every part with its `sha256`, `byte_size`, `triangles`, `vertices`, and a `role`. **Those
files, and only those files, may be delivered.** Three roles exist and all three are deliverable, landing
in their own directory inside the archive:

| Receipt role | Archive directory | What it is |
|---|---|---|
| `metal` | `geometry/` | The 19 named parts. This is what gets cast |
| `castable` | `fused/` | Rhino's own `Brep.CreateBooleanUnion`. **V6 emits it; V4/V5 do not — that absence *is* F2**, so `fused/` is empty in every delivery today. It is a *derivative* of the metal parts and is excluded from `metal_part_count`, exactly as `geometry_metrics` excludes it from the assembly sum; counting it would count the whole ring twice |
| `stone` | `reference/` | The centre stone, `reference_only_not_for_casting` |

**An invalid union is not a union.** `ships_castable_solid` is true only when the fused solid measures
closed *and* outward-oriented, so an open or inside-out one cannot satisfy a shop that requires one solid;
it raises `CASTABLE_SOLID_INVALID` instead, and the table refuses to load claiming otherwise.

**Completeness is checked in both directions.** Every delivered file must be vouched for by the receipt —
and every `role: metal` part the receipt records must actually appear in the delivered set. Without the
second check, a stale record that dropped `shank.stl` *and* decremented `metal_part_count` would pass the
schema, pass every hash check, and hand over a ring with no band. A missing part delivered silently is
worse than a refusal.

Consequences worth stating, because each one is a line somebody would otherwise cross:

- **`model.stl` is never delivered.** It is the concatenation of everything, including the stone, and the
  receipt does not hash it. Seven megabytes we cannot vouch for is not a deliverable.
- **`.3dm` and `.obj` are never delivered**, for the same reason — the receipt hashes only the STLs. If a
  shop needs `3dm` (and `mesh.preferred_format` says some will), the fix is to make the next solve hash the
  `3dm` in the receipt, not to relax this rule.
- **`fast_mvp/mesh.py` output can never appear here.** The procedural draft is a selection-time operational
  aid. It has no receipt, so it cannot pass condition 5 even if someone wired it in by mistake — the
  structure refuses it, not a comment. A test asserts it.

---

## 3. The mesh policy was already modelled — read it, do not invent it

`schemas/shop_profile_v0_1.schema.json` has defined this since Goal 0013 Part B:

```
mesh.preferred_format                        stl | 3dm | step | obj | unknown
mesh.max_chord_deviation_mm                  number | null
mesh.max_triangle_count                      integer | null
mesh.single_solid_required                   boolean | null
mesh.intersecting_solids_accepted            boolean | null
mesh.outward_normals_required                boolean | null
mesh.inverted_normals_are_silently_repaired  boolean | null
mesh.watertight_required                     boolean | null
mesh.units_assumed                           mm | inch | unknown
```

**Every one of them is `null` or `unknown` in the shipped template**, whose `status` is `uncalibrated` and
whose validity expired on 2026-08-05. Whether inside-out, unfused parts are deliverable is **a property of
the receiving shop**, not a property of our geometry, and no shop has answered. That is the honest reason
to refuse, and it was already in the schema waiting to be read.

A profile makes the `mesh` block usable only when the profile is `calibrated`, unexpired, and the six
gating fields are all stated: `preferred_format`, `units_assumed`, `single_solid_required`,
`intersecting_solids_accepted`, `outward_normals_required`, `watertight_required`. A partially-filled block
is `mesh_policy_undeclared` and says which fields are missing.

### Two rulings inside the policy that are not obvious

**`inverted_normals_are_silently_repaired` never grants permission.** Read plainly it says: this shop's
pipeline flips your normals without telling you. It is tempting to treat that as "F1 does not matter here",
and we do not, for two reasons. First, it makes the shop's slicer — not our geometry — the thing that
decides what the model means, and we would never learn when it decided wrong. Second, it means a known
defect ships and nobody is informed. The field that grants delivery is `outward_normals_required: false`.
When silent repair is declared it is recorded **in the evidence, as a warning**: it explains why nobody
noticed, it is not a reason to keep not noticing.

**`max_chord_deviation_mm` warns, it does not refuse.** We cannot recover the chord tolerance a mesh was
tessellated at from the mesh alone, so a declared value is reported as `not_verified` beside the delivery
rather than silently treated as met. Refusing on it would be over-reach; claiming compliance would be a lie.
Saying "you asked for 0.01 mm and we did not verify it" is the honest third option.

---

## 4. What travels with the bytes

A file arriving at a casting house with no context gets guessed at. Every delivery carries a generated
`delivery_manifest.json` — **MyJewl's own document, not Rhino output, and marked as such** — containing:

- the asset identity, its geometry signature, the definition path and its SHA-256, and the receipt SHA-256;
- every delivered file with its receipt hash, byte size, triangle and vertex counts, role, and — for metal
  parts — whether it measured watertight and outward;
- the measured volume that Goal 0023 priced from, its basis (`measured_mesh_union`), its engine, and the
  naive parts sum **named as what it is not**, with its over-count;
- the **Metal · Grams · DWT · SPG** table for all seven alloys from Goal 0024, so the shop can check our
  number against their own before they melt anything, in the unit they quote in;
- the open findings, in full: F1 with the twelve part names, F2 with the over-count it causes;
- the shop profile the delivery was authorised against, and what that profile did **not** declare;
- `certifies_manufacturing: false` and `manufacturing_gates: pending_not_run_needs_calibration`, restated
  where they cannot be missed.

The archive is built deterministically — fixed timestamps, sorted entries — so the delivery itself has a
stable SHA-256 that the response reports and a recipient can quote back.

---

## 5. What this refuses today, and what would change it

With the four promoted V4/V5 assets and the shipped uncalibrated profile, **every delivery request
refuses**, with this reason:

> `mesh_policy_undeclared` — No calibrated shop profile declares its mesh policy, so nobody has said
> whether this geometry is acceptable input. This asset ships **12 of 19 metal parts with inverted
> normals** (F1) and **no boolean union at all** (F2): the parts interpenetrate, and the naive sum
> over-states metal by 6.75%. Whether that is deliverable is a property of the receiving shop, and the
> shipped profile leaves every mesh field unset.

Three distinct things would each lift it, and they are owned by three different people:

1. **The casting shop answers Section 1 of the survey** (`docs/casting_shop_survey_2026-08-04.md`) and the
   operator transcribes it into a calibrated profile. If that shop accepts interpenetrating solids and does
   not require outward normals, **today's assets become deliverable as they are** — which is a real
   possibility, not a hypothetical; plenty of shops import, boolean, and repair as a matter of course.
2. **V6 is promoted** (Goal 0019, deferred). V6 already fixes F1 and F2 at the source: orientation repaired
   at the Brep level, and a real `Brep.CreateBooleanUnion` emitted as `castable_solid`. That makes a shop
   requiring outward normals deliverable, and makes `single_solid_required: true` satisfiable — which it
   structurally is not now. **One case it does not fix on its own:** a shop with
   `intersecting_solids_accepted: false` would still receive the named parts alongside the fused solid,
   because the delivered set is the whole receipt-hashed list. Sending only the fused solid is a coherent
   behaviour and deliberately not implemented — no promoted asset emits a union, so there is nothing to
   build it against or test it on, and guessing at it now would be the same mistake as shipping a derived
   union. That refusal names itself and says so.
3. **Neither** — and the refusal stands, correctly, saying so.

That is the point of this goal. F1 and F2 stop being rows in a findings table and become **the concrete
thing stopping the product from delivering**, which is the missing input to the Goal 0019 decision:
promoting V6 has looked optional because nothing depended on it. Now something does.

---

## 6. What delivery deliberately does not do

- **It does not certify manufacturing.** Nothing here moves a gate. Gates stay
  `pending` / `not_run` / `needs_calibration`, and a calibrated profile alone can never flip one — that
  invariant is Goal 0013's and is untouched. A shop profile authorising *delivery* is a statement about
  what that shop can read, not about whether the ring is castable. **F3 is unaffected**: every wire section
  in the envelope may still be too thin, and delivering the file does not make it thicker.
- **It does not approve anything new.** Only assets already promoted — approved, visually reviewed, exact —
  are candidates. Delivery adds no human gate and removes none.
- **It does not repair geometry.** Not winding, not the union, not units. If the bytes are wrong, they are
  delivered wrong-and-declared, or not at all.
- **It does not create geometry.** No path in `fast_mvp/geometry_delivery.py` can produce a file; it can
  only refuse, or read bytes a receipt already vouched for.

---

## 7. Where this lives

| Thing | Path |
|---|---|
| The gate and its refusals | `fast_mvp/geometry_delivery.py` |
| The mesh policy reader | `jewelry_gaas_foundation.shop_profile.ShopProfile.mesh_policy` |
| The committed table | `fast_mvp/preview_assets/deliverable_geometry_v0_1.json` |
| Its schema | `schemas/deliverable_geometry_table_v0_1.schema.json` |
| Its generator (free, local, `$0`) | `python3 -m scripts.goal0025_measure_deliverables --write` |
| The endpoints | `POST /api/deliver-geometry` · `GET /api/deliverable/<job_id>.zip` |
| Deployment state at a glance | `GET /api/health` → `delivery` |
| Point at a calibrated profile | `MYJEWL_SHOP_PROFILE=/path/to/profile.json` |

**Promoting an asset does not make it deliverable**, exactly as promoting does not make it quotable. The
registry says what can be *shown*, the volume table what can be *weighed*, the price sheet what it *costs*,
and this table what can be *handed over*. Four separate artefacts, four independent refusals, each one
naming itself. A test asserts the delivery table covers exactly the promoted approved exact assets, so
promoting without regenerating turns the suite red rather than shipping a silent gap.
