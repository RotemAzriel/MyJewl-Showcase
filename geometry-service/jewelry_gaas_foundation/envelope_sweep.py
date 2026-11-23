#!/usr/bin/env python3
"""Sweep the master's declared envelope and report the wire sections it implies.

Goal 0021. ``master_parameters.derive_parameters`` is pure Python with explicit
bounds, so the whole envelope can be sampled densely for free -- no Rhino, no
Azure, no paid compute. This module does that and answers one question for every
configuration MyJewl claims it can build: **how thin is the thinnest metal?**

It exists because the casting-shop and modelist conversations are expensive and
effectively one-shot. Walking in able to say *"our entire offered range produces
0.12-0.58 mm sections; what is your minimum?"* is a materially better conversation
than *"is 0.6 mm acceptable?"*.

**This module reports exact values and never verdicts.** Same discipline as
``geometry_metrics``: a floor being cleared is not a manufacturing pass, and a
floor being missed is not a manufacturing failure. Every floor offered here is a
*candidate* -- a number to put in front of a shop, explicitly uncalibrated. See
``manufacturing_assumptions`` for why, and ``shop_profile`` for the only path by
which a number becomes calibrated. Geometry gates stay ``pending`` / ``not_run`` /
``needs_calibration``.

**What the numbers describe.** Each section is measured on the *loose named part*
as the definition builds it, which is what ``geometry_metrics`` measures and what
``validate_against_measurement`` compares to. Where parts interpenetrate, the local
thickness of the fused ``castable_solid`` can only be **greater**, so these figures
are lower bounds on the union everywhere except at free ends -- prong tips, and the
outer face of every wire.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from jewelry_gaas_foundation import master_parameters as mp
from jewelry_gaas_foundation.manufacturing_evidence import (
    evidence_candidate_floors,
)
from jewelry_gaas_foundation.manufacturing_assumptions import (
    ASSUMPTIONS_VERSION,
    DIMENSION_BASIS,
    MANUFACTURING_FLOORS,
)

SWEEP_VERSION = "myjewl-envelope-sweep-v1"

#: Restated on every report so a consumer cannot read a verdict into it.
AUTHORITY = "measurement_only_not_manufacturing_certification"
MANUFACTURING_GATES = "pending_not_run_needs_calibration"


# ---------------------------------------------------------------------------
# Constants that live in the Grasshopper definition, not in the derivation.
#
# ``derive_parameters`` publishes ``gallery_wire_radius`` and ``prong_tip_radius``;
# the definition then scales them when it builds two specific parts. Those scale
# factors are part of the geometry and must appear here for the section figures to
# be right, but they are a **second copy** of a definition literal, so
# ``tests/test_goal0021_envelope_sweep.py`` asserts each one still appears verbatim
# in ``build_goal0018_castable_master_v6_ghx.SCRIPT``. If the definition changes
# and this does not, the test fails rather than the report going quietly wrong.
#
# These are read off the **V6 script**, which is the current master. Earlier links
# in the chain carry different literals -- the seat-rail factor was 0.92 in the
# Goal 0009 base and is 0.84 from V4 on -- so reading them from the wrong file is
# a live way to be wrong by 9%.
# ---------------------------------------------------------------------------

#: V6 SCRIPT: circle_pipe(..., P["gallery_wire_radius"] * 0.84)
SEAT_RAIL_WIRE_FACTOR = 0.84

#: V6 SCRIPT: rg.Sphere(prongs[-1], P["prong_tip_radius"] * 1.08)
PRONG_TIP_BEAD_FACTOR = 1.08

#: V6 SCRIPT: Transform.Scale(rg.Plane(...), 1.0, 1.0, 0.72) -- the *intent*, which
#: is to flatten the bead to 72% of its height. See PRONG_TIP_BEAD_EXPORT_SCALE:
#: this does not survive into the exported geometry.
PRONG_TIP_BEAD_FLATTEN = 0.72

#: **Measured, not intended.** The flattening transform above is applied to an
#: analytic sphere Brep, and what Rhino exports is not an oblate spheroid: it is a
#: uniform sphere scaled by ``sqrt(0.72)`` on all three axes. Measured on all six
#: solved bundles on disk -- three V5 (2026-08-04) and three V6 (2026-08-12) -- as
#: an exactly equal x/y/z extent, at a ratio to ``prong_tip_radius`` identical to
#: five decimal places across a 1.6x size range. This module models the geometry
#: that was actually built, because that is what a shop would receive; the
#: divergence from the source's intent is reported in ``SOURCE_DIVERGENCES`` rather
#: than absorbed silently.
PRONG_TIP_BEAD_EXPORT_SCALE = math.sqrt(PRONG_TIP_BEAD_FLATTEN)


@dataclass(frozen=True)
class SourceDivergence:
    """A place where the exported geometry does not match what the source says.

    An observation about our own toolchain, not a manufacturing verdict. It is
    recorded here because a table handed to a casting shop has to describe the
    parts they would actually receive.
    """

    key: str
    summary: str
    source_intent: str
    as_built: str
    evidence: str
    consequence: str


SOURCE_DIVERGENCES: tuple[SourceDivergence, ...] = (
    SourceDivergence(
        "prong_tip_bead_not_flattened",
        "The prong tip bead is exported as a uniform sphere, not a flattened one.",
        "rg.Sphere(tip, prong_tip_radius * 1.08) flattened to 0.72 in Z, giving an "
        "oblate bead 0.3672 mm wide and 0.2644 mm tall at the reference stone.",
        "A uniform sphere of radius prong_tip_radius * 1.08 * sqrt(0.72), i.e. "
        "0.3116 mm in every direction at the reference stone.",
        "Equal x/y/z bounding-box extents on prong_tip_01..04 in all six solved "
        "bundles (V5 2026-08-04 and V6 2026-08-12, small/control/large). The "
        "analytic model here reproduces the measured value to within 0.11%.",
        "The bead is ~15% narrower across the crown than the source intends, so it "
        "covers less of the stone; and ~18% thicker through its axis, so the "
        "thinnest-part figure is less alarming than the source implies. Neither is "
        "a manufacturing verdict -- the coverage question is for the modelist.",
    ),
)


# ---------------------------------------------------------------------------
# Features. One entry per metal cross-section the definition actually builds.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WireFeature:
    """One metal cross-section, and how to read its minimum from derived parameters."""

    key: str
    label: str
    #: STL stems in a solved bundle that carry this section, for measured comparison.
    part_names: tuple[str, ...]
    #: What the number is, in words a jeweller would use.
    describes: str
    section_mm: Callable[[dict[str, Any]], float]
    #: True when this section sits on a free surface of the fused solid, so the
    #: per-part figure is the real local thickness. False when the thin point is
    #: where this part overlaps another, which makes the figure a **lower bound**:
    #: after the boolean union the local metal there can only be thicker. The
    #: distinction matters commercially -- quoting a fused lower bound to a shop
    #: as if it were exposed metal would understate our own geometry.
    free_end: bool = False


def _shank_section(p: dict[str, Any]) -> float:
    # An elliptical pipe: radial thickness by axial width. The minimum normal
    # chord of an ellipse is its minor diameter, i.e. the smaller of the two.
    return min(p["shank_radial_thickness"], p["shank_width"])


def _shoulder_section(p: dict[str, Any]) -> float:
    return 2.0 * min(p["shoulder_radii"])


def _petal_section(p: dict[str, Any]) -> float:
    return 2.0 * min(p["basket_petal_radii"])


def _lower_rail_section(p: dict[str, Any]) -> float:
    return 2.0 * p["gallery_wire_radius"]


def _seat_rail_section(p: dict[str, Any]) -> float:
    return 2.0 * p["gallery_wire_radius"] * SEAT_RAIL_WIRE_FACTOR


def _prong_section(p: dict[str, Any]) -> float:
    return 2.0 * min(p["prong_radii"])


def _prong_root_section(p: dict[str, Any]) -> float:
    return 2.0 * p["prong_root_radius"]


def _prong_tip_bead_section(p: dict[str, Any]) -> float:
    # As built: a uniform sphere, so every through-section is its diameter. The
    # source intends an oblate spheroid; see SOURCE_DIVERGENCES.
    return (
        2.0
        * p["prong_tip_radius"]
        * PRONG_TIP_BEAD_FACTOR
        * PRONG_TIP_BEAD_EXPORT_SCALE
    )


def prong_tip_bead_source_intent_section(p: dict[str, Any]) -> float:
    """Return the bead section the definition's source *says* it builds.

    Kept beside the as-built figure so the divergence stays visible and testable
    rather than becoming a constant nobody remembers is empirical.
    """
    return 2.0 * p["prong_tip_radius"] * PRONG_TIP_BEAD_FACTOR * PRONG_TIP_BEAD_FLATTEN


WIRE_FEATURES: tuple[WireFeature, ...] = (
    WireFeature(
        "shank", "Band (shank)", ("shank",),
        "Smaller of band thickness and band width: the ring's own cross-section.",
        _shank_section, free_end=True,
    ),
    WireFeature(
        "shoulder_wire", "Shoulder wire (thinnest point)",
        ("shoulder_front_L", "shoulder_front_R", "shoulder_rear_L", "shoulder_rear_R"),
        "The split shoulders taper from the band up to the basket; this is the "
        "thin end, where they meet the basket petal.",
        _shoulder_section,
    ),
    WireFeature(
        "basket_petal_wire", "Basket petal wire (thinnest point)",
        ("basket_petal_01", "basket_petal_02", "basket_petal_03", "basket_petal_04"),
        "The four petals that form the basket, at their upper end.",
        _petal_section,
    ),
    WireFeature(
        "gallery_lower_rail_wire", "Gallery lower rail wire", ("basket_lower_rail",),
        "The lower gallery ring the petals and prongs stand on.",
        _lower_rail_section,
    ),
    WireFeature(
        "stone_seat_rail_wire", "Stone seat rail wire", ("stone_seat_rail",),
        "The upper rail the stone seats against.",
        _seat_rail_section,
    ),
    WireFeature(
        "prong_wire_min", "Prong wire (thinnest point, below the tip)",
        ("prong_01", "prong_02", "prong_03", "prong_04"),
        "The prong tapers from its root on the gallery to its tip over the "
        "girdle. This is the thin end.",
        _prong_section, free_end=True,
    ),
    WireFeature(
        "prong_root", "Prong base diameter", (),
        "The prong where it meets the gallery: the structural figure the trade "
        "quotes, and the one the stone-retention failure mode turns on. It is the "
        "thick end of the prong taper, so it is a maximum, not a minimum.",
        _prong_root_section,
    ),
    WireFeature(
        "prong_tip_bead", "Prong tip bead (through-thickness)",
        ("prong_tip_01", "prong_tip_02", "prong_tip_03", "prong_tip_04"),
        "The bead capping each prong over the crown, and the thinnest section of "
        "any single part. It sits on the prong's own tip and fuses into it, so on "
        "the finished solid the metal there is thicker than this figure.",
        _prong_tip_bead_section,
    ),
)

FEATURES_BY_KEY: dict[str, WireFeature] = {f.key: f for f in WIRE_FEATURES}


# ---------------------------------------------------------------------------
# Candidate floors. NONE of these is calibrated. This is the column the shop is
# being asked to pick, not a threshold MyJewl is asserting.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateFloor:
    """A minimum-section number worth putting in front of a casting shop.

    ``calibrated`` is ``False`` on every instance and there is no code path that
    sets it True. A number becomes usable through ``shop_profile``, never here.
    """

    key: str
    value_mm: float
    label: str
    source: str
    #: "myjewl_uncalibrated_baseline" | "third_party_unverified" | "question_for_the_shop"
    provenance: str
    calibrated: bool = False


#: ``source`` is read by a casting shop, so it names trades and documents rather
#: than files. Repository pointers stay in these comments: 0.45 mm is the
#: CLAUDE.md pinpoint-prong note, 0.90 mm is the engineer's estimate recorded
#: against ``min_prong_base_diameter_mm`` in ``manufacturing_assumptions``, and
#: the 1.0-1.3 mm band is ``docs/geometry_research_brief_2026-08-12.md``.
CANDIDATE_FLOORS: tuple[CandidateFloor, ...] = (
    CandidateFloor(
        "pinpoint_prong_0p45", 0.45, "0.45 mm",
        "Stuller's published minimum, which is specific to a PINPOINT prong and "
        "is explicitly not a universal centre-prong rule. Shown only because it "
        "is the smallest figure we can find published anywhere in the trade.",
        "third_party_unverified",
    ),
    CandidateFloor(
        "engineer_estimate_0p90", 0.90, "0.90 mm",
        "The low end of our own engineer's estimate for a centre-stone prong "
        "base. Nobody has verified it; it is a guess we would like replaced.",
        "question_for_the_shop",
    ),
    CandidateFloor(
        "trade_standard_1p00", 1.00, "1.00 mm",
        "Low end of a 1.0-1.3 mm centre-prong band we found quoted for a 1 ct "
        "stone. Unverified, and from a single source.",
        "third_party_unverified",
    ),
    CandidateFloor(
        "trade_standard_1p30", 1.30, "1.30 mm",
        "High end of that same 1.0-1.3 mm band. Unverified, single source.",
        "third_party_unverified",
    ),
)


def active_candidate_floors() -> tuple[CandidateFloor, ...]:
    """The built-in candidates plus any *declared* named testimony.

    Goal 0028. ``manufacturing_evidence`` is the intake for an answer given by a
    person rather than measured by a shop. A declared finding widens this table
    and does nothing else: it arrives as one more uncalibrated column, carrying
    who said it and when, for a casting shop to react to.

    ``CandidateFloor`` defaults ``calibrated`` to False and nothing here passes
    that argument, so testimony cannot become calibrated by this route even if a
    record claims it is. Calibration remains ``shop_profile``'s alone.
    """
    extra = tuple(
        CandidateFloor(key, value_mm, label, source, provenance)
        for key, value_mm, label, source, provenance in evidence_candidate_floors()
    )
    return CANDIDATE_FLOORS + extra


def baseline_floors() -> tuple[tuple[str, float, str], ...]:
    """Return MyJewl's own uncalibrated band floors, as ``(key, mm, source)``.

    These come from ``manufacturing_assumptions.MANUFACTURING_FLOORS`` -- the ones
    that carry a number at all. They are design baselines, not shop numbers, and
    they are still ``calibrated=False``.
    """
    return tuple(
        (floor.key, floor.value_mm, floor.source)
        for floor in MANUFACTURING_FLOORS
        if floor.value_mm is not None
    )


#: Which typed input each numeric baseline floor constrains, and when it applies.
#: The master builds one band form -- a round shank, neither knife-edge nor
#: comfort-fit -- so two of the four floors are recorded as not applying rather
#: than quietly dropped.
BASELINE_FLOOR_AXES: dict[str, tuple[str, str]] = {
    "min_shank_width_mm": ("band_width_mm", "always (constraints.py BAND-001)"),
    "min_shank_thickness_mm": (
        "band_thickness_mm", "always (constraints.py BAND-002)",
    ),
    "min_knife_edge_band_width_mm": (
        "band_width_mm",
        "knife-edge profiles only (BAND-003); this master builds a round shank",
    ),
    "min_comfort_fit_thickness_mm": (
        "band_thickness_mm",
        "comfort-fit profiles only (BAND-004); this master builds a round shank",
    ),
}


# ---------------------------------------------------------------------------
# The grid.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """One typed input the master accepts, and the bounds it declares."""

    key: str
    label: str
    minimum: float
    maximum: float
    unit: str


def envelope_axes() -> tuple[Axis, ...]:
    """Return the axes straight from ``master_parameters``, never re-declared here."""
    return (
        Axis("us_size", "Ring size (US)", mp.US_SIZE_MIN, mp.US_SIZE_MAX, "US"),
        Axis(
            "stone_diameter_mm", "Centre stone diameter",
            mp.STONE_DIAMETER_MIN_MM, mp.STONE_DIAMETER_MAX_MM, "mm",
        ),
        Axis(
            "stone_depth_ratio", "Stone depth / diameter",
            mp.STONE_DEPTH_RATIO_MIN, mp.STONE_DEPTH_RATIO_MAX, "ratio",
        ),
        Axis(
            "band_width_mm", "Band width",
            mp.BAND_WIDTH_MIN_MM, mp.BAND_WIDTH_MAX_MM, "mm",
        ),
        Axis(
            "band_thickness_mm", "Band thickness",
            mp.BAND_THICKNESS_MIN_MM, mp.BAND_THICKNESS_MAX_MM, "mm",
        ),
    )


#: How far outside each bound the probe points sit, as a fraction of the span.
#: The probes exist so the sweep *measures* where the master refuses instead of
#: assuming the declared bounds are the real ones.
OUT_OF_BOUND_PROBE_FRACTION = 0.05


def axis_values(axis: Axis, steps: int, probe: bool = True) -> tuple[float, ...]:
    """Return ``steps`` values spanning the axis, plus two out-of-bound probes."""
    if steps < 2:
        raise ValueError("steps must be at least 2 so both bounds are sampled")
    span = axis.maximum - axis.minimum
    inside = [
        axis.minimum + span * index / (steps - 1) for index in range(steps)
    ]
    if not probe:
        return tuple(inside)
    margin = span * OUT_OF_BOUND_PROBE_FRACTION
    return tuple([axis.minimum - margin] + inside + [axis.maximum + margin])


#: How many stone diameters the boundary-attainability probe walks. The point is
#: to include diameters that are *not* exactly representable in binary, which the
#: main grid may miss entirely -- at 9 steps every sampled diameter is a whole
#: number and the effect vanishes, which would be a resolution artifact reported
#: as an absence.
BOUNDARY_PROBE_SAMPLES = 1001


def boundary_attainability(samples: int = BOUNDARY_PROBE_SAMPLES) -> list[dict[str, Any]]:
    """Ask whether a customer can actually specify a value *at* a declared bound.

    ``derive_parameters`` takes a stone depth in millimetres and reconstructs the
    depth ratio as ``depth / diameter``. That division does not round-trip in
    IEEE-754, so a depth chosen to sit exactly on the ratio bound can come back a
    fraction outside it and be refused by the bare ``>`` comparison in
    ``_in_range``. Whether it does depends on the diameter's binary
    representation, which is why this is a dedicated probe rather than something
    the grid is trusted to stumble into.

    Fail-closed, so the consequence is an ``unavailable`` on a ring the published
    envelope claims -- never a ring built outside the envelope. Reported, not
    fixed: Goal 0021 measures the envelope and does not change the gate.
    """
    diameters = [
        mp.STONE_DIAMETER_MIN_MM
        + (mp.STONE_DIAMETER_MAX_MM - mp.STONE_DIAMETER_MIN_MM) * index / (samples - 1)
        for index in range(samples)
    ]
    rows: list[dict[str, Any]] = []
    for label, ratio in (
        ("stone_depth_ratio minimum", mp.STONE_DEPTH_RATIO_MIN),
        ("stone_depth_ratio maximum", mp.STONE_DEPTH_RATIO_MAX),
    ):
        refused: list[float] = []
        for diameter in diameters:
            request = mp.configuration_request(
                mp.REF_US_SIZE, diameter, ratio * diameter
            )
            try:
                mp.derive_parameters(request)
            except mp.MasterParameterError:
                refused.append(diameter)
        rows.append(
            {
                "bound": label,
                "value": ratio,
                "diameters_probed": len(diameters),
                "diameters_refused": len(refused),
                "fraction_refused": len(refused) / len(diameters),
                "example_refused_diameter_mm": refused[0] if refused else None,
                "explanation": (
                    "A depth specified exactly at this ratio bound is refused for "
                    "these stone diameters, even though every typed input is "
                    "inside the published envelope. The ratio is reconstructed as "
                    "depth / diameter and the division does not round-trip in "
                    "IEEE-754. Fail-closed: the effect is an 'unavailable' on a "
                    "ring we claim to build, never a ring built out of range. NOT "
                    "FIXED HERE -- Goal 0021 measures the envelope and does not "
                    "change derive_parameters."
                ),
            }
        )
    return rows


def _refusal_reason(message: str) -> str:
    """Collapse a refusal message to its reason, dropping the offending value.

    Grouping needs a stable key, and the message ends in the value that tripped
    the bound. The bounds themselves are kept -- they are the informative half.
    """
    marker = " is outside the approved envelope "
    if marker in message:
        label, rest = message.split(marker, 1)
        return label + marker + rest.rsplit(":", 1)[0]
    return message.split(", got")[0].split(", found")[0]


@dataclass
class GridPoint:
    """One sampled configuration and the sections it derives to."""

    us_size: float
    stone_diameter_mm: float
    stone_depth_ratio: float
    band_width_mm: float
    band_thickness_mm: float
    in_declared_bounds: bool
    accepted: bool
    refusal: str | None = None
    sections_mm: dict[str, float] = field(default_factory=dict)

    @property
    def stone_depth_mm(self) -> float:
        return self.stone_depth_ratio * self.stone_diameter_mm

    def request(self) -> dict[str, Any]:
        return mp.configuration_request(
            self.us_size,
            self.stone_diameter_mm,
            self.stone_depth_mm,
            band_width_mm=self.band_width_mm,
            band_thickness_mm=self.band_thickness_mm,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "us_size": self.us_size,
            "stone_diameter_mm": self.stone_diameter_mm,
            "stone_depth_ratio": self.stone_depth_ratio,
            "stone_depth_mm": self.stone_depth_mm,
            "band_width_mm": self.band_width_mm,
            "band_thickness_mm": self.band_thickness_mm,
        }


def evaluate_point(point: GridPoint) -> GridPoint:
    """Fill in ``accepted`` / ``refusal`` / ``sections_mm`` for one grid point."""
    try:
        parameters = mp.derive_parameters(point.request())
    except mp.MasterParameterError as exc:
        point.accepted = False
        point.refusal = _refusal_reason(str(exc))
        return point
    point.accepted = True
    point.sections_mm = {
        feature.key: feature.section_mm(parameters) for feature in WIRE_FEATURES
    }
    return point


def grid_points(steps: int = 7, probe: bool = True) -> Iterable[GridPoint]:
    """Yield every unevaluated point of the cartesian grid over the five axes."""
    axes = envelope_axes()
    values = [axis_values(axis, steps, probe=probe) for axis in axes]
    for combination in itertools.product(*values):
        in_bounds = all(
            axis.minimum <= value <= axis.maximum
            for axis, value in zip(axes, combination)
        )
        yield GridPoint(
            us_size=combination[0],
            stone_diameter_mm=combination[1],
            stone_depth_ratio=combination[2],
            band_width_mm=combination[3],
            band_thickness_mm=combination[4],
            in_declared_bounds=in_bounds,
            accepted=False,
        )


# ---------------------------------------------------------------------------
# The sweep.
# ---------------------------------------------------------------------------

#: Relative spread below which a section counts as exactly proportional to a
#: driver. Set at float-comparison scale, not an engineering tolerance: the claim
#: is that the derivation multiplies, so anything looser would be a fudge.
_PROPORTIONALITY_EPSILON = 1e-9

_DRIVERS: tuple[tuple[str, Callable[[GridPoint], float]], ...] = (
    ("stone_diameter_mm", lambda p: p.stone_diameter_mm),
    ("band_width_mm", lambda p: p.band_width_mm),
    ("band_thickness_mm", lambda p: p.band_thickness_mm),
)


@dataclass
class FeatureSummary:
    """What one cross-section does across the whole accepted envelope."""

    key: str
    label: str
    describes: str
    part_names: tuple[str, ...]
    free_end: bool
    min_section_mm: float
    max_section_mm: float
    at_minimum: dict[str, Any]
    at_maximum: dict[str, Any]
    #: The single typed input this section is exactly proportional to, if any.
    proportional_to: str | None
    #: ``section_mm = coefficient * driver`` when ``proportional_to`` is set.
    coefficient: float | None
    #: Every typed input the section actually responds to. A section can depend on
    #: two inputs without being proportional to either -- the shoulder wire is the
    #: thinner of a band-width-driven root and a stone-driven neck -- so this must
    #: be measured rather than inferred from ``proportional_to`` being None.
    varies_with: tuple[str, ...] = ()

    def required_driver_value(self, floor_mm: float) -> float | None:
        """Return the driver value at which this section would reach ``floor_mm``."""
        if self.coefficient is None or self.coefficient <= 0.0:
            return None
        return floor_mm / self.coefficient

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "describes": self.describes,
            "part_names": list(self.part_names),
            "free_end": self.free_end,
            "min_section_mm": self.min_section_mm,
            "max_section_mm": self.max_section_mm,
            "at_minimum": self.at_minimum,
            "at_maximum": self.at_maximum,
            "proportional_to": self.proportional_to,
            "coefficient": self.coefficient,
            "varies_with": list(self.varies_with),
        }


@dataclass
class FloorSensitivity:
    """How much of the envelope sits at or above one candidate floor."""

    floor_key: str
    floor_mm: float
    floor_label: str
    floor_source: str
    floor_provenance: str
    calibrated: bool
    #: Share of accepted configurations where *every* modelled section, including
    #: the ones that are fused lower bounds, sits at or above the floor.
    fraction_of_configurations_all_features_at_or_above: float
    #: Share where every section that is actually **exposed** on the fused solid
    #: sits at or above the floor. The commercially meaningful column: a fused
    #: lower bound understates the metal a shop would really be pouring.
    fraction_of_configurations_free_end_features_at_or_above: float
    per_feature: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "floor_key": self.floor_key,
            "floor_mm": self.floor_mm,
            "floor_label": self.floor_label,
            "floor_source": self.floor_source,
            "floor_provenance": self.floor_provenance,
            "calibrated": self.calibrated,
            "fraction_of_configurations_all_features_at_or_above": (
                self.fraction_of_configurations_all_features_at_or_above
            ),
            "fraction_of_configurations_free_end_features_at_or_above": (
                self.fraction_of_configurations_free_end_features_at_or_above
            ),
            "per_feature": self.per_feature,
        }


@dataclass
class SweepReport:
    """The whole sweep. Values only; no verdicts anywhere in it."""

    steps: int
    points_evaluated: int
    accepted: int
    refused: int
    accepted_in_declared_bounds: int
    points_in_declared_bounds: int
    refusals: list[dict[str, Any]]
    #: Grid points the master refuses even though every typed input is inside the
    #: declared bounds. Not an error to swallow: a customer hitting one of these
    #: sees `unavailable` for a ring the envelope says we build.
    boundary_refusals: list[dict[str, Any]]
    #: The same question asked deterministically, independent of grid resolution.
    boundary_attainability: list[dict[str, Any]]
    features: list[FeatureSummary]
    thinnest: dict[str, Any]
    thinnest_free_end: dict[str, Any]
    floor_sensitivity: list[FloorSensitivity]
    baseline_band_floors: list[dict[str, Any]]
    baseline_crosscheck: list[dict[str, Any]]
    validation: dict[str, Any] | None = None

    def feature(self, key: str) -> FeatureSummary:
        for item in self.features:
            if item.key == key:
                return item
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "myjewl.envelope_sweep.v1",
            "sweep_version": SWEEP_VERSION,
            "derivation_version": mp.MASTER_PARAMETER_DERIVATION_VERSION,
            "assumptions_version": ASSUMPTIONS_VERSION,
            "dimension_basis": DIMENSION_BASIS,
            "authority": AUTHORITY,
            "manufacturing_gates": MANUFACTURING_GATES,
            "envelope": mp.envelope(),
            "grid": {
                "steps_per_axis": self.steps,
                "out_of_bound_probe_fraction": OUT_OF_BOUND_PROBE_FRACTION,
                "points_evaluated": self.points_evaluated,
                "accepted": self.accepted,
                "refused": self.refused,
                "points_in_declared_bounds": self.points_in_declared_bounds,
                "accepted_in_declared_bounds": self.accepted_in_declared_bounds,
            },
            "refusals": self.refusals,
            "boundary_refusals": self.boundary_refusals,
            "boundary_attainability": self.boundary_attainability,
            "features": [item.as_dict() for item in self.features],
            "thinnest_section": self.thinnest,
            "thinnest_free_end_section": self.thinnest_free_end,
            "floor_sensitivity": [item.as_dict() for item in self.floor_sensitivity],
            "baseline_band_floors": self.baseline_band_floors,
            "baseline_crosscheck": self.baseline_crosscheck,
            "source_divergences": [
                {
                    "key": item.key,
                    "summary": item.summary,
                    "source_intent": item.source_intent,
                    "as_built": item.as_built,
                    "evidence": item.evidence,
                    "consequence": item.consequence,
                }
                for item in SOURCE_DIVERGENCES
            ],
            "validation": self.validation,
        }


def _varies_with(points: Sequence[GridPoint], key: str) -> tuple[str, ...]:
    """Return every axis whose value changes this section, measured on the grid.

    Holds the grid's own samples rather than probing synthetic points: an axis
    matters if two accepted configurations that differ only on that axis produce
    different sections.
    """
    axes = [axis.key for axis in envelope_axes()]
    seen: dict[tuple, float] = {}
    matters: set[str] = set()
    for point in points:
        values = point.as_dict()
        for index, axis in enumerate(axes):
            signature = (
                index,
                tuple(values[other] for other in axes if other != axis),
            )
            section = point.sections_mm[key]
            if signature in seen:
                if seen[signature] != section:
                    matters.add(axis)
            else:
                seen[signature] = section
    return tuple(axis for axis in axes if axis in matters)


def _proportionality(
    points: Sequence[GridPoint], key: str
) -> tuple[str | None, float | None]:
    """Return the driver a section is exactly proportional to, and the coefficient."""
    for driver_name, read in _DRIVERS:
        ratios = [point.sections_mm[key] / read(point) for point in points]
        low, high = min(ratios), max(ratios)
        if high <= 0.0:
            continue
        if (high - low) / high <= _PROPORTIONALITY_EPSILON:
            return driver_name, (low + high) / 2.0
    return None, None


def sweep(steps: int = 7, probe: bool = True) -> SweepReport:
    """Evaluate the grid and summarise it. Pure Python, offline, free."""
    accepted: list[GridPoint] = []
    refusal_counts: dict[str, int] = {}
    refusal_examples: dict[str, dict[str, Any]] = {}
    boundary_counts: dict[str, int] = {}
    boundary_examples: dict[str, dict[str, Any]] = {}
    evaluated = 0
    in_bounds_total = 0
    in_bounds_accepted = 0

    for point in grid_points(steps, probe=probe):
        evaluate_point(point)
        evaluated += 1
        if point.in_declared_bounds:
            in_bounds_total += 1
        if point.accepted:
            accepted.append(point)
            if point.in_declared_bounds:
                in_bounds_accepted += 1
        else:
            reason = point.refusal or "unknown"
            refusal_counts[reason] = refusal_counts.get(reason, 0) + 1
            refusal_examples.setdefault(reason, point.as_dict())
            if point.in_declared_bounds:
                boundary_counts[reason] = boundary_counts.get(reason, 0) + 1
                boundary_examples.setdefault(reason, point.as_dict())

    if not accepted:  # pragma: no cover - the declared bounds always accept
        raise RuntimeError("the sweep accepted no configuration at all")

    features: list[FeatureSummary] = []
    for feature in WIRE_FEATURES:
        low = min(accepted, key=lambda p: p.sections_mm[feature.key])
        high = max(accepted, key=lambda p: p.sections_mm[feature.key])
        driver, coefficient = _proportionality(accepted, feature.key)
        features.append(
            FeatureSummary(
                key=feature.key,
                label=feature.label,
                describes=feature.describes,
                part_names=feature.part_names,
                free_end=feature.free_end,
                min_section_mm=low.sections_mm[feature.key],
                max_section_mm=high.sections_mm[feature.key],
                at_minimum=low.as_dict(),
                at_maximum=high.as_dict(),
                proportional_to=driver,
                coefficient=coefficient,
                varies_with=_varies_with(accepted, feature.key),
            )
        )

    def _thinnest(candidates: Sequence[FeatureSummary]) -> dict[str, Any]:
        winner = min(candidates, key=lambda f: f.min_section_mm)
        return {
            "feature": winner.key,
            "label": winner.label,
            "section_mm": winner.min_section_mm,
            "free_end": winner.free_end,
            "at": winner.at_minimum,
        }

    thinnest = _thinnest(features)
    free_end_features = [f for f in features if f.free_end]
    thinnest_free_end = _thinnest(free_end_features) if free_end_features else {}

    sensitivity = [
        _floor_sensitivity(candidate, accepted, features)
        for candidate in active_candidate_floors()
    ]

    return SweepReport(
        steps=steps,
        points_evaluated=evaluated,
        accepted=len(accepted),
        refused=evaluated - len(accepted),
        points_in_declared_bounds=in_bounds_total,
        accepted_in_declared_bounds=in_bounds_accepted,
        boundary_attainability=boundary_attainability(),
        boundary_refusals=[
            {
                "reason": reason,
                "count": count,
                "example": boundary_examples[reason],
                "explanation": (
                    "Every typed input is inside the declared bounds, but the "
                    "master still refuses. The depth ratio is reconstructed as "
                    "depth / diameter inside derive_parameters, and that division "
                    "does not round-trip exactly in IEEE-754: a ratio specified at "
                    "the boundary can come back one ULP outside it and fail the "
                    "bare > comparison in _in_range. Fail-closed, so the effect is "
                    "an 'unavailable' on a ring the envelope says we build, not a "
                    "ring built out of range. NOT FIXED HERE -- Goal 0021 measures "
                    "the envelope and does not change derive_parameters."
                ),
            }
            for reason, count in sorted(
                boundary_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        refusals=[
            {
                "reason": reason,
                "count": count,
                "example": refusal_examples[reason],
            }
            for reason, count in sorted(
                refusal_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        features=features,
        thinnest=thinnest,
        thinnest_free_end=thinnest_free_end,
        floor_sensitivity=sensitivity,
        baseline_band_floors=[
            {"key": key, "value_mm": value, "source": source, "calibrated": False}
            for key, value, source in baseline_floors()
        ],
        baseline_crosscheck=_baseline_crosscheck(accepted),
    )


def _baseline_crosscheck(accepted: Sequence[GridPoint]) -> list[dict[str, Any]]:
    """Compare the master's declared envelope against MyJewl's own band floors.

    ``derive_parameters`` is the single envelope gate and ``constraints.validate``
    is the rule layer, and they were written independently. Where the envelope
    admits a band the rule layer would warn about, the two disagree -- and the
    envelope is the one geometry is actually built from. Reported as a measured
    fraction, not as a defect claim: whether the envelope or the rule is wrong is
    a commercial decision, and it needs the shop's number first.
    """
    axes = {axis.key: axis for axis in envelope_axes()}
    read = {
        "band_width_mm": lambda p: p.band_width_mm,
        "band_thickness_mm": lambda p: p.band_thickness_mm,
    }
    total = len(accepted)
    rows: list[dict[str, Any]] = []
    for key, value, source in baseline_floors():
        mapping = BASELINE_FLOOR_AXES.get(key)
        if mapping is None:  # pragma: no cover - every numeric floor is mapped
            continue
        axis_key, applies = mapping
        axis = axes[axis_key]
        below = sum(1 for point in accepted if read[axis_key](point) < value)
        rows.append(
            {
                "floor_key": key,
                "floor_mm": value,
                "calibrated": False,
                "source": source,
                "applies": applies,
                "typed_input": axis_key,
                "envelope_minimum_mm": axis.minimum,
                "envelope_admits_below_floor": bool(axis.minimum < value),
                "fraction_of_envelope_below_floor": below / total,
            }
        )
    return rows


def _floor_sensitivity(
    candidate: CandidateFloor,
    accepted: Sequence[GridPoint],
    features: Sequence[FeatureSummary],
) -> FloorSensitivity:
    total = len(accepted)
    per_feature: list[dict[str, Any]] = []
    for summary in features:
        at_or_above = sum(
            1
            for point in accepted
            if point.sections_mm[summary.key] >= candidate.value_mm
        )
        required = summary.required_driver_value(candidate.value_mm)
        in_envelope: bool | None = None
        if required is not None and summary.proportional_to is not None:
            axis = next(
                (a for a in envelope_axes() if a.key == summary.proportional_to), None
            )
            if axis is not None:
                in_envelope = bool(required <= axis.maximum)
        per_feature.append(
            {
                "feature": summary.key,
                "label": summary.label,
                "free_end": summary.free_end,
                "fraction_at_or_above": at_or_above / total,
                "min_section_mm": summary.min_section_mm,
                "max_section_mm": summary.max_section_mm,
                "required_driver": summary.proportional_to,
                "required_driver_value": required,
                "required_driver_value_in_envelope": in_envelope,
            }
        )
    all_clear = sum(
        1
        for point in accepted
        if min(point.sections_mm.values()) >= candidate.value_mm
    )
    free_end_keys = [f.key for f in features if f.free_end]
    free_end_clear = sum(
        1
        for point in accepted
        if min(point.sections_mm[key] for key in free_end_keys) >= candidate.value_mm
    ) if free_end_keys else 0
    return FloorSensitivity(
        floor_key=candidate.key,
        floor_mm=candidate.value_mm,
        floor_label=candidate.label,
        floor_source=candidate.source,
        floor_provenance=candidate.provenance,
        calibrated=candidate.calibrated,
        fraction_of_configurations_all_features_at_or_above=all_clear / total,
        fraction_of_configurations_free_end_features_at_or_above=free_end_clear / total,
        per_feature=per_feature,
    )


# ---------------------------------------------------------------------------
# Step 3: does the analytic sweep agree with measured geometry?
# ---------------------------------------------------------------------------

#: Agreement band for analytic section vs measured wall thickness, as a signed
#: fraction of the analytic value. Asymmetric on purpose, and both halves are
#: quantified biases of the measurement, not slack:
#:
#: * ``-0.06`` allows the ray-cast method to read thin. Two effects push that way
#:   and both are documented in ``geometry_metrics._wall_thickness``: mesh
#:   faceting inscribes the true circle (validated at -0.25% against an analytic
#:   torus), and on a tapered feature the inward normal crosses a shorter chord
#:   than the true diameter. The taper term dominates and grows with the cone
#:   half-angle, which is why the band is this wide on this side.
#: * ``+0.02`` allows almost nothing the other way. There is no mechanism by
#:   which a loose part measures *thicker* than the definition built it, so a
#:   positive excursion means the analytic model is wrong about which radius the
#:   part carries -- exactly the failure this check exists to catch.
MEASUREMENT_AGREEMENT_LOW = -0.06
MEASUREMENT_AGREEMENT_HIGH = 0.02


def analytic_sections(request: dict[str, Any]) -> dict[str, float]:
    """Return every feature's section for one typed request."""
    parameters = mp.derive_parameters(request)
    return {f.key: f.section_mm(parameters) for f in WIRE_FEATURES}


def _feature_for_part(part_name: str) -> WireFeature | None:
    for feature in WIRE_FEATURES:
        if part_name in feature.part_names:
            return feature
    return None


def validate_against_measurement(
    request: dict[str, Any],
    measured_parts: Sequence[dict[str, Any]],
    *,
    bundle: str = "",
) -> dict[str, Any]:
    """Compare analytic sections against one bundle's measured wall thickness.

    ``measured_parts`` is the ``parts`` list of a
    ``myjewl.geometry_measurement.v1`` report. Every part the sweep claims to
    model is compared; parts it does not model (the stone, the fused solid) are
    listed as skipped rather than silently dropped.

    Returns exact residuals. ``agrees`` is a statement about *this measurement
    harness*, not about the ring: it says the analytic table can be trusted to
    describe the geometry Rhino actually built. It is not a manufacturing verdict.
    """
    sections = analytic_sections(request)
    comparisons: list[dict[str, Any]] = []
    skipped: list[str] = []

    for part in measured_parts:
        name = str(part.get("name", ""))
        feature = _feature_for_part(name)
        if feature is None:
            skipped.append(name)
            continue
        measured = part.get("min_wall_thickness_mm")
        analytic = sections[feature.key]
        if measured is None:
            comparisons.append(
                {
                    "part": name,
                    "feature": feature.key,
                    "analytic_mm": analytic,
                    "measured_mm": None,
                    "residual_fraction": None,
                    "within_band": None,
                    "note": str(part.get("wall_thickness_method") or "no measurement"),
                }
            )
            continue
        residual = (float(measured) - analytic) / analytic
        comparisons.append(
            {
                "part": name,
                "feature": feature.key,
                "analytic_mm": analytic,
                "measured_mm": float(measured),
                "measured_p05_mm": part.get("wall_thickness_p05_mm"),
                "residual_fraction": residual,
                "within_band": bool(
                    MEASUREMENT_AGREEMENT_LOW <= residual <= MEASUREMENT_AGREEMENT_HIGH
                ),
            }
        )

    compared = [c for c in comparisons if c["residual_fraction"] is not None]
    outside = [c for c in compared if not c["within_band"]]
    residuals = [c["residual_fraction"] for c in compared]
    return {
        "bundle": bundle,
        "request": request,
        "agreement_band": {
            "low": MEASUREMENT_AGREEMENT_LOW,
            "high": MEASUREMENT_AGREEMENT_HIGH,
            "basis": "signed fraction of the analytic section; see "
                     "envelope_sweep.MEASUREMENT_AGREEMENT_LOW for why it is asymmetric",
        },
        "parts_compared": len(compared),
        "parts_outside_band": len(outside),
        "parts_skipped": sorted(skipped),
        "worst_residual_fraction": (
            max(residuals, key=abs) if residuals else None
        ),
        "mean_residual_fraction": (
            sum(residuals) / len(residuals) if residuals else None
        ),
        "agrees": bool(compared) and not outside,
        "comparisons": comparisons,
        "authority": AUTHORITY,
    }


# ---------------------------------------------------------------------------
# The shop-facing document.
# ---------------------------------------------------------------------------


def _mm(value: float | None, places: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:.{places}f}"


def _pct(fraction: float) -> str:
    if fraction == 0.0:
        return "none"
    if fraction == 1.0:
        return "all"
    return f"{fraction * 100:.1f}%"


def _short(label: str) -> str:
    """Drop a label's parenthetical, for use mid-sentence."""
    return label.split(" (")[0].strip()


def to_markdown(report: SweepReport, *, generated: str = "") -> str:
    """Render the sweep as a document a casting shop can read without this repo."""
    envelope = mp.envelope()
    lines: list[str] = []
    add = lines.append

    add("# MyJewl — wire sections across the whole design envelope")
    add("")
    if generated:
        add(f"**Generated:** {generated}")
        add("")
    add("**Dimensions are finished nominal.** No shrinkage allowance and no")
    add("finishing stock are applied — every figure here is the metal we want on the")
    add("ring when it is done, not the wax. If you need us to compensate, tell us and")
    add("we will.")
    add("")
    add("> **This document asserts no manufacturing verdict.** It states what our")
    add("> geometry measures and asks what your process can hold. Nothing in it is a")
    add("> pass or a fail, no threshold in it is calibrated, and nothing here has")
    add("> been approved for production.")
    add("")

    add("## The question")
    add("")
    add("We build one ring design — a four-prong round solitaire — at any size in")
    add("the range below. Every wire dimension in the head scales in proportion to")
    add("the centre stone, with **no minimum anywhere**. We want to know which of")
    add("these configurations you can actually cast.")
    add("")

    add("## What we offer")
    add("")
    add("| Typed input | Minimum | Maximum |")
    add("|---|---|---|")
    for axis in envelope_axes():
        add(f"| {axis.label} | {axis.minimum:g} {axis.unit} | {axis.maximum:g} {axis.unit} |")
    add(f"| Centre stone shape | {envelope['center_stone_shape']} | — |")
    add("")
    add(f"Sampled on a {report.steps}-point grid in each of the five inputs: "
        f"**{report.accepted:,} configurations** evaluated, plus "
        f"**{report.refused:,}** probe points outside the range, which the model "
        f"refuses to build rather than extrapolate.")
    add("")

    add("## The headline")
    add("")
    exposed = report.thinnest_free_end
    thin = report.thinnest
    add(f"The thinnest **exposed** metal anywhere in our range is "
        f"**{_mm(exposed['section_mm'])} mm** — the {_short(exposed['label']).lower()}, "
        f"at a {exposed['at']['stone_diameter_mm']:g} mm centre stone, our smallest.")
    add("")
    if thin["feature"] != exposed["feature"]:
        add(f"One part is thinner still — the {_short(thin['label']).lower()} at "
            f"**{_mm(thin['section_mm'])} mm** — but it sits on the prong tip and "
            f"fuses into it, so the finished solid is thicker there. We show it "
            f"because we would rather give you a number and explain it than leave "
            f"it out.")
        add("")
    prong = report.feature("prong_root")
    reference_root = (prong.coefficient or 0.0) * mp.REF_STONE_DIAMETER
    reaches_1mm = prong.required_driver_value(1.0)
    add(f"The prong base diameter — the figure the trade usually quotes — runs from "
        f"**{_mm(prong.min_section_mm)} mm** to **{_mm(prong.max_section_mm)} mm** "
        f"across the whole range. At a {mp.REF_STONE_DIAMETER:g} mm stone "
        f"(roughly 1 ct, and our reference ring) it is **{_mm(reference_root)} mm**.")
    add("")
    add(f"We have seen **1.0–1.3 mm** quoted as a centre-stone prong standard at "
        f"1 ct. If that is right, our reference ring is at less than half of it, and "
        f"our prong base does not reach 1.0 mm until the centre stone is "
        f"{reaches_1mm:.1f} mm — around 4.5 ct. **The problem would then not be the "
        f"small end of our range; it would be all of it.** Confirming or correcting "
        f"that standard is the single most useful thing you can tell us.")
    add("")

    add("## Every section, across the whole range")
    add("")
    add("| Feature | Thinnest | Thickest | Scales with | What it is |")
    add("|---|---|---|---|---|")
    plain = {
        "stone_diameter_mm": "stone Ø",
        "band_width_mm": "band width",
        "band_thickness_mm": "band thickness",
        "stone_depth_ratio": "stone depth",
        "us_size": "ring size",
    }
    for summary in sorted(report.features, key=lambda f: f.min_section_mm):
        if summary.proportional_to:
            driver = plain[summary.proportional_to]
        else:
            driver = " & ".join(
                plain.get(axis, axis) for axis in summary.varies_with
            ) or "nothing"
        add(
            f"| {summary.label} | **{_mm(summary.min_section_mm)} mm** | "
            f"{_mm(summary.max_section_mm)} mm | {driver} | {summary.describes} |"
        )
    add("")
    exposed_labels = [_short(f.label).lower() for f in report.features if f.free_end]
    add("Each section is measured on the individual part as we build it. Where parts")
    add("overlap and fuse into one solid, the metal there can only be thicker, so")
    add("most of these are lower bounds. The exceptions — where the figure is the")
    add(f"real exposed thickness — are the {' and the '.join(exposed_labels)}.")
    add("")

    add("## Sections against a stone size")
    add("")
    add("Everything in the head is exactly proportional to the centre stone, so one")
    add("table covers the range. Band width and thickness are typed separately and")
    add("are shown at our reference values.")
    add("")
    stone_axis = next(a for a in envelope_axes() if a.key == "stone_diameter_mm")
    samples = [3.0, 4.0, 5.0, 6.5, 8.0, 9.5, 11.0]
    proportional = [f for f in report.features if f.proportional_to == "stone_diameter_mm"]
    header = "| Centre stone Ø | " + " | ".join(f.label.split(" (")[0] for f in proportional) + " |"
    add(header)
    add("|" + "---|" * (len(proportional) + 1))
    for diameter in samples:
        if not (stone_axis.minimum <= diameter <= stone_axis.maximum):
            continue
        cells = [
            _mm((f.coefficient or 0.0) * diameter) for f in proportional
        ]
        tag = ""
        if diameter == 3.0:
            tag = " *(our minimum)*"
        elif diameter == 6.5:
            tag = " *(reference / ~1 ct)*"
        elif diameter == 11.0:
            tag = " *(our maximum)*"
        add(f"| **{diameter:g} mm**{tag} | " + " | ".join(cells) + " |")
    add("")
    add("All figures in mm.")
    add("")

    disagreements = [
        row for row in report.baseline_crosscheck
        if row["envelope_admits_below_floor"] and row["applies"].startswith("always")
    ]
    if disagreements:
        add("## Our band range is wider than our own design baseline")
        add("")
        add("Worth stating plainly, because you may be asked to cast one: the range")
        add("we accept from a customer goes below the band minimums our own design")
        add("rules use. Those rules are themselves uncalibrated, which is part of")
        add("why we are asking you.")
        add("")
        add("| Dimension | Our design baseline | Lowest we currently accept | Share of the range below the baseline |")
        add("|---|---|---|---|")
        for row in disagreements:
            add(
                f"| {row['typed_input'].replace('_mm', '').replace('_', ' ')} | "
                f"{row['floor_mm']:g} mm | {row['envelope_minimum_mm']:g} mm | "
                f"{_pct(row['fraction_of_envelope_below_floor'])} |"
            )
        add("")

    add("## What each candidate minimum would cost us")
    add("")
    add("**None of these numbers is yours yet — that is the question.** Each row")
    add("asks: if your minimum castable section were this, how much of our range")
    add("survives, and how big would the stone have to be for the prong to reach it?")
    add("")
    add("| If the minimum is | Where the number comes from | Our range where every **exposed** section clears it | …where **every** section clears it, fused ones included | Stone Ø needed for the prong base to clear it |")
    add("|---|---|---|---|---|")
    for item in report.floor_sensitivity:
        prong_row = next(
            r for r in item.per_feature if r["feature"] == "prong_root"
        )
        required = prong_row["required_driver_value"]
        if required is None:
            needed = "—"
        elif prong_row["required_driver_value_in_envelope"]:
            needed = f"{required:.1f} mm"
        else:
            needed = f"{required:.1f} mm — **larger than any stone we accept**"
        add(
            f"| **{item.floor_label}** | {item.floor_provenance.replace('_', ' ')} | "
            f"{_pct(item.fraction_of_configurations_free_end_features_at_or_above)} | "
            f"{_pct(item.fraction_of_configurations_all_features_at_or_above)} | "
            f"{needed} |"
        )
    add("")
    for item in report.floor_sensitivity:
        add(f"- **{item.floor_label}** — {item.floor_source}")
    add("")

    add("## What we are asking you")
    add("")
    add("1. What is your **minimum castable wire diameter** for an open gallery, in")
    add("   the alloys you pour? A single number, and whether it changes by alloy.")
    add("2. What is your **minimum prong base diameter** for a centre stone at, say,")
    add("   1 ct — and does it scale with the stone or is it a fixed floor?")
    add("3. Is there a **minimum separation** below which two features fuse in the")
    add("   burnout or the pour? We have no number for this at all.")
    add("4. Our geometry is **finished nominal** with no shrinkage or finishing stock")
    add("   applied. Is that what you want to receive, or should we compensate?")
    add("")
    add("The full 40-question survey is a separate document; these four are the ones")
    add("that change what we build.")
    add("")

    if report.validation:
        add("## Is this table trustworthy?")
        add("")
        add("The figures above are computed from our parametric model, not measured")
        add("off a part. We check them against three rings we actually solved in")
        add("Rhino and measured by ray-casting the meshes:")
        add("")
        add("| Ring | Parts compared | Worst disagreement |")
        add("|---|---|---|")
        for entry in report.validation.get("bundles", []):
            worst = entry.get("worst_residual_fraction")
            worst_text = "—" if worst is None else f"{worst * 100:+.2f}%"
            ring = entry.get("request", {}).get("canonical_spec", {})
            size = ring.get("ring", {}).get("size")
            stones = ring.get("stones") or [{}]
            diameter = (stones[0].get("dimensions_mm") or {}).get("length")
            label = (
                f"US {size:g}, {diameter:g} mm stone"
                if size is not None and diameter is not None
                else entry["bundle"]
            )
            add(f"| {label} | {entry['parts_compared']} | {worst_text} |")
        add("")
        overall = report.validation.get("agrees")
        if overall:
            add("Every part agrees within the stated band, so the table describes the")
            add("geometry we would actually send you.")
            add("")
            add("The largest disagreement is on the prongs, and it is the same figure")
            add("on all three rings across a 1.6× size range. That is the signature of")
            add("a known bias in how we measure a *tapered* wire, not an error in the")
            add("table: a probe fired at right angles to a cone crosses slightly less")
            add("than its true diameter. The table's figures are the wider, more")
            add("conservative ones.")
        else:
            add("**At least one part falls outside the stated band. The analytic table")
            add("above should not be relied on until that is explained.**")
        add("")

    add("---")
    add("")
    add(f"Reproduce this document offline, with no paid compute: "
        f"`python3 -m scripts.sweep_envelope --steps {report.steps}`")
    return "\n".join(lines) + "\n"
