#!/usr/bin/env python3
"""Every manufacturing assumption MyJewl makes, in one visible place.

Goal 0018. The operator's instruction: *"Anything that encodes a manufacturing
assumption should be visible and centralized, not buried as a constant."*

Two kinds of number live here, and they must never be confused:

``ConvergenceTolerance``
    How close our geometry must sit to a *reference CAD file* for a dimension to
    count as reproduced. These are **comparison thresholds for a measurement
    harness**, agreed with the operator on 2026-08-12. They say nothing about
    whether a ring can be cast.

``ManufacturingFloor``
    A physical process limit -- minimum castable wire, minimum wall. These are
    **uncalibrated placeholders** until a real shop supplies them. Every one
    carries ``calibrated=False`` and a ``source`` describing where the number
    came from, including when the honest answer is "an engineer's estimate".

**Nothing in this module certifies anything.** A floor being satisfied is not a
manufacturing pass; geometry gates stay ``pending`` / ``not_run`` /
``needs_calibration`` until a calibrated shop profile *and* human jeweler review
exist. See ``jewelry_gaas_foundation.shop_profile`` for the calibrated-intake
path that supersedes these placeholders once a real profile is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ASSUMPTIONS_VERSION = "myjewl-manufacturing-assumptions-v1"

Discipline = Literal["converge", "diverge"]


@dataclass(frozen=True)
class ConvergenceTolerance:
    """How closely a dimension must match a reference CAD file."""

    key: str
    label: str
    tolerance: float
    unit: str
    discipline: Discipline
    rationale: str
    #: True when the operator agreed the number itself, False when it is held
    #: pending a shop/modelist answer and the harness should measure-and-report only.
    agreed: bool = True


@dataclass(frozen=True)
class ManufacturingFloor:
    """A physical process limit. Uncalibrated until a shop supplies the number."""

    key: str
    label: str
    value_mm: float | None
    source: str
    calibrated: bool = False
    shop_profile_field: str | None = None


# ---------------------------------------------------------------------------
# Convergence line -- agreed with the operator 2026-08-12.
#
# Principle: converge on what determines fit, function, and cost; allow
# divergence on what determines appearance. The split also keeps MyJewl's
# ``source_ownership`` claim clean -- functional dimensions are facts about how
# rings work, styling is where authorship lives.
# ---------------------------------------------------------------------------

CONVERGENCE_TOLERANCES: tuple[ConvergenceTolerance, ...] = (
    ConvergenceTolerance(
        "inner_diameter_mm", "Inner diameter / ring size", 0.05, "mm", "converge",
        "Roughly a quarter US size. The ring fits or it does not.",
    ),
    ConvergenceTolerance(
        "seat_diameter_mm", "Stone seat diameter", 0.03, "mm", "converge",
        "Stone drops through or will not seat.",
    ),
    ConvergenceTolerance(
        "seat_height_mm", "Seat height above band", 0.10, "mm", "converge",
        "Sets finger clearance and total profile height.",
    ),
    ConvergenceTolerance(
        "band_width_mm", "Band width", 0.10, "mm", "converge",
        "Typed customer input; directly visible on the finished ring.",
    ),
    ConvergenceTolerance(
        "band_thickness_mm", "Band thickness", 0.10, "mm", "converge",
        "Structural and cost-bearing.",
    ),
    ConvergenceTolerance(
        "prong_base_diameter_mm", "Prong base diameter", 0.05, "mm", "converge",
        "Structural floor; the stone-retention failure mode.",
        agreed=False,  # held pending the shop/modelist number
    ),
    ConvergenceTolerance(
        "prong_coverage_mm", "Prong coverage over girdle", 0.10, "mm", "converge",
        "The setter's acceptance criterion.",
        agreed=False,  # held: no agreed measurement method yet
    ),
    ConvergenceTolerance(
        "metal_volume_mm3", "Metal volume", 0.03, "fraction", "converge",
        "Directly the quoted price. Fractional, not absolute.",
    ),
)

#: Styling. Constrained qualitatively, never compared numerically to a reference.
DIVERGENCE_ALLOWED: tuple[tuple[str, str], ...] = (
    ("shoulder_taper", "Monotonic, no inflection; endpoint radii within functional bounds."),
    ("basket_petal_form", "Must reach both rails; petal count is a catalog choice."),
    ("gallery_decoration", "Unconstrained above the castable wire floor."),
    ("cathedral_rise", "Must clear the girdle; the path itself is aesthetic."),
    ("prong_tip_form", "Must cover the girdle; bead vs claw vs tab is styling."),
    ("surface_finish", "Out of scope for STL entirely."),
)


# ---------------------------------------------------------------------------
# Manufacturing floors -- ALL UNCALIBRATED. This is the list for the casting
# shop. A value of ``None`` means MyJewl has no number at all, which is a more
# honest state than a fabricated one.
# ---------------------------------------------------------------------------

MANUFACTURING_FLOORS: tuple[ManufacturingFloor, ...] = (
    ManufacturingFloor(
        "min_castable_wire_diameter_mm", "Minimum castable wire diameter (any feature)",
        None,
        "NOT REPRESENTED. Goal 0018 audit finding F3: the master scales every wire "
        "dimension linearly with stone radius, with no floor anywhere in the "
        "codebase to catch it. Quantified across the whole envelope by Goal 0021 "
        "(scripts/sweep_envelope.py): thinnest exposed section 0.157 mm at a 3 mm "
        "stone rising to 0.575 mm at the 11 mm maximum, so the *entire* range sits "
        "below the 1.0-1.3 mm centre-prong band the research brief flags -- not "
        "only the small end. The prong base runs 0.277-1.015 mm over the same "
        "range and is 0.600 mm at the ~1 ct reference.",
        shop_profile_field="min_open_gallery_wire_diameter_mm",
    ),
    ManufacturingFloor(
        "min_prong_base_diameter_mm", "Minimum centre-stone prong base diameter",
        None,
        "Uncalibrated. constraints.py defaults this to 0.0 and emits an advisory "
        "rather than enforcing a fabricated number -- deliberately. Stuller's "
        "0.45 mm is a PINPOINT prong minimum and must not be generalised. "
        "Engineer's estimate for a centre stone is 0.9-1.2 mm; unverified.",
        shop_profile_field="min_prong_wire_diameter_mm",
    ),
    ManufacturingFloor(
        "min_shank_width_mm", "Minimum shank width", 1.8,
        "MyJewl uncalibrated design baseline (constraints.py BAND-001).",
    ),
    ManufacturingFloor(
        "min_shank_thickness_mm", "Minimum shank thickness", 1.5,
        "MyJewl uncalibrated design baseline (constraints.py BAND-002).",
    ),
    ManufacturingFloor(
        "min_knife_edge_band_width_mm", "Minimum knife-edge band width", 2.0,
        "MyJewl uncalibrated design baseline (constraints.py BAND-003).",
    ),
    ManufacturingFloor(
        "min_comfort_fit_thickness_mm", "Minimum comfort-fit thickness", 1.5,
        "MyJewl uncalibrated design baseline (constraints.py BAND-004).",
    ),
    ManufacturingFloor(
        "min_feature_separation_mm", "Minimum separation before features fuse in casting",
        None,
        "NOT REPRESENTED. Governs petal and prong spacing; nothing checks it.",
    ),
    ManufacturingFloor(
        "alloy_shrinkage_factor", "Alloy shrinkage allowance", None,
        "NOT REPRESENTED. MyJewl ships FINISHED-NOMINAL geometry with no "
        "compensation applied (operator decision, 2026-08-12). Recorded here so "
        "the omission is an explicit assertion rather than an oversight.",
        shop_profile_field="alloy_shrinkage_allowance",
    ),
    ManufacturingFloor(
        "finishing_stock_allowance_mm", "Finishing stock allowance", None,
        "NOT REPRESENTED. Consequence of the finished-nominal decision.",
    ),
)

# ---------------------------------------------------------------------------
# Output contract, declared explicitly because STL cannot carry it.
# ---------------------------------------------------------------------------

#: Operator decision, 2026-08-12. MyJewl emits FINISHED NOMINAL dimensions with
#: no shrinkage or finishing compensation applied, because those factors are
#: shop- and alloy-specific. The receiving shop applies its own compensation.
DIMENSION_BASIS = "finished_nominal_no_compensation_applied"

#: Whether the centre stone belongs inside the castable solid. It does not --
#: a caster receiving a fused stone gets a diamond-shaped lump of metal.
STONE_IN_CASTABLE_SOLID = False


def uncalibrated_floors() -> tuple[ManufacturingFloor, ...]:
    """Return every floor still needing a real number. The shop conversation list."""
    return tuple(floor for floor in MANUFACTURING_FLOORS if not floor.calibrated)


def unrepresented_floors() -> tuple[ManufacturingFloor, ...]:
    """Return floors MyJewl has no number for at all -- the most urgent subset."""
    return tuple(floor for floor in MANUFACTURING_FLOORS if floor.value_mm is None)


def agreed_tolerances() -> tuple[ConvergenceTolerance, ...]:
    """Return only tolerances the operator has agreed the numeric value of."""
    return tuple(item for item in CONVERGENCE_TOLERANCES if item.agreed)


def held_tolerances() -> tuple[ConvergenceTolerance, ...]:
    """Return tolerances held pending a shop or modelist answer.

    The harness still measures and reports these dimensions exactly; it just does
    not compare them against a threshold nobody has validated.
    """
    return tuple(item for item in CONVERGENCE_TOLERANCES if not item.agreed)
