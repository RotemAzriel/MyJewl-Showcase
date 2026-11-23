#!/usr/bin/env python3
"""Derive Rhino realism-master parameters from a configuration request.

Goal 0014. The Goal 0011 V4 master froze its parameter dict as literals and used
the request only to check ``template_id``; ``build_parts()`` then read that frozen
dict, so the definition emitted byte-identical geometry for every configuration it
accepted while still returning ``execution_status: "completed"`` with a valid
receipt. This module makes the parameter dict a pure function of the typed request.

Geometry authorship stays in Rhino/Grasshopper. This module produces *numbers*
only -- no curves, surfaces, or meshes. The Grasshopper definition inlines the
delimited derivation block below verbatim so the definition and the test suite
share one source of truth; ``tests/test_goal0014_parametric_master.py`` asserts
the two copies are byte-identical.

Calibration: every ratio is computed at import time from the approved Goal 0011 V4
reference literals, so ``derive_parameters`` reproduces the approved shape at the
reference configuration by construction -- with one deliberate correction. V4 froze
``inner_diameter`` at 16.91 mm; the repository's verified formula
(``11.63 + 0.8128 * US``, see ``jewelry_gaas_foundation.constraints``) gives
16.9132 mm at US 6.5. The derivation uses the formula, so the reference control
differs from frozen V4 by 0.0032 mm on inner diameter, and by the same amount on
every head height anchored to the band. That is a correction of a value frozen
from the repository's own formula, not a redesign of the approved shape.

This module parameterizes geometry. It certifies nothing: manufacturing verdicts
remain ``pending`` / ``not_run`` / ``needs_calibration``.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# The block between the BEGIN/END markers below is inlined verbatim into the
# Grasshopper definition by manual/tools/build_goal0014_parametric_master_ghx.py.
# It must stay pure standard-library Python compatible with the Rhino 8 CPython
# 3.9 script component: no dataclasses, no PEP 604 unions, no walrus in
# annotations, and no imports beyond ``math``.
# ---------------------------------------------------------------------------
# --- BEGIN MYJEWL MASTER PARAMETER DERIVATION v1 ---
import math

MASTER_PARAMETER_DERIVATION_VERSION = "myjewl-master-parameter-derivation-v1"

# Verified ring-size formula (jewelry_gaas_foundation.constraints._inner_diameter_mm).
RING_SIZE_INTERCEPT_MM = 11.63
RING_SIZE_SLOPE_MM_PER_US = 0.8128

# Approved Goal 0011 V4 reference configuration.
REF_US_SIZE = 6.5
REF_STONE_DIAMETER = 6.50
REF_STONE_DEPTH = 3.90
REF_BAND_WIDTH = 2.05
REF_BAND_THICKNESS = 1.65
REF_INNER_DIAMETER_FROZEN = 16.91

# Approved Goal 0011 V4 geometry literals, used only to calibrate ratios.
_REF_STONE_RADIUS = REF_STONE_DIAMETER / 2.0
_REF_CROWN_HEIGHT = 0.96
_REF_GIRDLE_THICKNESS = 0.18
_REF_TABLE_RATIO = 0.56
_REF_PAVILION_DEPTH = REF_STONE_DEPTH - _REF_CROWN_HEIGHT - _REF_GIRDLE_THICKNESS
_REF_SHANK_TOP = REF_INNER_DIAMETER_FROZEN / 2.0 + REF_BAND_THICKNESS
_REF_SHANK_CENTER_RADIUS = REF_INNER_DIAMETER_FROZEN / 2.0 + REF_BAND_THICKNESS / 2.0
_REF_BASKET_LOWER_Z = 9.70
_REF_BASKET_UPPER_Z = 10.90
_REF_GIRDLE_Z = 12.10
_REF_BASKET_HEIGHT = _REF_BASKET_UPPER_Z - _REF_BASKET_LOWER_Z
_REF_BASKET_LOWER_RADIUS = 1.32
_REF_BASKET_UPPER_RADIUS = 2.42
_REF_GALLERY_WIRE_RADIUS = 0.20
_REF_PRONG_TIP_HEIGHT = 0.48

_REF_PETAL_RADIAL = (1.32, 1.50, 1.85, 2.24)
_REF_PETAL_TANGENT = (0.0, 0.08, 0.0, 0.0)
_REF_PETAL_Z = (9.70, 9.94, 10.38, 10.90)
_REF_PETAL_WIRE = (0.25, 0.235, 0.21, 0.18)

_REF_PRONG_RADIAL = (1.38, 1.66, 2.24, 3.06, 2.86, 2.68)
_REF_PRONG_LOWER_Z = (9.72, 10.02, 10.82)
_REF_PRONG_WIRE = (0.30, 0.29, 0.27, 0.235, 0.20, 0.17)

_REF_SHOULDER_X = (5.35, 4.82, 4.35, 3.55, 2.45, 1.30815)
_REF_SHOULDER_Y = (0.25, 0.28, 0.42, 0.66, 0.98, 1.30815)
_REF_SHOULDER_Z = (7.42, 7.72, 8.30, 9.05, 9.78, 10.38)
_REF_SHOULDER_WIRE = (0.56, 0.56, 0.52, 0.45, 0.35, 0.28)

# Accepted typed-input envelope. Outside it the derivation refuses rather than
# extrapolating the approved shape into a range nobody has reviewed.
US_SIZE_MIN = 3.0
US_SIZE_MAX = 13.5
STONE_DIAMETER_MIN_MM = 3.0
STONE_DIAMETER_MAX_MM = 11.0
STONE_DEPTH_RATIO_MIN = 0.50
STONE_DEPTH_RATIO_MAX = 0.75
BAND_WIDTH_MIN_MM = 1.20
BAND_WIDTH_MAX_MM = 4.00
BAND_THICKNESS_MIN_MM = 0.90
BAND_THICKNESS_MAX_MM = 3.00

# Ratios calibrated from the reference literals above.
_CROWN_HEIGHT_RATIO = _REF_CROWN_HEIGHT / REF_STONE_DIAMETER
_GIRDLE_THICKNESS_RATIO = _REF_GIRDLE_THICKNESS / REF_STONE_DIAMETER
_BASKET_LOWER_EMBED = _REF_SHANK_TOP - _REF_BASKET_LOWER_Z
_BASKET_HEIGHT_PER_PAVILION = _REF_BASKET_HEIGHT / _REF_PAVILION_DEPTH
_GIRDLE_RISE_PER_PAVILION = (_REF_GIRDLE_Z - _REF_BASKET_UPPER_Z) / _REF_PAVILION_DEPTH
_BASKET_LOWER_RADIUS_RATIO = _REF_BASKET_LOWER_RADIUS / _REF_STONE_RADIUS
_BASKET_UPPER_RADIUS_RATIO = _REF_BASKET_UPPER_RADIUS / _REF_STONE_RADIUS
_GALLERY_WIRE_RADIUS_RATIO = _REF_GALLERY_WIRE_RADIUS / _REF_STONE_RADIUS
_PRONG_TIP_HEIGHT_RATIO = _REF_PRONG_TIP_HEIGHT / _REF_STONE_RADIUS
_PRONG_GRIP_PER_GIRDLE = 0.18 / _REF_GIRDLE_THICKNESS

_PETAL_RADIAL_RATIOS = tuple(value / _REF_STONE_RADIUS for value in _REF_PETAL_RADIAL)
_PETAL_TANGENT_RATIOS = tuple(value / _REF_STONE_RADIUS for value in _REF_PETAL_TANGENT)
_PETAL_WIRE_RATIOS = tuple(value / _REF_STONE_RADIUS for value in _REF_PETAL_WIRE)
_PETAL_Z_FRACTIONS = tuple(
    (value - _REF_BASKET_LOWER_Z) / _REF_BASKET_HEIGHT for value in _REF_PETAL_Z
)

_PRONG_RADIAL_RATIOS = tuple(value / _REF_STONE_RADIUS for value in _REF_PRONG_RADIAL)
_PRONG_WIRE_RATIOS = tuple(value / _REF_STONE_RADIUS for value in _REF_PRONG_WIRE)
_PRONG_LOWER_Z_FRACTIONS = tuple(
    (value - _REF_BASKET_LOWER_Z) / _REF_BASKET_HEIGHT for value in _REF_PRONG_LOWER_Z
)

_REF_SHOULDER_ROOT_RADIUS = math.hypot(_REF_SHOULDER_X[0], _REF_SHOULDER_Z[0])
_SHOULDER_ROOT_ANGLE = math.atan2(_REF_SHOULDER_X[0], _REF_SHOULDER_Z[0])
_SHOULDER_ROOT_RADIUS_FRACTION = _REF_SHOULDER_ROOT_RADIUS / _REF_SHANK_CENTER_RADIUS
_SHOULDER_ROOT_Y_RATIO = _REF_SHOULDER_Y[0] / (REF_BAND_WIDTH / 2.0)
_SHOULDER_ROOT_WIRE_RATIO = _REF_SHOULDER_WIRE[0] / REF_BAND_WIDTH
_SHOULDER_NECK_WIRE_RATIO = _REF_SHOULDER_WIRE[-1] / _REF_STONE_RADIUS


def _span_fractions(values):
    """Return each value's normalized position between the first and last value."""
    first = values[0]
    last = values[-1]
    span = last - first
    if span == 0.0:
        return tuple(0.0 for _ in values)
    return tuple((value - first) / span for value in values)


_SHOULDER_X_FRACTIONS = _span_fractions(_REF_SHOULDER_X)
_SHOULDER_Y_FRACTIONS = _span_fractions(_REF_SHOULDER_Y)
_SHOULDER_Z_FRACTIONS = _span_fractions(_REF_SHOULDER_Z)
_SHOULDER_WIRE_FRACTIONS = _span_fractions(_REF_SHOULDER_WIRE)


class MasterParameterError(ValueError):
    """The request cannot be turned into approved-envelope master parameters."""


def _number(container, key, label):
    if not isinstance(container, dict) or key not in container:
        raise MasterParameterError("missing required value: " + label)
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasterParameterError("value must be a number: " + label)
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise MasterParameterError("value must be finite: " + label)
    return value


def _in_range(value, low, high, label):
    if value < low or value > high:
        raise MasterParameterError(
            "{} is outside the approved envelope [{}, {}]: {}".format(
                label, low, high, value
            )
        )
    return value


def _center_stone(spec):
    stones = spec.get("stones")
    if not isinstance(stones, list) or not stones:
        raise MasterParameterError("canonical spec has no stones")
    centers = [
        stone
        for stone in stones
        if isinstance(stone, dict) and stone.get("role") == "center"
    ]
    if len(centers) != 1:
        raise MasterParameterError(
            "this master requires exactly one center stone, found "
            + str(len(centers))
        )
    if len(stones) != 1:
        raise MasterParameterError(
            "this master is solitaire-only; side or accent stones are unsupported"
        )
    stone = centers[0]
    if stone.get("shape") != "round":
        raise MasterParameterError(
            "this master is round-only, got shape: " + str(stone.get("shape"))
        )
    quantity = stone.get("quantity", 1)
    if quantity != 1:
        raise MasterParameterError("center stone quantity must be 1")
    return stone


def inner_diameter_mm(us_size):
    """Return the verified US ring-size inner diameter in millimetres."""
    return RING_SIZE_INTERCEPT_MM + RING_SIZE_SLOPE_MM_PER_US * us_size


def derive_parameters(request):
    """Return the Rhino master parameter dict for a typed configuration request.

    ``request`` must carry ``canonical_spec`` (the nested canonical v0.1 spec).
    Every geometric value in the returned dict is a function of that spec; nothing
    is frozen. Raises ``MasterParameterError`` when the request falls outside the
    approved envelope, so the definition refuses rather than silently substituting
    the reference ring.
    """
    if not isinstance(request, dict):
        raise MasterParameterError("request must be an object")
    spec = request.get("canonical_spec")
    if not isinstance(spec, dict):
        raise MasterParameterError("request is missing canonical_spec")

    ring = spec.get("ring")
    if not isinstance(ring, dict):
        raise MasterParameterError("canonical spec has no ring object")
    if ring.get("size_standard") != "US":
        raise MasterParameterError(
            "ring size standard must be US, got: " + str(ring.get("size_standard"))
        )
    us_size = _in_range(
        _number(ring, "size", "ring.size"), US_SIZE_MIN, US_SIZE_MAX, "ring.size"
    )
    band_width = _in_range(
        _number(ring, "band_width_mm", "ring.band_width_mm"),
        BAND_WIDTH_MIN_MM,
        BAND_WIDTH_MAX_MM,
        "ring.band_width_mm",
    )
    band_thickness = _in_range(
        _number(ring, "band_thickness_mm", "ring.band_thickness_mm"),
        BAND_THICKNESS_MIN_MM,
        BAND_THICKNESS_MAX_MM,
        "ring.band_thickness_mm",
    )

    stone = _center_stone(spec)
    dimensions = stone.get("dimensions_mm")
    length = _number(dimensions, "length", "stone.dimensions_mm.length")
    width = _number(dimensions, "width", "stone.dimensions_mm.width")
    depth = _number(dimensions, "depth", "stone.dimensions_mm.depth")
    if abs(length - width) > 0.05:
        raise MasterParameterError(
            "round master requires length == width within 0.05 mm, got "
            "{} x {}".format(length, width)
        )
    stone_diameter = _in_range(
        (length + width) / 2.0,
        STONE_DIAMETER_MIN_MM,
        STONE_DIAMETER_MAX_MM,
        "stone diameter",
    )
    _in_range(
        depth / stone_diameter,
        STONE_DEPTH_RATIO_MIN,
        STONE_DEPTH_RATIO_MAX,
        "stone depth ratio",
    )

    # Band.
    inner_diameter = inner_diameter_mm(us_size)
    shank_top = inner_diameter / 2.0 + band_thickness
    shank_center_radius = inner_diameter / 2.0 + band_thickness / 2.0

    # Stone.
    stone_radius = stone_diameter / 2.0
    crown_height = _CROWN_HEIGHT_RATIO * stone_diameter
    girdle_thickness = _GIRDLE_THICKNESS_RATIO * stone_diameter
    pavilion_depth = depth - crown_height - girdle_thickness
    if pavilion_depth <= 0.0:
        raise MasterParameterError("stone depth leaves no pavilion")

    # Head, anchored to the top of the band and scaled by the pavilion it holds.
    basket_lower_z = shank_top - _BASKET_LOWER_EMBED
    basket_height = _BASKET_HEIGHT_PER_PAVILION * pavilion_depth
    basket_upper_z = basket_lower_z + basket_height
    girdle_z = basket_upper_z + _GIRDLE_RISE_PER_PAVILION * pavilion_depth
    basket_lower_radius = _BASKET_LOWER_RADIUS_RATIO * stone_radius
    basket_upper_radius = _BASKET_UPPER_RADIUS_RATIO * stone_radius
    gallery_wire_radius = _GALLERY_WIRE_RADIUS_RATIO * stone_radius
    prong_tip_height = _PRONG_TIP_HEIGHT_RATIO * stone_radius
    prong_grip = _PRONG_GRIP_PER_GIRDLE * girdle_thickness

    petal_profile = [
        [
            _PETAL_RADIAL_RATIOS[index] * stone_radius,
            _PETAL_TANGENT_RATIOS[index] * stone_radius,
            basket_lower_z + _PETAL_Z_FRACTIONS[index] * basket_height,
        ]
        for index in range(len(_PETAL_RADIAL_RATIOS))
    ]
    petal_radii = [ratio * stone_radius for ratio in _PETAL_WIRE_RATIOS]

    prong_z = [
        basket_lower_z + fraction * basket_height
        for fraction in _PRONG_LOWER_Z_FRACTIONS
    ]
    prong_z.append(girdle_z - prong_grip)
    prong_z.append(girdle_z + prong_grip)
    prong_z.append(girdle_z + prong_tip_height)
    prong_profile = [
        [_PRONG_RADIAL_RATIOS[index] * stone_radius, prong_z[index]]
        for index in range(len(_PRONG_RADIAL_RATIOS))
    ]
    prong_radii = [ratio * stone_radius for ratio in _PRONG_WIRE_RATIOS]

    # Shoulders: rooted on the band, terminating on the third petal control point
    # so the split branches meet the basket instead of floating beside it.
    root_radius = _SHOULDER_ROOT_RADIUS_FRACTION * shank_center_radius
    root_x = root_radius * math.sin(_SHOULDER_ROOT_ANGLE)
    root_z = root_radius * math.cos(_SHOULDER_ROOT_ANGLE)
    root_y = _SHOULDER_ROOT_Y_RATIO * (band_width / 2.0)
    join_xy = petal_profile[2][0] / math.sqrt(2.0)
    join_z = petal_profile[2][2]
    root_wire = _SHOULDER_ROOT_WIRE_RATIO * band_width
    neck_wire = _SHOULDER_NECK_WIRE_RATIO * stone_radius

    shoulder_path = [
        [
            root_x + _SHOULDER_X_FRACTIONS[index] * (join_xy - root_x),
            root_y + _SHOULDER_Y_FRACTIONS[index] * (join_xy - root_y),
            root_z + _SHOULDER_Z_FRACTIONS[index] * (join_z - root_z),
        ]
        for index in range(len(_SHOULDER_X_FRACTIONS))
    ]
    shoulder_radii = [
        root_wire + fraction * (neck_wire - root_wire)
        for fraction in _SHOULDER_WIRE_FRACTIONS
    ]

    return {
        "template_id": request.get("template_id"),
        "derivation_version": MASTER_PARAMETER_DERIVATION_VERSION,
        "units": "millimeters",
        "reference_band_type": "split_ring_solitaire",
        "reference_head_type": "classic_basket",
        "surface_continuity": (
            "split_shoulders_into_mid_basket_petals_below_independent_prongs"
        ),
        "source_ownership": "myjewl_original_rhinocommon_source",
        "manufacturing_authority": False,
        "ring_size_us": us_size,
        "inner_diameter": inner_diameter,
        "shank_width": band_width,
        "shank_radial_thickness": band_thickness,
        "shoulder_root_radius": shoulder_radii[0],
        "shoulder_neck_radius": shoulder_radii[-1],
        "shoulder_split_half_gap_root": shoulder_path[1][1],
        "shoulder_split_half_gap_neck": shoulder_path[-1][1],
        "shoulder_contact_z": shoulder_path[1][2],
        "shoulder_path": shoulder_path,
        "shoulder_radii": shoulder_radii,
        "stone_diameter": stone_diameter,
        "stone_total_depth": depth,
        "stone_table_ratio": _REF_TABLE_RATIO,
        "stone_crown_height": crown_height,
        "stone_girdle_thickness": girdle_thickness,
        "stone_girdle_z": girdle_z,
        "basket_lower_radius": basket_lower_radius,
        "basket_upper_radius": basket_upper_radius,
        "basket_lower_z": basket_lower_z,
        "basket_upper_z": basket_upper_z,
        "basket_petal_profile": petal_profile,
        "basket_petal_radii": petal_radii,
        "gallery_wire_radius": gallery_wire_radius,
        "prong_profile": prong_profile,
        "prong_radii": prong_radii,
        "prong_root_radius": prong_radii[0],
        "prong_seat_radius": prong_radii[3],
        "prong_tip_radius": prong_radii[-1],
        "prong_tip_height": prong_tip_height,
    }


def assembly_metrics(parameters):
    """Return non-calibrated source-interface diagnostics for a derived parameter set.

    These are the Goal 0011 V4 connection checks re-expressed against derived
    values: they confirm the shoulders still root in the band, the petals still
    meet the rails, and the prongs still grip the stone after scaling. They are
    interface diagnostics for local preflight, **not** manufacturing gates -- they
    certify nothing and are never a pass/fail verdict on castability or fit.
    """
    inner_radius = parameters["inner_diameter"] / 2.0
    shank_center_radius = inner_radius + parameters["shank_radial_thickness"] / 2.0
    shank_radial_radius = parameters["shank_radial_thickness"] / 2.0
    shank_y_radius = parameters["shank_width"] / 2.0
    stone_radius = parameters["stone_diameter"] / 2.0
    girdle_bottom = (
        parameters["stone_girdle_z"] - parameters["stone_girdle_thickness"] / 2.0
    )
    petals = parameters["basket_petal_profile"]
    petal_radii = parameters["basket_petal_radii"]
    prongs = parameters["prong_profile"]
    prong_radii = parameters["prong_radii"]
    shoulder = parameters["shoulder_path"]
    shoulder_radii = parameters["shoulder_radii"]
    gallery_wire = parameters["gallery_wire_radius"]
    seat_wire = gallery_wire * 0.84

    root = shoulder[0]
    shoulder_root_ellipse = (
        ((math.hypot(root[0], root[2]) - shank_center_radius) / shank_radial_radius) ** 2
        + (root[1] / shank_y_radius) ** 2
    )
    lower_rail_ellipse = (
        (math.hypot(parameters["basket_lower_radius"], parameters["basket_lower_z"])
         - shank_center_radius) / shank_radial_radius
    ) ** 2

    join = [petals[2][0] / math.sqrt(2.0), petals[2][0] / math.sqrt(2.0), petals[2][2]]
    shoulder_petal_error = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(shoulder[-1], join))
    )
    shoulder_vertical_clearance = girdle_bottom - (shoulder[-1][2] + shoulder_radii[-1])
    shoulder_petal_overlap = shoulder_radii[-1] + petal_radii[2] - shoulder_petal_error
    petal_upper_overlap = petal_radii[-1] + seat_wire - abs(
        parameters["basket_upper_radius"] - petals[-1][0]
    )
    prong_collar_overlap = prong_radii[2] + seat_wire - abs(
        parameters["basket_upper_radius"] - prongs[2][0]
    )
    prong_root_overlap = prong_radii[0] + gallery_wire - abs(
        prongs[0][0] - parameters["basket_lower_radius"]
    )
    prong_seat_overlap = prong_radii[3] - math.hypot(
        stone_radius - prongs[3][0], girdle_bottom - prongs[3][1]
    )
    crown_star_radius = stone_radius * 0.79
    crown_star_z = parameters["stone_girdle_z"] + parameters["stone_crown_height"] * 0.46
    tip_crown_overlap = prong_radii[-1] * 1.08 - math.hypot(
        prongs[-1][0] - crown_star_radius, prongs[-1][1] - crown_star_z
    )

    metrics = {
        "shoulder_root_shank_ellipse_value": shoulder_root_ellipse,
        "lower_rail_shank_ellipse_value": lower_rail_ellipse,
        "shoulder_end_petal_centerline_error_mm": shoulder_petal_error,
        "shoulder_petal_overlap_mm": shoulder_petal_overlap,
        "shoulder_vertical_clearance_below_girdle_mm": shoulder_vertical_clearance,
        "petal_upper_collar_overlap_mm": petal_upper_overlap,
        "prong_collar_overlap_mm": prong_collar_overlap,
        "prong_root_lower_rail_overlap_mm": prong_root_overlap,
        "prong_seat_girdle_overlap_mm": prong_seat_overlap,
        "prong_tip_crown_overlap_mm": tip_crown_overlap,
    }
    metrics["source_preflight_ready"] = bool(
        shoulder_root_ellipse < 1.0
        and lower_rail_ellipse < 1.0
        and shoulder_petal_error < 0.001
        and shoulder_vertical_clearance > 0.0
        and shoulder_petal_overlap > 0.0
        and petal_upper_overlap > 0.0
        and prong_collar_overlap > 0.0
        and prong_root_overlap > 0.0
        and prong_seat_overlap > 0.0
        and tip_crown_overlap > 0.0
    )
    metrics["manufacturing_authority"] = False
    return metrics
# --- END MYJEWL MASTER PARAMETER DERIVATION v1 ---


REFERENCE_REQUEST: dict[str, Any] = {
    "template_id": "goal0014-parametric-round-solitaire-master-v5",
    "canonical_spec": {
        "ring": {
            "size": REF_US_SIZE,
            "size_standard": "US",
            "band_width_mm": REF_BAND_WIDTH,
            "band_thickness_mm": REF_BAND_THICKNESS,
        },
        "stones": [
            {
                "stone_id": "center",
                "role": "center",
                "shape": "round",
                "quantity": 1,
                "dimensions_mm": {
                    "length": REF_STONE_DIAMETER,
                    "width": REF_STONE_DIAMETER,
                    "depth": REF_STONE_DEPTH,
                },
            }
        ],
    },
}

def configuration_request(
    us_size: float,
    stone_diameter_mm: float,
    stone_depth_mm: float,
    band_width_mm: float = REF_BAND_WIDTH,
    band_thickness_mm: float = REF_BAND_THICKNESS,
    template_id: str | None = None,
    stone_width_mm: float | None = None,
) -> dict[str, Any]:
    """Return a typed configuration request for the round solitaire master.

    ``stone_width_mm`` defaults to ``stone_diameter_mm`` so the solved batch and
    the promotion gate keep sending a perfectly round stone. A caller relaying a
    customer's typed measurements should pass both, so the master's own
    "length == width within 0.05 mm" rule refuses a near-round stone rather than
    quietly averaging it into one.
    """
    if stone_width_mm is None:
        stone_width_mm = stone_diameter_mm
    request: dict[str, Any] = {
        "template_id": template_id or REFERENCE_REQUEST["template_id"],
        "canonical_spec": {
            "ring": {
                "size": us_size,
                "size_standard": "US",
                "band_width_mm": band_width_mm,
                "band_thickness_mm": band_thickness_mm,
            },
            "stones": [
                {
                    "stone_id": "center",
                    "role": "center",
                    "shape": "round",
                    "quantity": 1,
                    "dimensions_mm": {
                        "length": stone_diameter_mm,
                        "width": stone_width_mm,
                        "depth": stone_depth_mm,
                    },
                }
            ],
        },
    }
    return request


def envelope() -> dict[str, Any]:
    """Return the typed envelope this master refuses to build outside of.

    Published so the catalog can describe what the master *could* build without
    re-declaring the bounds. ``derive_parameters`` stays the only gate; this is a
    description of it, never a second copy of the rule.
    """
    return {
        "center_stone_shape": "round",
        "ring_size_us": {"minimum": US_SIZE_MIN, "maximum": US_SIZE_MAX},
        "stone_diameter_mm": {
            "minimum": STONE_DIAMETER_MIN_MM,
            "maximum": STONE_DIAMETER_MAX_MM,
        },
        "stone_depth_ratio": {
            "minimum": STONE_DEPTH_RATIO_MIN,
            "maximum": STONE_DEPTH_RATIO_MAX,
        },
        "band_width_mm": {"minimum": BAND_WIDTH_MIN_MM, "maximum": BAND_WIDTH_MAX_MM},
        "band_thickness_mm": {
            "minimum": BAND_THICKNESS_MIN_MM,
            "maximum": BAND_THICKNESS_MAX_MM,
        },
        "derivation_version": MASTER_PARAMETER_DERIVATION_VERSION,
    }


#: The bounded config-differential batch: input responsiveness, not catalog coverage.
#: The control is the approved Goal 0011 V4 configuration.
DIFFERENTIAL_BATCH: dict[str, dict[str, Any]] = {
    "small": configuration_request(4.0, 5.00, 3.00),
    "control": configuration_request(REF_US_SIZE, REF_STONE_DIAMETER, REF_STONE_DEPTH),
    "large": configuration_request(9.0, 8.00, 4.80),
}

#: Parameters whose value is expected to change whenever the typed request changes.
GEOMETRIC_PARAMETER_KEYS: tuple[str, ...] = (
    "inner_diameter",
    "shank_width",
    "shank_radial_thickness",
    "stone_diameter",
    "stone_total_depth",
    "stone_crown_height",
    "stone_girdle_thickness",
    "stone_girdle_z",
    "basket_lower_radius",
    "basket_upper_radius",
    "basket_lower_z",
    "basket_upper_z",
    "gallery_wire_radius",
    "prong_tip_height",
)


def derivation_block_source() -> str:
    """Return the inlined derivation block verbatim, markers included."""
    from pathlib import Path

    text = Path(__file__).read_text(encoding="utf-8")
    begin = text.index(BLOCK_BEGIN_MARKER)
    end = text.index(BLOCK_END_MARKER) + len(BLOCK_END_MARKER)
    return text[begin:end]


BLOCK_BEGIN_MARKER = "# --- BEGIN MYJEWL MASTER PARAMETER DERIVATION v1 ---"
BLOCK_END_MARKER = "# --- END MYJEWL MASTER PARAMETER DERIVATION v1 ---"
