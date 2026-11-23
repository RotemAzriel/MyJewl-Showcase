"""Canonical solitaire spec -> RhinoArtisan build plan (pure, deterministic).

MVP niche: **the certified-stone solitaire mounting** — one typed center stone
(shape + L x W x D, ideally from a GIA cert) set in a prong, basket or bezel
head on a plain or cathedral shank. That covers the bulk of solitaire demand
(R-1: round = 62% of sales, prong-family ~85-90%, bezel ~10-14%, plain +
cathedral ~83% of shanks), so the parametric surface stays small enough to be
exact. Pear and marquise get V-tip prongs at their points automatically.

``compile_solitaire_plan`` turns a validated canonical spec v0.1 into an ordered
list of RhinoArtisan MCP tool calls (``PlanStep``). Arguments that depend on ids
Rhino returns at run time are symbolic: ``{"$ref": "gem.id"}`` resolves to one
value, ``{"$refs": ["shank.id", "head.ids"]}`` to a flattened list. The executor
(``artisan_executor``) resolves them; nothing in this module talks to Rhino.

Geometry fit is derived, not guessed: the stone's girdle height comes from the
shank top + a culet clearance + the stone's pavilion depth, using pavilion
fractions measured against the Artisan QuickGems kernel; the head's height is
derived so its base sinks ``head_embed_mm`` into the shank for a sound union.
Clearances here are *design defaults*, not certified values — CLEARANCE-001/002/003
are schema gaps, and the five geometry gates stay ``pending`` (brand rule).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from jewelry_gaas_foundation.constraints import _inner_diameter_mm
from jewelry_gaas_foundation.constraints import validate as validate_constraints
from jewelry_gaas_foundation.validation import validate_job_spec

NICHE = (
    "one-center-stone engagement ring: a typed center stone in a prong, basket or "
    "bezel head (or a halo / hidden halo) on a plain, cathedral or pave shank"
)

# Calibrated 2026-11-22 against RhinoArtisan MCP 7.1.0.10 (QuickGems kernel):
# pavilion depth (girdle plane -> culet) / total depth. Measured from gem bounding
# boxes; constant across widths and depths for each shape.
PAVILION_FRACTION: dict[str, float] = {
    "round": 0.7337,
    "oval": 0.7406,
    "cushion": 0.7426,
    "emerald": 0.7696,
    "princess": 0.8004,
    "pear": 0.7406,
    "radiant": 0.7937,
    "marquise": 0.7406,
}

ARTISAN_SHAPE: dict[str, str] = {shape: shape.upper() for shape in PAVILION_FRACTION}

SHANK_STYLES = ("plain", "cathedral", "pave")
HEAD_STYLES = ("prong", "basket", "bezel")
HALO_STYLES = ("none", "single", "hidden")

# --- Halo (create_halo), calibrated live 2026-11-22 ------------------------------
# The halo brings its own center prongs and a bridge, so it REPLACES the head. With
# Artisan's default gap the channel floats clear of the prongs (2 bodies); 0.1 mm
# against 1.0 mm prongs makes one solid. The channel spans girdle -> girdle - 1.5;
# the bridge's lowest point sits HALO_BRIDGE_OFFSET_MM under ``channel bottom -
# height_to_bridge``, so height_to_bridge is derived to sink it into the shank.
HALO_SHAPES = ("round", "oval", "cushion", "emerald", "radiant", "pear")
HALO_DISTANCE_TO_GEM_MM = 0.1
HALO_CENTER_PRONG_DIAMETER_MM = 1.0
HALO_CHANNEL_DEPTH_MM = 1.5
HALO_CHANNEL_WIDTH_MM = 1.6  # radial: the halo's outer edge sits ~gem r + 0.1 + 1.6
HALO_BRIDGE_OFFSET_MM = 0.39
HALO_MIN_BRIDGE_MM = 0.5
# The bridges converge under the culet; their top sits ~0.95 mm over their lowest
# point, so the culet clearance is measured from there (live: 0.03 mm culet contact
# when the clearance was taken from the band's top instead).
HALO_BRIDGE_THICKNESS_MM = 1.0
HALO_CENTER_PRONGS = {"pear": 5}  # else 4

# --- Hidden halo (create_hidden_halo), calibrated live 2026-11-22 ----------------
# Artisan's hidden halo also carries its own 4-prong head, whose prongs always end
# 5.7 mm under the girdle (the document ring size doesn't move them). The girdle is
# raised so they end at the head's usual embed depth inside the shank, never in the
# finger hole; the channel is lifted to 1.2 mm under the girdle (default 3.4 sits
# under the culet and reads as a gallery ring, not a hidden halo).
HIDDEN_HALO_SHAPES = ("round", "oval", "cushion", "princess", "emerald", "radiant", "pear")
HIDDEN_HALO_PRONG_DROP_MM = 5.7
HIDDEN_HALO_CHANNEL_POSITION_MM = 1.2
HIDDEN_HALO_RAIL = {
    "round": "CIRCLE",
    "oval": "OVAL",
    "pear": "OVAL",
    "cushion": "SQUARE",
    "princess": "SQUARE",
    "emerald": "SQUARE",
    "radiant": "SQUARE",
}
HIDDEN_HALO_RAIL_MARGIN_MM = 0.2  # rail = stone outline + margin (non-circle rails)

# --- Pave shank (create_pave_shank) -------------------------------------------
# One row of round melee on the band's top, bead-set, from just outside the head to
# the coverage's end angle (degrees from the top, each side).
PAVE_COVERAGE_END_DEG = {"shoulders": 75.0, "half": 110.0, "three_quarter": 150.0}
PAVE_DEFAULT_COVERAGE = "shoulders"
PAVE_DEFAULT_MELEE_MM = 1.3
PAVE_WALL_MM = 0.3  # metal each side of a melee (funnel guard: melee <= width - 2 walls)
PAVE_HEAD_CLEARANCE_MM = 0.6  # first melee starts this far outside the head's footprint
PAVE_BEZEL_WALL_MM = 0.8  # extra footprint for a bezel cup's wall
HALO_MELEE_DEFAULT_MM = 1.2
HIDDEN_HALO_MELEE_DEFAULT_MM = 1.3

# Pointed stones need V-tip prongs at their points (constraints PRONG-003 advisory:
# round tips "will crack the stone"). Artisan's peghead has no V-tip, so pointed
# stones always go through the basket kernel with ``v_prongs``; a "prong" setting
# keeps only the base rail — without it the side prongs float clear of a 2 mm band.
POINTED_SHAPES = ("pear", "marquise")

# Artisan factory bezel wall height above the girdle (measured 2026-11-22); passed
# explicitly so the derived bezel height is deterministic.
BEZEL_GIRDLE_HEIGHT_MM = 0.3

# Cathedral shank calibration (Artisan factory defaults, measured 2026-11-22):
# the bridge under the head tops out 0.61 mm above the finger circle, and the arm
# crests sit 0.70 mm above ``inner_radius + arm_height``.
CATHEDRAL_DEFAULT_ARM_HEIGHT_MM = 3.5
CATHEDRAL_BRIDGE_TOP_MM = 0.61
CATHEDRAL_ARM_CREST_OFFSET_MM = 0.70
# Arms open slightly narrower than the stone so the head's prongs overlap them.
CATHEDRAL_ARM_OVERLAP_MM = 0.5

# Artisan's factory basket scales its prong tip height over the girdle to ~6% of
# the stone width (0.39 mm on a 6.5 mm round). Passed explicitly so the derived
# head height is deterministic.
BASKET_OVER_GIRDLE_RATIO = 0.06

GEOMETRY_GATES = (
    "minimum_structural_floors",
    "stone_metal_collision",
    "stone_stone_collision",
    "mountability_and_setting_access",
    "mesh_closedness_and_manifold_suitability",
)


class UnsupportedSpecError(ValueError):
    """The spec is valid but outside the MVP niche (or the Artisan vocabulary)."""


class SpecRejectedError(ValueError):
    """The spec failed schema validation or a HARD constraint."""

    def __init__(self, message: str, issues: list[str]):
        super().__init__(message + ": " + "; ".join(issues))
        self.issues = issues


@dataclass(frozen=True)
class MountingOptions:
    """Build-time fit options (and overrides of spec mounting axes).

    ``shank_style`` overrides ``ring.shank_style`` when set; ``None`` uses the
    spec's value, else ``plain``. For prong sizes, ``None`` means "leave it to
    Artisan's (or the user's saved) default".
    """

    shank_style: str | None = None
    culet_clearance_mm: float = 0.5  # design default, NOT a certified clearance
    head_embed_mm: float = 0.4  # how deep the head's base sinks into the shank
    peghead_over_girdle_mm: float = 1.0  # Artisan factory peghead default
    # Artisan's peghead seat cone pokes ~0.035 mm into the culet at gem_inside=0;
    # gem_inside=-0.1 drops the seat clear of the pavilion and opens a light hole
    # under the culet (measured 2026-11-22). Design default, not certified.
    seat_clearance_mm: float = 0.1
    prong_thickness_mm: float | None = None  # peghead prong thickness
    prong_diameter_mm: float | None = None  # basket prong diameter

    def __post_init__(self) -> None:
        if self.shank_style is not None and self.shank_style not in SHANK_STYLES:
            raise UnsupportedSpecError(
                f"shank_style must be one of {SHANK_STYLES}, got {self.shank_style!r}"
            )
        if self.culet_clearance_mm < 0:
            raise ValueError("culet_clearance_mm must be >= 0")
        if self.head_embed_mm <= 0:
            raise ValueError("head_embed_mm must be > 0 for a sound union")
        if self.seat_clearance_mm < 0:
            raise ValueError("seat_clearance_mm must be >= 0")


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    tool: str
    args: dict[str, Any]
    bind: str | None = None
    purpose: str = ""


@dataclass(frozen=True)
class BuildPlan:
    job_id: str
    niche: str
    artisan_metal: str
    steps: tuple[PlanStep, ...]
    derived: dict[str, Any]
    options: MountingOptions
    warnings: tuple[str, ...] = field(default_factory=tuple)  # SOFT constraint warnings
    notes: tuple[str, ...] = field(default_factory=tuple)  # internal fit adjustments

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "niche": self.niche,
            "artisan_metal": self.artisan_metal,
            "options": asdict(self.options),
            "derived": self.derived,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "steps": [asdict(step) for step in self.steps],
        }


# ---------------------------------------------------------------------------
# Vocabulary mapping
# ---------------------------------------------------------------------------

_GOLD_COLORS = ("yellow", "white", "rose", "green")


def artisan_metal(metal: dict[str, Any]) -> str:
    """Map a canonical ``metal`` block to an Artisan metal enum name."""
    mtype = str(metal.get("type") or "").lower()
    alloy = str(metal.get("alloy") or "").lower().replace("_", " ")

    if mtype == "platinum" or "platinum" in alloy or alloy.startswith("pt"):
        return "PLATINUM_950" if "950" in alloy else "PLATINUM"
    if mtype == "silver" or "silver" in alloy:
        return "SILVER_925" if ("925" in alloy or "sterling" in alloy) else "SILVER"
    if mtype == "gold" or "gold" in alloy or re.search(r"\d+\s*k", alloy):
        karat_match = re.search(r"\b(9|10|14|18|22|24)\s*k", alloy)
        if not karat_match:
            raise UnsupportedSpecError(f"gold alloy needs a karat: {alloy!r}")
        karat = int(karat_match.group(1))
        if karat == 24:
            return "GOLD_24"
        color = next((c for c in _GOLD_COLORS if c in alloy), None)
        if color is None:
            raise UnsupportedSpecError(f"gold alloy needs a color: {alloy!r}")
        if karat == 22 and color != "yellow":
            raise UnsupportedSpecError("Artisan only has 22k in yellow gold")
        return f"GOLD_{color.upper()}_{karat}"
    raise UnsupportedSpecError(f"unsupported metal: type={mtype!r} alloy={alloy!r}")


def finger_diameter_mm(ring: dict[str, Any]) -> float:
    """Inner (finger) diameter from the canonical ``ring`` block (US or EU)."""
    standard = ring.get("size_standard")
    if standard == "US":
        diameter = _inner_diameter_mm(ring)
        if diameter is None:
            raise UnsupportedSpecError(f"unreadable US ring size: {ring.get('size')!r}")
        return diameter
    if standard == "EU":
        # EU size = inner circumference in mm.
        return float(ring["size"]) / math.pi
    raise UnsupportedSpecError(
        f"ring size standard {standard!r} not supported by the MVP (US, EU)"
    )


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def compile_solitaire_plan(
    spec: dict[str, Any],
    options: MountingOptions | None = None,
    *,
    validate: bool = True,
) -> BuildPlan:
    """Compile a canonical one-center-stone spec into an ordered RhinoArtisan build plan.

    Covers the solitaire (prong / basket / bezel head on a plain, cathedral or pave
    shank) and the halo archetype (``halo_style`` single or hidden). The name is kept
    for the callers that predate halos.
    """
    options = options or MountingOptions()
    warnings: list[str] = []

    if validate:
        _require_valid(spec, warnings)

    job = spec.get("job") or {}
    archetype = job.get("archetype")
    if archetype not in ("solitaire", "halo"):
        raise UnsupportedSpecError("the builder covers solitaire and halo rings only")

    setting = spec.get("setting") or {}
    head = setting.get("center_setting")
    if head not in HEAD_STYLES:
        raise UnsupportedSpecError(
            f"center_setting {head!r} outside the MVP niche {HEAD_STYLES}"
        )
    halo_style = setting.get("halo_style") or "none"
    if halo_style not in HALO_STYLES:
        raise UnsupportedSpecError(f"halo_style {halo_style!r} not supported {HALO_STYLES}")
    if (archetype == "halo") != (halo_style != "none"):
        raise UnsupportedSpecError("a halo ring needs halo_style single or hidden (and only a halo ring)")
    if setting.get("side_stone_layout", "none") != "none":
        raise UnsupportedSpecError("side stones are outside the one-center-stone builder")
    if halo_style != "none" and head == "bezel":
        raise UnsupportedSpecError("a halo needs a prong head, not a bezel (STONE-005)")

    stones = spec.get("stones") or []
    centers = [s for s in stones if s.get("role") == "center"]
    if len(centers) != 1:
        raise UnsupportedSpecError("exactly one center stone is required")
    stone = centers[0]
    shape = str(stone.get("shape") or "").lower()
    if shape not in PAVILION_FRACTION:
        raise UnsupportedSpecError(
            f"stone shape {shape!r} not calibrated; supported: {sorted(PAVILION_FRACTION)}"
        )
    if halo_style == "single" and shape not in HALO_SHAPES:
        raise UnsupportedSpecError(f"the halo isn't available for {shape} stones")
    if halo_style == "hidden" and shape not in HIDDEN_HALO_SHAPES:
        raise UnsupportedSpecError(f"the hidden halo isn't available for {shape} stones")
    dims = stone.get("dimensions_mm") or {}
    try:
        length = float(dims["length"])
        width = float(dims["width"])
        depth = float(dims["depth"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedSpecError("stone length/width/depth (mm) are required") from exc

    ring = spec.get("ring") or {}
    band_width = float(ring["band_width_mm"])
    band_thickness = float(ring["band_thickness_mm"])
    finger_d = round(finger_diameter_mm(ring), 3)
    inner_r = finger_d / 2.0
    metal = artisan_metal(spec.get("metal") or {})

    shank_style = options.shank_style or ring.get("shank_style") or "plain"
    if shank_style not in SHANK_STYLES:
        raise UnsupportedSpecError(
            f"shank_style {shank_style!r} outside the MVP niche {SHANK_STYLES}"
        )
    if halo_style != "none" and shank_style == "cathedral":
        raise UnsupportedSpecError("halos are built on a plain or pave band, not a cathedral")

    melee_halo = _melee_size(stones, "halo")
    melee_pave = _melee_size(stones, "accent")
    if shank_style == "pave" and melee_pave is None:
        melee_pave = PAVE_DEFAULT_MELEE_MM
    coverage = ring.get("pave_coverage") or PAVE_DEFAULT_COVERAGE
    if shank_style == "pave" and coverage not in PAVE_COVERAGE_END_DEG:
        raise UnsupportedSpecError(f"pave_coverage {coverage!r} not in {tuple(PAVE_COVERAGE_END_DEG)}")

    pavilion = PAVILION_FRACTION[shape] * depth
    clearance = options.culet_clearance_mm
    notes: list[str] = []

    if shank_style in ("plain", "pave"):
        seat_z = inner_r + band_thickness
        seat_thickness = band_thickness
        girdle_z = seat_z + clearance + pavilion
    else:
        seat_z = inner_r + CATHEDRAL_BRIDGE_TOP_MM
        seat_thickness = CATHEDRAL_BRIDGE_TOP_MM
        arm_crest_z = inner_r + CATHEDRAL_DEFAULT_ARM_HEIGHT_MM + CATHEDRAL_ARM_CREST_OFFSET_MM
        girdle_z = max(arm_crest_z, seat_z + clearance + pavilion)

    # Never let the head's base reach the finger: sink it at most half the seat.
    embed = min(options.head_embed_mm, 0.5 * seat_thickness)
    if embed < options.head_embed_mm:
        notes.append(
            f"head embed reduced to {embed:.3f} mm to stay clear of the finger"
        )
    head_bottom_z = seat_z - embed

    halo_bridge = None
    if halo_style == "single":
        girdle_z = max(girdle_z, head_bottom_z + HALO_BRIDGE_THICKNESS_MM + clearance + pavilion)
        halo_bridge = (
            girdle_z - HALO_CHANNEL_DEPTH_MM - HALO_BRIDGE_OFFSET_MM - head_bottom_z
        )
        if halo_bridge < HALO_MIN_BRIDGE_MM:
            girdle_z += HALO_MIN_BRIDGE_MM - halo_bridge
            halo_bridge = HALO_MIN_BRIDGE_MM
            notes.append("stone raised so the halo's bridge clears the band")
    elif halo_style == "hidden":
        raised = head_bottom_z + HIDDEN_HALO_PRONG_DROP_MM
        if raised > girdle_z:
            notes.append(
                f"stone raised {raised - girdle_z:.2f} mm so the hidden halo's prongs end in the band"
            )
            girdle_z = raised

    steps: list[PlanStep] = [
        PlanStep(
            "metal",
            "set_design_metal",
            {"metal": metal, "slot": 1},
            purpose="Metal 01 = the design alloy (weight + price)",
        )
    ]

    pave_start = pave_end = None
    if shank_style == "plain":
        steps.append(
            PlanStep(
                "shank",
                "create_ring",
                {
                    "finger_diameter": finger_d,
                    "width": band_width,
                    "height": band_thickness,
                },
                bind="shank",
                purpose="comfort-fit plain shank at the typed ring size",
            )
        )
    elif shank_style == "pave":
        outer_r = inner_r + band_thickness
        footprint = width / 2.0 + PAVE_HEAD_CLEARANCE_MM
        if halo_style == "single":
            footprint += HALO_DISTANCE_TO_GEM_MM + HALO_CHANNEL_WIDTH_MM
        elif head == "bezel":
            # The bezel cup's wall sits outside the girdle; without this the first bead
            # prong touched it (oval 1 ct, shoulders: non-manifold STL, live 2026-11-23).
            footprint += PAVE_BEZEL_WALL_MM
        pave_start = round(math.degrees(math.asin(min(1.0, footprint / outer_r))), 1)
        pave_end = PAVE_COVERAGE_END_DEG[coverage]
        if pave_start >= pave_end - 10.0:
            raise UnsupportedSpecError("the stone covers the band's shoulders: no room for pave")
        steps.append(
            PlanStep(
                "shank",
                "create_pave_shank",
                {
                    "finger_diameter": finger_d,
                    "width": band_width,
                    "height": band_thickness,
                    "rows": 1,
                    "gem_size": melee_pave,
                    "layout_mode": "SIDE_ANGLE",
                    "start_angle": pave_start,
                    "end_angle": pave_end,
                },
                bind="shank",
                purpose=f"pave band: one row of {melee_pave:g} mm melee, {coverage.replace('_', '-')}",
            )
        )
    else:
        steps.append(
            PlanStep(
                "shank",
                "create_cathedral",
                {
                    "finger_diameter": finger_d,
                    "bottom_width": band_width,
                    "bottom_height": band_thickness,
                    "arm_width": round(max(width - CATHEDRAL_ARM_OVERLAP_MM, 1.0), 3),
                },
                bind="shank",
                purpose="cathedral shank; arms open just inside the stone's girdle",
            )
        )

    steps.append(
        PlanStep(
            "stone",
            "add_gem_by_size",
            {
                "shape": ARTISAN_SHAPE[shape],
                "size_x": width,
                "size_y": length,
                "size_z": depth,
                "plane": [0.0, 0.0, round(girdle_z, 4)],
            },
            bind="gem",
            purpose="typed center stone, girdle placed for the culet clearance",
        )
    )

    prongs = setting.get("prong_count")
    prong_args: dict[str, Any] = {}
    if isinstance(prongs, int) and prongs > 0:
        prong_args["prongs"] = prongs

    pointed = shape in POINTED_SHAPES
    if halo_style == "single":
        head_kernel = "halo"
    elif halo_style == "hidden":
        head_kernel = "hidden_halo"
    elif head == "bezel":
        head_kernel = "bezel"
    elif head == "basket" or pointed:
        head_kernel = "basket"
    else:
        head_kernel = "peghead"

    if head_kernel == "halo":
        halo_args: dict[str, Any] = {
            "gem_id": {"$ref": "gem.id"},
            "prong_count": HALO_CENTER_PRONGS.get(shape, 4),
            "prong_diameter": HALO_CENTER_PRONG_DIAMETER_MM,
            "distance_to_gem": HALO_DISTANCE_TO_GEM_MM,
            "extend_to_bridge": True,
            "height_to_bridge": round(halo_bridge, 4),
        }
        if melee_halo is not None:
            halo_args["stone_size"] = melee_halo
        steps.append(
            PlanStep(
                "head",
                "create_halo",
                halo_args,
                bind="head",
                purpose="halo of melee around the stone, with its own center prongs bridged into the band",
            )
        )
    elif head_kernel == "hidden_halo":
        hidden_args: dict[str, Any] = {
            "gem_id": {"$ref": "gem.id"},
            "channel_position_z": HIDDEN_HALO_CHANNEL_POSITION_MM,
            "rail_shape": HIDDEN_HALO_RAIL[shape],
        }
        if HIDDEN_HALO_RAIL[shape] != "CIRCLE":
            hidden_args["rail_x_length"] = round(width + HIDDEN_HALO_RAIL_MARGIN_MM, 3)
            hidden_args["rail_y_length"] = round(length + HIDDEN_HALO_RAIL_MARGIN_MM, 3)
        if melee_halo is not None:
            hidden_args["stone_size"] = melee_halo
        steps.append(
            PlanStep(
                "head",
                "create_hidden_halo",
                hidden_args,
                bind="head",
                purpose="hidden halo under the girdle, on its own 4-prong head sunk into the band",
            )
        )
    elif head_kernel == "bezel":
        steps.append(
            PlanStep(
                "head",
                "add_bezel_setting",
                {
                    "gem_id": {"$ref": "gem.id"},
                    "height": round(girdle_z + BEZEL_GIRDLE_HEIGHT_MM - head_bottom_z, 4),
                    "girdle_height": BEZEL_GIRDLE_HEIGHT_MM,
                },
                bind="head",
                purpose="parametric bezel cup around the stone, base sunk into the shank",
            )
        )
    elif head_kernel == "peghead":
        over = options.peghead_over_girdle_mm
        head_args: dict[str, Any] = {
            "gem_id": {"$ref": "gem.id"},
            **prong_args,
            "height": round(girdle_z + over - head_bottom_z, 4),
            "over_girdle_height": over,
            "gem_inside": -options.seat_clearance_mm,
        }
        if options.prong_thickness_mm is not None:
            head_args["thickness"] = options.prong_thickness_mm
        steps.append(
            PlanStep(
                "head",
                "add_prong_setting",
                head_args,
                bind="head",
                purpose="parametric peghead around the stone, base sunk into the shank",
            )
        )
    else:
        over = round(BASKET_OVER_GIRDLE_RATIO * width, 3)
        head_args = {
            "gem_ids": [{"$ref": "gem.id"}],
            **prong_args,
            "prong_height": round(girdle_z + over - head_bottom_z, 4),
            "prong_height_over_girdle": over,
        }
        if options.prong_diameter_mm is not None:
            head_args["prong_diameter"] = options.prong_diameter_mm
        if pointed:
            head_args["v_prongs"] = True
        if head == "prong":
            # Prong look on the basket kernel: drop the upper rail, keep the base
            # rail that ties every prong to the shank.
            head_args["upper_rail"] = False
            head_args["base_rail"] = True
        steps.append(
            PlanStep(
                "head",
                "add_basket_setting",
                head_args,
                bind="head",
                purpose=(
                    "V-tip prong head (basket kernel, base rail only)"
                    if head == "prong"
                    else "parametric basket (prongs + rails)"
                )
                + ", base sunk into the shank",
            )
        )

    simple = shank_style != "pave" and head_kernel in ("peghead", "basket", "bezel")
    steps.append(
        PlanStep(
            "union",
            "boolean_operation",
            {
                "operation": "UNION",
                # Halos and pave bands return metal and melee ids together (and pave
                # bead prongs as separate solids): union every metal solid instead.
                "target_ids": (
                    {"$refs": ["shank.id", "head.ids"]} if simple else {"$metal": "all"}
                ),
            },
            bind="metal",
            purpose="one castable metal body; operands kept for re-editing",
        )
    )

    derived = {
        "finger_diameter_mm": finger_d,
        "stone_shape": shape,
        "stone_mm": {"length": length, "width": width, "depth": depth},
        "pavilion_depth_mm": round(pavilion, 4),
        "seat_z_mm": round(seat_z, 4),
        "girdle_z_mm": round(girdle_z, 4),
        "expected_culet_z_mm": round(girdle_z - pavilion, 4),
        "expected_culet_clearance_mm": round(girdle_z - pavilion - seat_z, 4),
        "head_bottom_z_mm": round(head_bottom_z, 4),
        "head_embed_mm": round(embed, 4),
        "head_style": head,
        "shank_style": shank_style,
        "head_kernel": head_kernel,
        "halo_style": halo_style,
        "v_tips": pointed and head_kernel == "basket",
    }
    if shank_style == "pave":
        derived["pave"] = {
            "melee_mm": melee_pave,
            "coverage": coverage,
            "start_angle_deg": pave_start,
            "end_angle_deg": pave_end,
        }
    if halo_style == "single":
        derived["halo"] = {"melee_mm": melee_halo or HALO_MELEE_DEFAULT_MM,
                           "height_to_bridge_mm": round(halo_bridge, 4)}
    elif halo_style == "hidden":
        derived["halo"] = {"melee_mm": melee_halo or HIDDEN_HALO_MELEE_DEFAULT_MM,
                           "channel_under_girdle_mm": HIDDEN_HALO_CHANNEL_POSITION_MM}

    return BuildPlan(
        job_id=str(job.get("job_id") or "job"),
        niche=NICHE,
        artisan_metal=metal,
        steps=tuple(steps),
        derived=derived,
        options=options,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


compile_ring_plan = compile_solitaire_plan


def _melee_size(stones: list[dict[str, Any]], role: str) -> float | None:
    """Melee size (mm) of the first ``role`` stone record, or None when absent."""
    for stone in stones:
        if stone.get("role") == role:
            dims = stone.get("dimensions_mm") or {}
            try:
                return float(dims.get("width") or dims["length"])
            except (KeyError, TypeError, ValueError) as exc:
                raise UnsupportedSpecError(f"{role} stones need a size in mm") from exc
    return None


def _require_valid(spec: dict[str, Any], warnings: list[str]) -> None:
    schema = validate_job_spec(spec)
    errors = [f"{i.code} {i.path}: {i.message}" for i in schema.issues if i.severity == "error"]
    if errors:
        raise SpecRejectedError("schema validation failed", errors)
    constraints = validate_constraints(spec)
    if not constraints.accepted:
        raise SpecRejectedError(
            "HARD constraint violated",
            [f"{v.rule_id}: {v.message}" for v in constraints.hard_violations],
        )
    warnings.extend(f"{v.rule_id}: {v.message}" for v in constraints.soft_warnings)
