"""Jewelry domain constraint engine for canonical spec v0.1.

All rule thresholds and formulas cite their authoritative source in code comments.
Rules without published standards are documented as heuristics or moved to schema_gaps.

Separate from validation.py (JSON Schema + archetype contract checks).
Operates on raw spec dicts; only schema/product-contract contradictions reject.

M2 Revision (2026-05-20): All CRITICAL/MAJOR findings from m2_critique_report.md resolved.
  - CLEARANCE-001/002/003: moved to schema_gaps (no published derivation formula)
  - STONE-009: moved to schema_gaps (wrong physics; no published spacing standard)
  - METAL-001/002: downgraded HARD→SOFT (1.63mm/1.29mm thresholds definitively wrong)
  - WEIGHT-001: shape-specific carat factors (GIA-aligned)
  - SETTING-001: oval/cushion downgraded HARD→SOFT (industry sources confirm compatibility)
  - PRONG-003: added count enforcement for pear (SOFT) and marquise (SOFT)
  - PRONG-006: removed (fires as noise; prong_style is v0.2 gap)
  - BAND-008, BAND-009: new advisory rules from knowledge base §5.1

Goal 0003 audit (2026-07-24): authoritative-value and HARD-tier re-verification.
  - HARD is limited to eight schema/product-contract contradictions
  - Empirical dimensional and setting rules demoted to SOFT/ADVISORY
  - BAND-005 moved to schema_gaps (finger-bore proxy is physically invalid)
  - GIA AGD formulas and L:W-dependent factor tables implemented
  - STONE-005 bezel+halo false rejection removed

Schema v0.2 gaps (comment-only, NOT implemented as code):
  # Legacy gap: ring.profile remains free-text. Goal 0007 modular specs add
  # ring.band_profile/shoulder_style; rules prefer them and retain substring
  # matching only for older canonical specs.
  # v0.2 GAP: setting.prong_style enum missing — PRONG-003 degrades to ADVISORY; PRONG-004 partial
  # v0.2 GAP: setting.profile_style enum missing — SETTING-003/004 partially enforced
  # v0.2 GAP: stone.shape "heart" not in v0.1 enum — STONE-011 rejects it
  # v0.2 GAP: setting.halo_radius_mm missing — STONE-002/003/004 deferred to geometry gate
  # v0.2 GAP: gallery_height_mm missing — CLEARANCE-001/002 deferred to geometry gate
  # v0.2 GAP: prong_seat_depth_mm missing — CLEARANCE-003 deferred to geometry gate
  # v0.2 GAP: setting head positions missing — STONE-009 deferred to geometry gate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures (spec §7)
# ---------------------------------------------------------------------------

@dataclass
class ConstraintViolation:
    rule_id: str           # e.g., "BAND-001"
    tier: str              # "HARD", "SOFT", "ADVISORY"
    parameters: list[str]  # e.g., ["ring.band_width_mm"]
    message: str           # user-facing error/warning text
    suggested_fix: str     # nearest valid alternative


@dataclass
class ConstraintResult:
    accepted: bool
    hard_violations: list[ConstraintViolation] = field(default_factory=list)
    soft_warnings: list[ConstraintViolation]   = field(default_factory=list)
    advisory_notes: list[ConstraintViolation]  = field(default_factory=list)
    schema_gaps: list[str]                     = field(default_factory=list)
    # accepted = True ONLY when hard_violations is empty


# ---------------------------------------------------------------------------
# Carat estimation — shape-specific factors
# ---------------------------------------------------------------------------

# SOURCE: GIA Diamond Grading Lab Manual (2019), pp. 265-267.  These are
# dimensional estimates, never substitutes for a scale or grading report.
# Round and oval use average-girdle-diameter squared; elongated fancy shapes
# use L x W x D and an L:W-ratio-dependent factor.  Cushion's 0.00815 is a
# trade formula retained as an estimate because the cited GIA table does not
# publish a cushion formula.
SHAPE_FACTORS: dict[str, float] = {
    "round":    0.0061,
    "oval":     0.0062,
    "princess": 0.0083,
    "cushion":  0.00815,
    # Representative values only; _shape_factor() uses the GIA L:W tables.
    "pear":     0.0060,   # 1.50:1
    "marquise": 0.0058,   # 2.00:1
    "radiant":  0.00836,  # 1.15:1, interpolated
}


def _piecewise_factor(ratio: float, points: tuple[tuple[float, float], ...]) -> float:
    """Linearly interpolate a clamped factor table."""
    if ratio <= points[0][0]:
        return points[0][1]
    if ratio >= points[-1][0]:
        return points[-1][1]
    for (left_ratio, left_factor), (right_ratio, right_factor) in zip(points, points[1:]):
        if ratio <= right_ratio:
            fraction = (ratio - left_ratio) / (right_ratio - left_ratio)
            return left_factor + fraction * (right_factor - left_factor)
    return points[-1][1]


def _emerald_carat_factor(length: float, width: float) -> float:
    """Return the GIA emerald/radiant factor interpolated by L:W ratio.

    GIA table: 1.00→0.0080, 1.50→0.0092, 2.00→0.0100,
    2.50→0.0106.  Values outside the table are clamped.
    """
    if width <= 0:
        return 0.0092  # fallback for typical 1.5:1 emerald
    return _piecewise_factor(length / width, (
        (1.00, 0.0080),
        (1.50, 0.0092),
        (2.00, 0.0100),
        (2.50, 0.0106),
    ))


def _shape_factor(shape: str, length: float, width: float) -> float:
    """Return the GIA/trade dimensional-estimation factor for a shape."""
    ratio = length / width if width > 0 else 1.0
    if shape in ("emerald", "radiant"):
        return _emerald_carat_factor(length, width)
    if shape == "marquise":
        return _piecewise_factor(ratio, (
            (1.50, 0.00565),
            (2.00, 0.00580),
            (2.50, 0.00585),
            (3.00, 0.00595),
        ))
    if shape == "pear":
        return _piecewise_factor(ratio, (
            (1.25, 0.00615),
            (1.50, 0.00600),
            (1.66, 0.00590),
            (2.00, 0.00575),
        ))
    return SHAPE_FACTORS.get(shape, SHAPE_FACTORS["round"])


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _derive_carat(stone: dict[str, Any]) -> float | None:
    """Return stated carat or a qualified dimensional estimate."""
    c = stone.get("carat")
    if c is not None:
        try:
            return float(c)
        except (TypeError, ValueError):
            pass
    dims = stone.get("dimensions_mm", {}) or {}
    try:
        length = float(dims["length"])
        width  = float(dims["width"])
        depth  = float(dims["depth"])
    except (KeyError, TypeError, ValueError):
        return None

    shape = str(stone.get("shape") or "").lower()
    factor = _shape_factor(shape, length, width)
    if shape in ("round", "oval"):
        average_girdle_diameter = (length + width) / 2.0
        return average_girdle_diameter ** 2 * depth * factor
    return length * width * depth * factor


def _inner_diameter_mm(ring: dict[str, Any]) -> float | None:
    """Return inner ring diameter for US size only.

    The linear North-American conversion matches Stuller's published US-size
    diameter chart to its 0.01 mm precision.  Non-US standards return None.
    """
    if ring.get("size_standard") != "US":
        return None
    try:
        return 11.63 + 0.8128 * float(ring["size"])
    except (KeyError, TypeError, ValueError):
        return None


def _is_platinum(alloy: str) -> bool:
    al = alloy.lower()
    return "platinum" in al or al.startswith("pt")


def _stone_schedule_quantity(stone: dict[str, Any]) -> int:
    """Return the number of stones represented by one schedule record."""
    quantity = stone.get("quantity", 1)
    if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
        return quantity
    return 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Threshold:
    """A rule threshold plus the wording that states where it came from.

    Goal 0013 Part B. With no active shop profile every method returns exactly the
    wording these rules used before profiles existed, so behavior is unchanged
    until a shop commits numbers in writing. A profile only changes which number a
    check runs against and how the message attributes it -- it never changes a
    rule's severity, never removes a violation, and never certifies manufacturing.
    """

    limit: float
    calibrated: bool
    profile_label: str | None = None
    source: str | None = None

    def describe(self, label: str) -> str:
        if not self.calibrated:
            return f"MyJewl's uncalibrated {self.limit} mm {label}"
        return (
            f"the calibrated {self.limit} mm {label} from shop profile "
            f"{self.profile_label}"
        )

    def consequence(self, uncalibrated_text: str) -> str:
        if not self.calibrated:
            return uncalibrated_text
        return (
            f"Threshold supplied by shop profile {self.profile_label} "
            f"(source: {self.source}). This is calibration input for review, "
            "not a manufacturing verdict."
        )

    def remedy_suffix(self) -> str:
        if not self.calibrated:
            return " or obtain calibrated shop approval."
        return f" per shop profile {self.profile_label}."

    def attribution(self) -> str:
        return "" if not self.calibrated else f" (shop profile {self.profile_label})"


def _calibrated_threshold(key: str, default: float, alloy: str | None) -> _Threshold:
    """Resolve a threshold from the active shop profile, else keep the baseline."""
    try:
        from jewelry_gaas_foundation.shop_profile import active_threshold
    except ImportError:  # pragma: no cover - shop profiles are optional at import
        return _Threshold(default, False)
    resolved = active_threshold(key, alloy=alloy)
    if not resolved.usable or resolved.value is None:
        return _Threshold(default, False)
    return _Threshold(
        float(resolved.value), True, resolved.profile_id, resolved.source
    )


def validate(spec: dict[str, Any]) -> ConstraintResult:
    """Run all constraint rules against a canonical spec dict.

    HARD (schema/product-contract contradictions only):
          PRONG-009, SETTING-002, STONE-007/008/011, ARCH-001/002/003

    SOFT: BAND-001/002/003/004/006/007, PRONG-001/002/003/004/007,
          METAL-001/002/003, SETTING-001/003/004, STONE-001/006,
          WEIGHT-001, RING-001

    ADVISORY: BAND-008, BAND-009, PRONG-003 (V-tip, v0.1 degraded),
              PRONG-005, PRONG-008, STONE-005/010, SIDE-001

    Schema-gapped (always documented, never evaluated as active checks):
      BAND-005, CLEARANCE-001/002/003, STONE-002/003/004/009,
      PRONG-006, METAL-001/002 (gap note)
    """
    hard: list[ConstraintViolation] = []
    soft: list[ConstraintViolation] = []
    advisory: list[ConstraintViolation] = []
    gaps: list[str] = []

    # --- Parse helpers -------------------------------------------------
    ring     = spec.get("ring") or {}
    metal    = spec.get("metal") or {}
    setting  = spec.get("setting") or {}
    stones   = spec.get("stones") or []
    job      = spec.get("job") or {}
    archetype = str(job.get("archetype") or "")

    center_stones = [s for s in stones if isinstance(s, dict) and s.get("role") == "center"]
    halo_stones   = [s for s in stones if isinstance(s, dict) and s.get("role") == "halo"]
    side_stones   = [s for s in stones if isinstance(s, dict) and s.get("role") == "side"]
    center        = center_stones[0] if center_stones else {}
    center_dims   = center.get("dimensions_mm") or {}

    band_width     = ring.get("band_width_mm")
    band_thickness = ring.get("band_thickness_mm")
    profile        = str(ring.get("profile") or "").lower()
    band_profile   = str(ring.get("band_profile") or "").lower()
    shoulder_style = str(ring.get("shoulder_style") or "").lower()

    center_setting = str(setting.get("center_setting") or "").lower()
    prong_count    = setting.get("prong_count")
    halo_style     = str(setting.get("halo_style") or "").lower()
    side_layout    = str(setting.get("side_stone_layout") or "").lower()

    metal_type  = str(metal.get("type") or "").lower()
    metal_alloy = str(metal.get("alloy") or "")

    center_shape = str(center.get("shape") or "").lower()

    try:
        center_length = float(center_dims.get("length") or 0)
    except (TypeError, ValueError):
        center_length = 0.0
    try:
        center_width_val = float(center_dims.get("width") or 0)
    except (TypeError, ValueError):
        center_width_val = 0.0
    center_max_dim = max(center_length, center_width_val)

    center_depth: float | None = None
    try:
        center_depth = float(center_dims["depth"])
    except (KeyError, TypeError, ValueError):
        pass

    center_carat = _derive_carat(center)

    # --- Document all schema/geometry-deferred gaps (always emitted) ---
    gaps.append(
        "LEGACY GAP: ring.profile is free-text. Goal 0007 modular specs use "
        "ring.band_profile and ring.shoulder_style; older specs retain "
        "substring fallback for BAND-003/004/SETTING-003/004."
    )
    gaps.append(
        "v0.2 GAP: setting.prong_style enum missing — "
        "PRONG-003 degrades to ADVISORY; PRONG-004 partially enforced"
    )
    gaps.append(
        "v0.2 GAP: setting.profile_style enum missing — "
        "SETTING-003/004 partially enforced"
    )
    gaps.append(
        "v0.2 GAP: stone.shape 'heart' not in v0.1 enum — STONE-011 rejects it"
    )
    gaps.append(
        "v0.2 GAP: setting.halo_radius_mm missing — "
        "STONE-002, STONE-003, STONE-004 deferred to geometry gate (stone_stone_collision)"
    )
    gaps.append(
        "BAND-005: Center-stone fit cannot be derived from finger-bore diameter. "
        "The stone sits above the shank; head, shoulder, and setting geometry are required. "
        "Deferred to Rhino mountability and stone-metal geometry gates."
    )
    gaps.append(
        "CLEARANCE-001: Gallery clearance requires gallery_height_mm (GH pipeline output; "
        "v0.2 field gap). No published formula derives clearance from stone depth alone. "
        "Stuller CAD/CAM guidance specifies 0.5 mm culet-to-finger-rail clearance, "
        "which must be measured on geometry. Enforced at geometry gate."
    )
    gaps.append(
        "CLEARANCE-002: Same as CLEARANCE-001 for colored/non-round stones. "
        "No depth-based proxy formula exists in any authoritative source "
        "(Ganoksin, Stuller, GIA). Enforced at geometry gate."
    )
    gaps.append(
        "CLEARANCE-003: Seat bearing depth requires prong_seat_depth_mm (not in schema v0.1). "
        "Authoritative ratio: seat cut ≤ 25% of prong diameter "
        "(Stuller CAD/CAM Production Standards). Enforced at geometry gate."
    )
    gaps.append(
        "STONE-009: Trilogy center-to-side stone clearance requires setting head position "
        "data from GH pipeline (v0.2 gap). No authoritative published minimum gap exists "
        "for trilogy stone spacing. Enforced at geometry gate."
    )
    gaps.append(
        "PRONG-006: V-tip advisory for oval/cushion/radiant prong settings requires "
        "prong_style field (v0.2 gap). Cannot detect V-tip intent from schema v0.1."
    )
    gaps.append(
        "METAL-001/002: Minimum prong diameter for 1ct+ cathedral settings cannot be "
        "verified from spec v0.1 (prong_dia not a spec field). Stuller's 0.45 mm "
        "minimum applies specifically to pinpoint-setting prongs, not all center-stone "
        "prongs. Enforced via calibrated manufacturing-profile review."
    )

    # ===================================================================
    # ARCH rules (checked first)
    # ===================================================================

    # ARCH-001: solitaire must not specify halo
    if archetype == "solitaire" and halo_style not in ("", "none"):
        hard.append(ConstraintViolation(
            "ARCH-001", "HARD",
            ["job.archetype", "setting.halo_style"],
            "Solitaire archetype does not support halo stones. "
            "Remove halo_style or change archetype to 'halo'.",
            "Set halo_style to 'none' or change archetype to 'halo'.",
        ))

    # ARCH-002: solitaire/halo must not specify side_stone_layout
    if archetype in ("solitaire", "halo") and side_layout not in ("", "none"):
        hard.append(ConstraintViolation(
            "ARCH-002", "HARD",
            ["job.archetype", "setting.side_stone_layout"],
            "Side stones are only supported in trilogy archetype. "
            "Remove side_stone_layout or change archetype to 'trilogy'.",
            "Set side_stone_layout to 'none' or change archetype to 'trilogy'.",
        ))

    # ARCH-003: solitaire/halo must not have side-role stones
    if archetype in ("solitaire", "halo"):
        for s in stones:
            if isinstance(s, dict) and s.get("role") == "side":
                sid = s.get("stone_id", "unknown")
                hard.append(ConstraintViolation(
                    "ARCH-003", "HARD",
                    ["job.archetype", "stones[].role"],
                    f"Stone '{sid}' has role 'side', which is only valid for trilogy archetype. "
                    "Change role to 'accent' or change archetype to 'trilogy'.",
                    "Change stone role to 'accent' or archetype to 'trilogy'.",
                ))
                break  # one violation per spec

    # ===================================================================
    # BAND rules
    # ===================================================================

    # BAND-001: band_width_mm ≥ 1.8 (SOFT design baseline).
    # No universal standard supports this as a manufacturing pass/fail boundary.
    _band_001 = _calibrated_threshold("min_shank_width_mm", 1.8, metal_alloy)
    if band_width is not None and band_width < _band_001.limit:
        soft.append(ConstraintViolation(
            "BAND-001", "SOFT",
            ["ring.band_width_mm"],
            f"Band width {band_width} mm is below "
            f"{_band_001.describe('daily-wear design baseline')}. "
            + _band_001.consequence(
                "A shop-specific structural minimum is required."
            ),
            f"Increase ring.band_width_mm to {_band_001.limit}"
            f"{_band_001.remedy_suffix()}",
        ))

    # BAND-002: band_thickness_mm ≥ 1.5 (SOFT design baseline).
    _band_002 = _calibrated_threshold("min_shank_thickness_mm", 1.5, metal_alloy)
    if band_thickness is not None and band_thickness < _band_002.limit:
        soft.append(ConstraintViolation(
            "BAND-002", "SOFT",
            ["ring.band_thickness_mm"],
            f"Band thickness {band_thickness} mm is below "
            f"{_band_002.describe('design baseline')}. "
            + _band_002.consequence(
                "Production loss depends on the shop process."
            ),
            f"Increase ring.band_thickness_mm to {_band_002.limit}"
            f"{_band_002.remedy_suffix()}",
        ))

    # BAND-003: knife-edge profile → band_width ≥ 2.0 (SOFT heuristic)
    # v0.2 GAP: ring.profile is free-text — substring match for "knife"
    is_knife_edge = band_profile == "knife_edge" or "knife" in profile
    _band_003 = _calibrated_threshold("min_knife_edge_band_width_mm", 2.0, metal_alloy)
    if is_knife_edge and band_width is not None and band_width < _band_003.limit:
        soft.append(ConstraintViolation(
            "BAND-003", "SOFT",
            ["ring.band_width_mm", "ring.band_profile", "ring.profile"],
            f"Knife-edge profiles require a minimum band width of "
            f"{_band_003.limit} mm{_band_003.attribution()}. "
            f"Current width {band_width} mm is insufficient.",
            f"Set ring.band_width_mm ≥ {_band_003.limit} or choose a different profile.",
        ))

    # BAND-004: comfort-fit profile → band_thickness ≥ 1.5 (SOFT heuristic)
    # v0.2 GAP: ring.profile is free-text — substring match for "comfort"
    is_comfort_fit = band_profile == "comfort_fit" or "comfort" in profile
    _band_004 = _calibrated_threshold("min_comfort_fit_thickness_mm", 1.5, metal_alloy)
    if is_comfort_fit and band_thickness is not None and band_thickness < _band_004.limit:
        soft.append(ConstraintViolation(
            "BAND-004", "SOFT",
            ["ring.band_thickness_mm", "ring.band_profile", "ring.profile"],
            f"Comfort-fit profiles require a minimum center thickness of "
            f"{_band_004.limit} mm{_band_004.attribution()}. "
            f"Current thickness {band_thickness} mm is insufficient.",
            f"Set ring.band_thickness_mm ≥ {_band_004.limit}.",
        ))

    # BAND-005: removed from active evaluation in the 2026-07-24 audit.
    # The prior finger-bore formula was physically invalid because the center
    # stone sits above the shank.  See the unconditional schema-gap entry.

    # BAND-006: band_width ≥ 20% of stone width (SOFT, ADVISORY for trilogy)
    if center_width_val > 0 and band_width is not None:
        rec = center_width_val * 0.20
        if band_width < rec:
            tier_606 = "ADVISORY" if archetype == "trilogy" else "SOFT"
            v606 = ConstraintViolation(
                "BAND-006", tier_606,
                ["ring.band_width_mm", "stones[center].dimensions_mm.width"],
                f"Band width ({band_width} mm) is less than 20% of stone width "
                f"({center_width_val} mm). The ring will appear visually top-heavy. "
                f"Consider increasing band width to at least {rec:.2f} mm.",
                f"Set ring.band_width_mm ≥ {rec:.2f}.",
            )
            if tier_606 == "ADVISORY":
                advisory.append(v606)
            else:
                soft.append(v606)

    # BAND-007: trilogy → band_width ≥ 2.0 (SOFT design baseline)
    if archetype == "trilogy" and band_width is not None and band_width < 2.0:
        soft.append(ConstraintViolation(
            "BAND-007", "SOFT",
            ["ring.band_width_mm", "job.archetype"],
            f"Trilogy rings require a minimum band width of 2.0 mm. "
            f"Current width {band_width} mm is insufficient.",
            "Set ring.band_width_mm ≥ 2.0 for trilogy archetype.",
        ))

    # BAND-008: comfort-fit sizing advisory
    # SOURCE: Jewelry industry consensus (parameter_constraints_and_validation.md §5.1).
    # Fires when profile contains "comfort" AND ring size is a whole number (not half-size).
    if is_comfort_fit:
        try:
            sz = float(ring.get("size") or 0)
            if sz > 0 and sz == int(sz):  # integer size, not a half-size
                advisory.append(ConstraintViolation(
                    "BAND-008", "ADVISORY",
                    ["ring.profile", "ring.size"],
                    "Comfort-fit rings are typically ordered 0.5 sizes smaller than the "
                    "measured finger size due to the thicker interior profile. "
                    "Verify ring size with recipient before manufacturing.",
                    "Confirm ring size or consider adding 0.5 to the measured finger size.",
                ))
        except (TypeError, ValueError):
            pass

    # BAND-009: knife-edge manufacturing advisory
    # SOURCE: parameter_constraints_and_validation.md §5.1 — casting limitations.
    # Knife-edge profiles cannot be cast in a ring tube; require hand-forging or wax cutting.
    if is_knife_edge:
        advisory.append(ConstraintViolation(
            "BAND-009", "ADVISORY",
            ["ring.profile"],
            "Knife-edge profiles cannot be cast in a ring tube. This design requires "
            "hand-forging or wax cutting. Confirm manufacturing process supports this profile.",
            "Verify with manufacturer that hand-forging or wax cutting is available.",
        ))

    # ===================================================================
    # PRONG rules
    # ===================================================================

    # PRONG-009: bezel + prong_count > 0 (checked first; skip 001/002 if fires)
    prong_009_fired = False
    if center_setting == "bezel" and prong_count is not None:
        try:
            if int(prong_count) > 0:
                prong_009_fired = True
                hard.append(ConstraintViolation(
                    "PRONG-009", "HARD",
                    ["setting.prong_count", "setting.center_setting"],
                    "Bezel settings fully enclose the stone's girdle. "
                    "Prongs are structurally incompatible with a bezel. "
                    "Set prong_count to 0 or use a prong/basket setting instead.",
                    "Set setting.prong_count = 0 or change center_setting to 'prong'.",
                ))
        except (TypeError, ValueError):
            pass

    # PRONG-001 / PRONG-002: atypical prong counts for round stones (SOFT).
    # GIA documents four- and six-prong norms but does not establish other
    # counts as universal manufacturing failures.
    if not prong_009_fired and center_shape == "round" and prong_count is not None:
        try:
            pc = int(prong_count)
            if pc == 2:
                soft.append(ConstraintViolation(
                    "PRONG-002", "SOFT",
                    ["setting.prong_count", "stones[center].shape"],
                    "Two-prong round settings are outside MyJewl's catalog baseline. "
                    "GIA describes four to six prongs as the usual range; verify this "
                    "custom retention design with the setter.",
                    "Set setting.prong_count to 4 or 6.",
                ))
            elif pc > 0 and pc not in (3, 4, 6):
                soft.append(ConstraintViolation(
                    "PRONG-001", "SOFT",
                    ["setting.prong_count", "stones[center].shape"],
                    f"A {pc}-prong round setting is outside MyJewl's catalog baseline. "
                    "GIA documents four- and six-prong norms, not a universal reject list.",
                    "Prefer 4 or 6 prongs, or obtain setter approval for the custom layout.",
                ))
        except (TypeError, ValueError):
            pass

    # PRONG-003: pear/marquise count enforcement (SOFT) + V-tip advisory
    #
    # Count enforcement: computable from v0.1 spec fields.
    # SOURCE: Industry consensus — pear standard 5–6 prongs (4 marginal);
    #         marquise standard 6 prongs (4 with V-tips is also accepted).
    #
    # V-tip advisory: HARD-classified rule degraded to ADVISORY in v0.1.
    # v0.2 GAP: cannot verify round-tip vs V-tip from center_setting enum alone.

    if center_shape == "pear" and center_setting == "prong" and prong_count is not None:
        try:
            pc = int(prong_count)
            if pc < 4:
                soft.append(ConstraintViolation(
                    "PRONG-003", "SOFT",
                    ["setting.prong_count", "stones[center].shape"],
                    f"Pear stones are typically set with 5 or 6 prongs for daily-wear security. "
                    f"{pc}-prong setting is insufficient for a pointed stone.",
                    "Use 5 or 6 prongs for pear center stones.",
                ))
            elif pc == 4:
                soft.append(ConstraintViolation(
                    "PRONG-003", "SOFT",
                    ["setting.prong_count", "stones[center].shape"],
                    "Pear stones are most secure with 5 or 6 prongs for daily-wear. "
                    "4-prong pear settings are marginal — the pointed tip has reduced coverage.",
                    "Consider upgrading to 5 or 6 prongs for daily-wear security.",
                ))
        except (TypeError, ValueError):
            pass

    if center_shape == "marquise" and center_setting == "prong" and prong_count is not None:
        try:
            pc = int(prong_count)
            if pc not in (4, 6):
                soft.append(ConstraintViolation(
                    "PRONG-003", "SOFT",
                    ["setting.prong_count", "stones[center].shape"],
                    f"Marquise stones are typically set with 6 prongs (V-tips at both points). "
                    f"4-prong with V-tips is also accepted. {pc}-prong is not standard.",
                    "Use 4 or 6 prongs for marquise center stones.",
                ))
        except (TypeError, ValueError):
            pass

    if center_shape in ("pear", "marquise") and center_setting == "prong":
        advisory.append(ConstraintViolation(
            "PRONG-003", "ADVISORY",
            ["setting.center_setting", "stones[center].shape"],
            f"{center_shape.capitalize()} stones require V-tip prongs at the pointed end(s). "
            "Standard round-tip prongs at pointed ends will crack the stone. "
            "(HARD enforcement pending schema v0.2 prong_style enum.)",
            "Verify V-tip prongs are used; specify via setting.prong_style in schema v0.2.",
        ))

    # PRONG-004: princess + prong → SOFT (L-claw preferred)
    # v0.2 GAP: setting.prong_style enum missing; cannot verify L-claw vs round prong.
    if center_shape == "princess" and center_setting == "prong":
        soft.append(ConstraintViolation(
            "PRONG-004", "SOFT",
            ["setting.center_setting", "stones[center].shape"],
            "Round prongs on princess-cut corners concentrate stress at the stone's edges. "
            "Corner L-claws (claw tips) are strongly recommended to protect princess-cut corners.",
            "Use L-claw prongs for princess-cut stones.",
        ))

    # PRONG-005: emerald + prong → ADVISORY (flat-grip preferred)
    if center_shape == "emerald" and center_setting == "prong":
        advisory.append(ConstraintViolation(
            "PRONG-005", "ADVISORY",
            ["setting.center_setting", "stones[center].shape"],
            "V-prongs are designed for pointed-end stones. Emerald cuts have flat edges "
            "that are better secured with flat-grip (step-cut) prongs.",
            "Consider switching to 4-prong flat-grip setting.",
        ))

    # PRONG-006 removed (M2 Revision): fired for all oval/cushion/radiant prong rings as noise.
    # Cannot detect V-tip intent without prong_style field (v0.2 gap). See schema_gaps.

    # PRONG-007: stones < 1.5 mm with prongs need process-specific review (SOFT).
    # Stuller's production guide explicitly supports pinpoint prongs for melee,
    # so the previous universal "cannot use prongs" HARD claim was false.
    if 0 < center_max_dim < 1.5 and prong_count is not None:
        try:
            if int(prong_count) > 0:
                soft.append(ConstraintViolation(
                    "PRONG-007", "SOFT",
                    ["setting.prong_count", "stones[center].dimensions_mm"],
                    f"A {center_max_dim} mm stone requires a calibrated melee-setting "
                    "process; conventional center-stone prongs may be unsuitable.",
                    "Use a validated pinpoint, bead, flush, or bezel process, or obtain setter approval.",
                ))
        except (TypeError, ValueError):
            pass

    # PRONG-008: stones ≥ 6.5 mm with 4 prongs → ADVISORY (6 preferred)
    if center_max_dim >= 6.5 and prong_count is not None:
        try:
            if int(prong_count) == 4:
                advisory.append(ConstraintViolation(
                    "PRONG-008", "ADVISORY",
                    ["setting.prong_count", "stones[center].dimensions_mm"],
                    f"Stones 1 carat and larger (≥6.5 mm; this stone: {center_max_dim} mm) are "
                    "typically more secure in a 6-prong setting. 4-prong is functional but "
                    "provides less redundancy against prong wear.",
                    "Consider upgrading to 6-prong setting for ≥1ct stones.",
                ))
        except (TypeError, ValueError):
            pass

    # ===================================================================
    # METAL rules
    # ===================================================================

    _cathedral = "cathedral" in shoulder_style or "cathedral" in profile

    # METAL-001/002: cathedral + ≥1ct center stone → SOFT prong verification advisory.
    # The original 14/16 AWG thresholds are not jewelry setting standards. Stuller's
    # published 0.45 mm minimum is specifically for pinpoint prongs; it must not be
    # generalized to a center-stone prong. No universal diameter is enforced here.
    if _cathedral and center_carat is not None and center_carat >= 1.0:
        if _is_platinum(metal_alloy):
            rule_id_m, desc_m = "METAL-002", "Platinum"
        elif metal_type in ("gold", "silver"):
            rule_id_m, desc_m = "METAL-001", metal_type.capitalize()
        else:
            rule_id_m = None

        if rule_id_m is not None:
            _prong = _calibrated_threshold(
                "min_prong_diameter_base_mm", 0.0, metal_alloy
            )
            if not _prong.calibrated:
                message_m = (
                    f"{desc_m} prongs for 1 carat+ stones in cathedral settings require a "
                    "shop-calibrated section and bearing. No universal diameter is represented "
                    "by spec v0.1; verify the CAD/manufacturing profile."
                )
                remedy_m = "Confirm prong base diameter in manufacturing profile."
            else:
                message_m = (
                    f"{desc_m} prongs for 1 carat+ stones in cathedral settings require a "
                    f"minimum base diameter of {_prong.limit} mm per shop profile "
                    f"{_prong.profile_label} (source: {_prong.source}). Spec v0.1 does not "
                    "carry prong diameter, so this cannot be checked against the geometry "
                    "here; it remains a calibrated review item, not a verdict."
                )
                remedy_m = (
                    f"Confirm prong base diameter ≥ {_prong.limit} mm against shop profile "
                    f"{_prong.profile_label}."
                )
            soft.append(ConstraintViolation(
                rule_id_m, "SOFT",
                ["metal.type", "metal.alloy", "stones[center].carat", "setting.center_setting"],
                message_m,
                remedy_m,
            ))

    # METAL-003: platinum alloy → density correction note (SOFT)
    # NOTE: 20.7 g/cm³ applies specifically to Pt950/Ru alloy.
    # Pt950/Ir is ~21.4 g/cm³ and would be underestimated by this figure.
    # Alloy-specific density distinction is a v0.2 refinement. SOURCE: metallurgy density tables.
    if _is_platinum(metal_alloy):
        soft.append(ConstraintViolation(
            "METAL-003", "SOFT",
            ["metal.alloy"],
            "Using Pt950/Ru5 density (20.7 g/cm³) for platinum mass estimate. "
            "Pure Pt (21.45) overestimates mass by ~3.6%.",
            "No action required; density correction applied automatically.",
        ))

    # ===================================================================
    # SETTING rules
    # ===================================================================

    # SETTING-001: tension settings require stone- and shop-specific review (SOFT).
    # GIA warns that girdle condition and setter expertise control suitability; it
    # does not publish a shape-only prohibition that supports a HARD reject.
    if center_setting == "tension" and center_shape in ("pear", "marquise", "heart"):
        soft.append(ConstraintViolation(
            "SETTING-001", "SOFT",
            ["setting.center_setting", "stones[center].shape"],
            f"{center_shape.capitalize()} stones have vulnerable points and a tension "
            "setting needs stone-specific girdle inspection and expert calibration.",
            f"Prefer a protective prong or bezel setting for {center_shape}, or obtain setter approval.",
        ))

    if center_setting == "tension" and center_shape in ("oval", "cushion"):
        soft.append(ConstraintViolation(
            "SETTING-001", "SOFT",
            ["setting.center_setting", "stones[center].shape"],
            f"Tension settings work best with symmetric shapes. "
            f"{center_shape.capitalize()} stones can be tension-set but require expert "
            "custom manufacturing and calibration. Verify with your setter.",
            f"Use prong or bezel setting for {center_shape}, or confirm expert tension-setting "
            "capability with your manufacturer.",
        ))

    # SETTING-002: channel setting not valid for center stone
    if center_setting == "channel":
        hard.append(ConstraintViolation(
            "SETTING-002", "HARD",
            ["setting.center_setting", "stones[center].shape"],
            "Channel settings are designed for rows of matched side stones, not center stones.",
            "Select prong, bezel, or basket for the center stone.",
        ))

    # SETTING-003: cathedral profile → band_width ≥ 2.0 (SOFT heuristic)
    # v0.2 GAP: ring.profile is free-text — substring match for "cathedral"
    if _cathedral and band_width is not None and band_width < 2.0:
        soft.append(ConstraintViolation(
            "SETTING-003", "SOFT",
            ["setting.center_setting", "ring.profile"],
            f"This cathedral design uses a {band_width} mm band, below MyJewl's "
            "uncalibrated 2.0 mm design baseline.",
            "Increase ring.band_width_mm to 2.0 or obtain calibrated shop approval.",
        ))

    # SETTING-004: low-profile + depth > 6.0 mm (SOFT heuristic)
    # v0.2 GAP: ring.profile is free-text — substring match for "low"
    _low_profile = "low" in profile
    if _low_profile and center_depth is not None and center_depth > 6.0:
        soft.append(ConstraintViolation(
            "SETTING-004", "SOFT",
            ["setting.center_setting", "stones[center].dimensions_mm.depth"],
            f"This stone's depth ({center_depth} mm) exceeds MyJewl's uncalibrated "
            "6.0 mm low-profile review threshold; head geometry determines actual fit.",
            "Review mountability geometry or use a higher-profile mounting.",
        ))

    # ===================================================================
    # CLEARANCE rules — ALL moved to schema_gaps (M2 Revision)
    #
    # CLEARANCE-001/002: Gallery clearance cannot be derived from stone depth.
    #   The "Rule of Thirds" formula (depth × 0.33) does not appear in any
    #   authoritative source. Stuller's published CAD/CAM minimum is 0.5 mm
    #   culet-to-finger-rail, measured directly on the geometry.
    #   SOURCE: m2_critique_report.md Finding 2 (CRITICAL), verified Round 3.
    #
    # CLEARANCE-003: Seat bearing depth ratio (≤25% of prong diameter, Stuller)
    #   is a real constraint but requires prong_seat_depth_mm, not in schema v0.1.
    #   The original 0.30 mm absolute constant has no industry basis.
    #   SOURCE: m2_critique_report.md Finding 3 (CRITICAL), Loop 2.
    #
    # All three deferred to geometry gate. Documented in schema_gaps above.
    # ===================================================================

    # ===================================================================
    # STONE rules
    # ===================================================================

    # STONE-001: halo stone diameter ≤ 40% of center stone (SOFT)
    if halo_stones and center_width_val > 0:
        halo_widths = []
        for hs in halo_stones:
            try:
                hw = float((hs.get("dimensions_mm") or {}).get("width") or 0)
                if hw > 0:
                    halo_widths.append(hw)
            except (TypeError, ValueError):
                pass
        if halo_widths:
            avg_halo = sum(halo_widths) / len(halo_widths)
            if avg_halo > center_width_val * 0.40:
                soft.append(ConstraintViolation(
                    "STONE-001", "SOFT",
                    ["stones[halo].dimensions_mm", "stones[center].dimensions_mm"],
                    f"Halo stones ({avg_halo:.2f} mm avg) are relatively large compared to "
                    f"center stone ({center_width_val} mm). Industry practice uses halo stones "
                    "at roughly 20–40% of center width.",
                    f"Consider halo stones ≤ {center_width_val * 0.40:.2f} mm.",
                ))

    # STONE-002/003/004: geometry-gate deferred — halo_radius_mm not in schema v0.1
    # (Declared in schema_gaps above; no HARD check here.)

    # STONE-005: halo + bezel requires geometry-specific review (ADVISORY).
    # Bezel-halo rings are real designs; the old blanket "physically impossible"
    # HARD claim was contradicted by GIA design guidance and current catalogs.
    if halo_style == "single" and center_setting == "bezel":
        advisory.append(ConstraintViolation(
            "STONE-005", "ADVISORY",
            ["setting.halo_style", "setting.center_setting"],
            "A bezel center can be combined with a halo, but bezel-wall and halo "
            "clearance must be resolved by the Rhino geometry and reviewed by a setter.",
            "Run the geometry clearance gates and obtain setter review.",
        ))

    # STONE-006: halo + cathedral profile → SOFT (halo will be obscured)
    if halo_style == "single" and _cathedral:
        soft.append(ConstraintViolation(
            "STONE-006", "SOFT",
            ["setting.halo_style", "ring.profile"],
            "Cathedral settings raise the center stone above the halo plane, visually "
            "obscuring the halo.",
            "Consider low-profile or standard-profile mounting for halo designs.",
        ))

    # STONE-007: trilogy requires exactly 2 side stones
    if archetype == "trilogy":
        n_sides = sum(_stone_schedule_quantity(stone) for stone in side_stones)
        if n_sides != 2:
            hard.append(ConstraintViolation(
                "STONE-007", "HARD",
                ["stones[side]", "job.archetype"],
                f"Trilogy rings require exactly 2 side stones. Found {n_sides}.",
                "Add exactly 2 side-role stones for trilogy archetype.",
            ))

    # STONE-008: trilogy side stones must be symmetric (same shape + dimensions)
    if archetype == "trilogy" and len(side_stones) == 2:
        s0, s1 = side_stones[0], side_stones[1]
        shape0 = str(s0.get("shape") or "").lower()
        shape1 = str(s1.get("shape") or "").lower()
        dims0  = s0.get("dimensions_mm") or {}
        dims1  = s1.get("dimensions_mm") or {}
        mismatch = shape0 != shape1
        if not mismatch:
            for key in ("length", "width", "depth"):
                v0, v1 = dims0.get(key), dims1.get(key)
                if v0 is None or v1 is None:
                    continue
                try:
                    if abs(float(v0) - float(v1)) > 0.01:
                        mismatch = True
                        break
                except (TypeError, ValueError):
                    pass
        if mismatch:
            hard.append(ConstraintViolation(
                "STONE-008", "HARD",
                ["stones[side][0]", "stones[side][1]"],
                f"Trilogy side stones must be symmetrical. "
                f"Stone 1 ({shape0}) and Stone 2 ({shape1}) differ in shape or size. "
                "Use matching stones. For asymmetric designs, use 'accent' role instead.",
                "Make both side stones identical in shape and dimensions.",
            ))

    # STONE-009: moved to schema_gaps (M2 Revision).
    # Original check compared total stone span to inner ring diameter — physically wrong.
    # Stones sit above the shank, not inside the finger bore. No published standard
    # specifies a minimum center-to-side stone gap. SOURCE: m2_critique_report.md Finding 4.

    # STONE-010: trilogy side stone ≤ 75% of center stone diameter (ADVISORY)
    if archetype == "trilogy" and center_width_val > 0 and side_stones:
        for sv in side_stones:
            try:
                sw = float((sv.get("dimensions_mm") or {}).get("width") or 0)
                if sw > center_width_val * 0.75:
                    advisory.append(ConstraintViolation(
                        "STONE-010", "ADVISORY",
                        ["stones[center].dimensions_mm", "stones[side].dimensions_mm"],
                        f"Side stones ({sw} mm each) approach or exceed 75% of center stone "
                        f"diameter ({center_width_val} mm). Side stones may visually overpower "
                        "the center.",
                        f"Consider side stones ≤ {center_width_val * 0.75:.2f} mm.",
                    ))
                    break
            except (TypeError, ValueError):
                pass

    # STONE-011: heart shape not supported in v0.1 (HARD; see also v0.2 GAP in gaps)
    for s in stones:
        if isinstance(s, dict) and str(s.get("shape") or "").lower() == "heart":
            hard.append(ConstraintViolation(
                "STONE-011", "HARD",
                ["stones[].shape"],
                "Heart-shaped stones are not yet supported (schema v0.2 required). "
                "Supported shapes: round, oval, cushion, pear, emerald, radiant, princess.",
                "Use a supported shape or wait for schema v0.2.",
            ))
            break

    # ===================================================================
    # WEIGHT rules
    # ===================================================================

    # WEIGHT-001: GIA Weight Ratio sanity check (SOFT)
    # SOURCE: GIA educational materials; shape factors from GIA-aligned trade formulas.
    # NOTE: 1.10 WR threshold is an internal heuristic; no GIA publication specifies
    #       this exact value. Flagged as SOFT only.
    # Uses shape-specific _derive_carat() for dimensional estimate (M2 Revision).
    if (center.get("carat") is not None
            and center_length > 0 and center_width_val > 0 and center_depth is not None):
        try:
            _dim_only = {k: v for k, v in center.items() if k != "carat"}
            calc = _derive_carat(_dim_only)
            if calc and calc > 0:
                wr = float(center["carat"]) / calc
                if wr > 1.10:
                    soft.append(ConstraintViolation(
                        "WEIGHT-001", "SOFT",
                        ["stones[center].carat", "stones[center].dimensions_mm"],
                        f"This stone's carat weight implies a Weight Ratio of {wr:.2f}×, "
                        "suggesting a deep or heavy-bottom stone. "
                        "Verify stone dimensions and carat.",
                        "Check stone carat and dimension values against certificate.",
                    ))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # WEIGHT-002: emerald / radiant → silent +1–18% bulge mass correction (logged only)
    # WEIGHT-003: pear / marquise / oval → silent +1–10% wing correction (logged only)
    # NOTE: These are silent mass correction factors applied in the geometry pipeline.
    # They are NOT SOFT rules and do NOT appear in soft_warnings. The session progress doc
    # previously counted them as SOFT; this was incorrect. SOURCE: knowledge base §3.1.
    if center_shape in ("emerald", "radiant"):
        logger.debug("WEIGHT-002: +1%%–18%% bulge mass correction applied for %s", center_shape)

    if center_shape in ("pear", "marquise", "oval"):
        logger.debug("WEIGHT-003: +1%%–10%% wing mass correction applied for %s", center_shape)

    # ===================================================================
    # RING-001: HK/JP size standard → SOFT
    # ===================================================================
    if str(ring.get("size_standard") or "").upper() in ("HK", "JP"):
        soft.append(ConstraintViolation(
            "RING-001", "SOFT",
            ["ring.size_standard"],
            f"Warning: {ring['size_standard']} ring size conversion formulas are pending "
            "verification against official standards (HKCS / JIS L 4003). "
            "The generated inner diameter may differ from your physical size.",
            "Verify ring size against an official gauge before production.",
        ))

    # ===================================================================
    # SIDE-001: trilogy + bezel center → suggest matching side settings (ADVISORY)
    # ===================================================================
    if archetype == "trilogy" and center_setting == "bezel" and side_stones:
        advisory.append(ConstraintViolation(
            "SIDE-001", "ADVISORY",
            ["setting.center_setting", "stones[side].role"],
            "Center stone uses bezel setting. Mixed setting styles for center and side "
            "stones may look inconsistent.",
            "Consider matching bezel or flush settings for side stones.",
        ))

    # ===================================================================
    # Result
    # ===================================================================
    return ConstraintResult(
        accepted=len(hard) == 0,
        hard_violations=hard,
        soft_warnings=soft,
        advisory_notes=advisory,
        schema_gaps=gaps,
    )


# Alias for compatibility
check_constraints = validate
