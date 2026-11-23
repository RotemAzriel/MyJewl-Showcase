# Casting shop survey — field questionnaire

**Date:** 2026-08-04 · **Purpose:** collect the calibrated manufacturing numbers MyJewl currently does not
have, in a form that can be encoded as a versioned `ShopProfile` (Goal 0013 Part B).

**Why this document exists.** `jewelry_gaas_foundation/constraints.py` is full of deferred calibration —
*"a shop-specific structural minimum is required"*, *"obtain calibrated shop approval"* — and
`stl_gates.py` returns `needs_calibration`. Those placeholders are waiting on answers a shop has and we
don't. Per the brand-protecting rule, MyJewl never certifies manufacturing pass/fail; a calibrated shop
profile plus human jeweler review is the *only* path off `needs_calibration`. This survey is how that
profile gets populated.

**Every question is annotated with what it populates.** Answers that don't map to a field or a decision
were cut.

**Companion document:** [`modelist_brief_2026-08-12.md`](modelist_brief_2026-08-12.md) — this survey is for
the **casting shop** (process limits: what will physically fill, what will break). The modelist brief is for
an **experienced jewelry modelist** (construction and craft: where the seat is cut, how prong wire is
chosen). Different people, different conversations, do not merge them.

---

## Before you go — bring evidence, not abstractions

Shops answer hypotheticals badly and real parts well. Bring:

- **A printed resin sample of the V6 ring**, or at minimum the file plus the measured numbers below.
- **The measured facts.** **Superseded 2026-08-12** — the 2026-08-04 figures in earlier drafts of this
  document were a *naive per-part sum* that double-counted every interpenetration. A real boolean union
  now exists, and it is **6.75% smaller**. Quote from the union column.

| Fact | Value |
|---|---|
| **Metal volume (true boolean union)** | **162.23 mm³** |
| Metal volume (naive per-part sum — **do not quote**) | 173.19 mm³ (+6.75%) |
| Mass from union: 14k / 18k / pt950 / 925 | **2.12 g / 2.53 g / 3.48 g / 1.68 g** |
| Shank alone | 153.87 mm³ — **~89% of all metal** |
| Basket petal (×4) | 0.223 mm³ each |
| Prong tip (×4) | **0.016 mm³ each** |
| **Thinnest measured section (US 6.5 / 6.50 mm)** | **0.311 mm** (prong tip) |
| **Thinnest at the smallest offered size (5.00 mm stone)** | **0.239 mm** (prong tip) |
| Parts in the file | 20 named + 1 unioned `castable_solid`, all watertight |

Densities used: 13.07 / 15.58 / 21.45 / 10.36 g·cm⁻³. Volumes are **finished nominal with no shrinkage
or finishing compensation applied** — that is MyJewl's deliberate choice, on the grounds that
compensation is shop- and alloy-specific. Confirm they expect to apply their own.

The two numbers to physically point at: **the 0.016 mm3 prong tips and the 0.239 mm minimum
section at our smallest offered stone.** Ask *"would you cast this, as-is, today?"* and watch what they
do with their hands. That single moment will teach you more than any answer below.

**The one question this document did not previously ask** (audit finding F3, 2026-08-12): our generator
currently scales every wire dimension **linearly with stone size**, so prong wire thins toward zero as
the stone shrinks. Ask directly: *"Does prong wire scale with the stone, or do you work from a fixed set
of stock sizes with a floor?"* We have no calibrated minimum castable wire diameter anywhere in the
system — it is the single most load-bearing empty number we hold.

**Ask for the "no" first.** People are more precise about failure than success. Lead every role with what
goes wrong, then work backwards to the numbers.

---

## Segment by role — these answers do not live with one person

| Role | What only they know |
|---|---|
| **CAD / model prep** | What they silently repair before it ever reaches the caster — the most under-asked question in this whole document |
| **Caster / foundry operator** | Burnout, flask, pour temperature, what physically fails to fill |
| **Setter** | Seat geometry, prong stock, what must be left un-modeled |
| **Polisher / finisher** | Stock allowance, what breaks in tumbling |
| **Owner / PM / sales** | Pricing, weight calculation, liability, turnaround, volume terms |

If you only get one conversation, get it with **CAD/model prep**. They sit exactly where an automated
pipeline would plug in, and they are the ones who currently absorb the defects a generator would produce.

---

## Section 1 — CAD / model prep

**1.1 What makes you send a file back? Top five reasons, ranked by how often.**
*Ask this first, to this person.* Then: *"walk me through the last one you rejected."*
→ Becomes the highest-priority validation rules. Frequency ranking tells us what to build first.

**1.2 What do you silently fix without telling the customer?**
The critical follow-up to 1.1. Shops routinely repair normals, close meshes, thicken sections, and never
mention it. **If our generator relies on that invisible repair, our approved geometry is not what gets
cast.** Ask: *"if you repair it, does the customer ever see what changed?"*
→ Determines whether "approved preview geometry" is a truthful claim.

**1.3 Format: STL, 3DM, STEP, or something else? Which do you actually prefer, versus tolerate?**
→ Export target. Note we currently produce STL, OBJ, and 3DM.

**1.4 Mesh tolerance — what maximum chord deviation do you want? Is there a triangle count that is too dense?**
Vague "high quality" answers are useless; push for a number in mm.
→ Mesh export settings.

**1.5 Do you want the ring as ONE merged solid, or as separate named parts?**
**Updated 2026-08-12: we now emit both.** 20 named parts *and* a booleaned `castable_solid` (one
watertight body). Ask which they want to receive, and whether the named parts are useful or noise.
→ Decides whether union is a hard pipeline requirement.

**1.6 Do overlapping/intersecting solids cause you problems, or does your software handle them?**
→ Same decision as 1.5, from the failure side.

**1.7 Do you care about normal orientation? What happens if some solids have inverted normals?**
**Fixed 2026-08-12 (V6): all parts are now outward-oriented.** Still ask — we need to know whether the
week of V4/V5 files we may already have sent them were silently auto-fixed, rejected, or carried the
defect into a cast.
→ Validates the inverted-winding check in Goal 0013 Part A.

**1.8 Watertight/manifold — hard requirement or do you repair? Non-manifold edges, self-intersections, duplicate vertices?**
→ Mesh gate severity.

**1.9 Units — do you assume mm? Have you ever received a file at the wrong scale?**
→ Trivial to get right, catastrophic to get wrong. Confirm the convention in writing.

---

## Section 2 — Process route

**2.1 Which route do you use: resin print → investment, wax print → investment, or print a master → rubber mold → production waxes?**
→ The single biggest determinant of minimum feature size. Rubber molds need materially more material
than direct casting.

**2.2 Does that route change for a one-off versus repeat production?**
→ Whether we need two profiles (prototype vs production) rather than one.

**2.3 Which printer and what resolution/layer height?**
The printer, not the caster's rule of thumb, sets the true minimum feature size.
→ Grounds every minimum in Section 3.

**2.4 Do you apply shrinkage compensation, or must it be baked into the file we send? What factor, per alloy?**
Push past a single number — shrinkage stacks across resin/wax, investment, and metal.
→ Whether we scale geometry at export, and by how much per alloy. **If we must scale, this changes the
delivered geometry and must be recorded in the receipt.**

**2.5 Do you add sprues and gates, or must they be modeled? If you add them — where do you prefer them, and is there anywhere we must keep clear?**
**Our shank is 89% of the metal**, so feed strategy is a shank question. Ask whether they need a thickened
or flattened sprue zone.
→ Whether the generator must reserve sprue real estate.

---

## Section 3 — Minimums (the core of the shop profile)

Ask each as *"minimum for what alloy, and over what unsupported length?"* — a thin section that is short
is fine; the same section spanning 4 mm is not. **A single number without those two qualifiers is not
usable.**

**3.1 Minimum prong diameter at tip, and at base?**
→ Calibrates `METAL-001` / `METAL-002`, currently SOFT and explicitly calibration-gapped. Our recorded
0.45 mm figure is Stuller's *pinpoint-prong* minimum and is not a general rule.

**3.2 Minimum shank thickness? Minimum shank width?**
→ Calibrates `BAND-001` (currently an uncalibrated 1.8 mm placeholder) and `BAND-002`/`BAND-004`
(uncalibrated 1.5 mm).

**3.3 Minimum gallery / basket wall thickness?**
→ Currently unrepresented. **Our basket petals are 0.223 mm³ — ask directly whether that survives.**

**3.4 Minimum wire diameter for an open gallery or filigree element?**
→ New profile field.

**3.5 Minimum gap between adjacent metal features?** (pavé beads, split-shank halves)
→ Our trilogy reference config has 14 pavé accents and a split shank; both need this.

**3.6 Is there an alloy where the minimums get stricter?** Rose gold, high-karat, platinum?
→ Profile must be **per-alloy**, not global. Expect platinum to differ most — possibly different
equipment entirely.

**3.7 Are these minimums pre- or post-finishing?**
**Frequently missed and it inverts the whole rule set.** If polishing removes 0.05 mm and the minimum is
post-finish, every threshold must be raised before we validate against it.
→ Determines whether minimums are checked against as-modeled or as-finished geometry.

---

## Section 4 — Finishing allowance

**4.1 How much stock do you expect us to leave for polishing and finishing? Does it vary by surface?**
Outer shank, inside shank, under-gallery, and between prongs are usually different.
→ New profile field. Interacts directly with 3.7.

**4.2 Do you tumble, hand-polish, or magnetic-pin finish? What breaks in that process?**
→ Fragility rules. Thin unsupported elements often survive casting and die in finishing.

**4.3 Does finishing change the ring size? Do you expect us to model undersize?**
→ Ring size is a **typed input** we promise is exact. If finishing moves it, we must compensate at
generation and say so.

---

## Section 5 — Stone setting

**5.1 Cast-with-stones-in-place, or cast then set?**
Ask before anything else in this section — it forks every other answer.
→ Fundamental pipeline branch.

**5.2 Does the setter cut the seats, or do you expect seat, girdle clearance, and culet clearance modeled?**
→ Decides whether our head geometry ships setting-ready or setting-blank. Calibrates the stone-metal
collision gate, currently `needs_calibration`.

**5.3 If we model the seats — what clearance under the girdle and around the culet?**
→ Numeric fields for the collision gate.

**5.4 How much prong length and thickness do you need left as stock for setting?**
Prongs are almost always modeled long and trimmed at setting. **Our prong tips are 0.016 mm³ — ask
whether they are modeling stock or a finished tip.**
→ Prong stock allowance field.

**5.5 For melee and pavé — do you cut those seats, and do you need the holes modeled?**
→ Pavé schedule requirements for the trilogy config.

---

## Section 6 — Commercial and liability

*Entirely absent from the original list, and this is where an automated pipeline actually gets blocked.*

**6.1 How do you calculate the weight you quote from? Do you compute volume from the file yourselves?**
**Ask precisely: do you boolean-union overlapping parts before computing volume, and what density figures
do you use per alloy?** If their number and ours disagree, one of us mis-prices every ring.
→ Directly validates our `quote_basis` implementation.

**Updated 2026-08-17 (Goals 0023–0024) — give them our actual numbers and ask them to mark their own.**
Our reference ring is the US 6.5 / 6.50 mm round solitaire, measured union **162.234 mm³**:

| Metal | Grams | DWT | SPG we use | Their SPG | Their grams |
|---|---:|---:|---:|---|---|
| 14k yellow | 2.12 | 1.37 | 13.10 | | |
| 14k white | 2.09 | 1.35 | 12.90 | | |
| 14k rose | 2.11 | 1.36 | 13.00 | | |
| 18k yellow | 2.52 | 1.62 | 15.50 | | |
| 18k white | 2.47 | 1.59 | 15.20 | | |
| 18k rose | 2.47 | 1.59 | 15.20 | | |
| Platinum 950 | 3.36 | 2.16 | 20.70 | | |

**We already know these will not match exactly.** Gemvision Matrix's stock library gives 14KY as
**12.88** against our 13.1 (~1.7%) — alloy recipes genuinely differ by house. **The question is whose
figures we should ship.** `schemas/shop_profile_v0_1.schema.json` already has a
`commercial.densities_g_cm3` slot for their answer; nothing reads it yet, and wiring it changes every
weight we quote, so their numbers decide whether we do.

**6.1b Do you quote in grams or pennyweight, and do you want files accompanied by both?**
→ We now emit both (DWT = grams / 1.55517384). Confirm which they treat as authoritative.

**6.2 Who pays when a cast fails — metal, labor, both?**
→ Risk model for automated submission.

**6.3 What is your scrap and remelt policy? Do we get the metal back?**
→ True unit economics.

**6.4 Minimum order quantity? Turnaround for one piece versus a batch?**
→ Product lead-time promises.

**6.5 Is quoting automatic from the file, or does a human price it?**
→ Whether an API-driven quote is even possible with this shop.

**6.6 Do you accept files directly from a system, or must a person submit them?**
→ Integration feasibility.

---

## Section 7 — The strategic questions

**7.1 If I send a file from an automated system with no modelist reviewing it — would you cast it? What would you need to see first?**
Keep this as the closer, and push for specifics: *what evidence, in what form?* Then the sharper follow-up
they will not expect: **"would you cast it at fifty units a week, and would you price it differently?"**
Willingness for one piece as a favor is not willingness at volume.

**7.2 Would you be willing to give us your minimums in writing, as a spec we can version and encode?**
→ **This is the ask that unblocks our manufacturing gates.** Everything else is research; this is the
deliverable. Frame it as: we will encode your numbers, attribute them to you, and re-validate whenever you
change equipment.

**7.3 Would you review a sample set of automated outputs and mark them pass or fail?**
→ The calibration set. A shop profile plus jeweler review is the only route off `needs_calibration`, and
this is how we get the jeweler review.

**7.4 How often do these numbers change? When you change printer, investment, or alloy supplier, do the minimums move?**
→ Profile expiry and re-validation cadence. A profile with no expiry is a profile that quietly goes stale.

**7.5 When something fails, will you tell us why in enough detail to fix the generator? Photos?**
→ The feedback loop. Without it we are guessing forever.

---

## After the visit

1. Write answers into a `ShopProfile` instance — **cited, dated, attributed, with alloy and process scope.**
2. Anything they would not commit to in writing stays **uncalibrated** and the corresponding gate stays
   `needs_calibration`. Do not promote a hallway opinion to a HARD rule.
3. A profile alone still cannot certify manufacturing. Per the brand rule, pass/fail requires the
   calibrated profile **and** human jeweler review.
4. Contradictions between shops are signal, not noise — they mean the field is genuinely process-dependent
   and the profile must stay per-shop rather than global.
