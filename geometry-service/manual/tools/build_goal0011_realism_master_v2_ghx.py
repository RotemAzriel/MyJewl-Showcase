#!/usr/bin/env python3
"""Build Goal 0011 v2 with split shoulders resolved into a lower basket collar.

This MyJewl-original revision responds to the operator's visual review of v1.
The split shank no longer terminates on the high diamond-seat rail; it converges
into a lower, tighter gallery collar. The four prongs alone continue upward to
retain the stone. All dimensions remain bounded to the 6.5 mm round / US 6.5
reference, and no manufacturing verdict is assigned.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0011_realism_master_ghx import SCRIPT as V1_SCRIPT
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx
    from build_goal0011_realism_master_ghx import SCRIPT as V1_SCRIPT


TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v2"
MASTER_FILE_STEM = "solitaire_realism_master_v2"
SOURCE_OWNERSHIP = "myjewl_original_rhinocommon_source"
BASKET_LOWER_RADIUS = 1.32
BASKET_LOWER_Z = 9.70
BASKET_UPPER_RADIUS = 2.42
BASKET_UPPER_Z = 10.90
GALLERY_WIRE_RADIUS = 0.20

# The split opens gradually from the upper shank and converges into the lower
# collar. Its terminal sections remain below the diamond pavilion/seat zone.
SHOULDER_PATH = (
    (5.35, 0.25, 7.42),
    (4.82, 0.28, 7.72),
    (4.52, 0.36, 8.44),
    (3.95, 0.47, 9.18),
    (3.20, 0.54, 9.95),
    (2.3833, 0.42, 10.90),
)
SHOULDER_RADII = (0.56, 0.56, 0.54, 0.48, 0.40, 0.32)
PETAL_RADII = (1.32, 1.50, 1.85, 2.24)
PETAL_Z = (9.70, 9.94, 10.38, 10.90)
PETAL_WIRE_RADII = (0.25, 0.235, 0.21, 0.18)
PRONG_RADII = (1.38, 1.66, 2.24, 3.06, 2.86, 2.68)
PRONG_Z = (9.72, 10.02, 10.82, 11.92, 12.28, 12.54)
PRONG_WIRE_RADII = (0.30, 0.29, 0.27, 0.235, 0.20, 0.17)


def connection_metrics() -> dict[str, float | bool]:
    """Return v2 source-interface diagnostics; never manufacturing gates."""
    inner_radius = 16.91 / 2.0
    shank_center_radius = inner_radius + 1.65 / 2.0
    shank_radial_radius = 1.65 / 2.0
    shank_y_radius = 2.05 / 2.0
    shoulder_x, shoulder_y, shoulder_z = SHOULDER_PATH[0]
    shoulder_root_ellipse = (
        ((math.hypot(shoulder_x, shoulder_z) - shank_center_radius) / shank_radial_radius) ** 2
        + (shoulder_y / shank_y_radius) ** 2
    )
    lower_rail_ellipse = (
        ((math.hypot(BASKET_LOWER_RADIUS, BASKET_LOWER_Z) - shank_center_radius) / shank_radial_radius) ** 2
    )
    shoulder_end = SHOULDER_PATH[-1]
    shoulder_collar_error = math.hypot(shoulder_end[0], shoulder_end[1]) - BASKET_UPPER_RADIUS
    shoulder_vertical_clearance_below_girdle = (12.10 - 0.18 / 2.0) - (
        SHOULDER_PATH[-1][2] + SHOULDER_RADII[-1]
    )
    petal_upper_overlap = PETAL_WIRE_RADII[-1] + GALLERY_WIRE_RADIUS * 0.84 - abs(
        BASKET_UPPER_RADIUS - PETAL_RADII[-1]
    )
    prong_collar_overlap = PRONG_WIRE_RADII[2] + GALLERY_WIRE_RADIUS * 0.84 - abs(
        BASKET_UPPER_RADIUS - PRONG_RADII[2]
    )
    prong_root_overlap = PRONG_WIRE_RADII[0] + GALLERY_WIRE_RADIUS - abs(
        PRONG_RADII[0] - BASKET_LOWER_RADIUS
    )
    stone_girdle_bottom = 12.10 - 0.18 / 2.0
    prong_seat_distance = math.hypot(3.25 - PRONG_RADII[3], stone_girdle_bottom - PRONG_Z[3])
    prong_seat_overlap = PRONG_WIRE_RADII[3] - prong_seat_distance
    crown_star_radius = 3.25 * 0.79
    crown_star_z = 12.10 + 0.96 * 0.46
    tip_radius = 0.17 * 1.08
    tip_crown_distance = math.hypot(PRONG_RADII[-1] - crown_star_radius, PRONG_Z[-1] - crown_star_z)
    tip_crown_overlap = tip_radius - tip_crown_distance
    ready = (
        shoulder_root_ellipse < 1.0
        and lower_rail_ellipse < 1.0
        and abs(shoulder_collar_error) < 0.001
        and shoulder_vertical_clearance_below_girdle > 0.5
        and petal_upper_overlap > 0.0
        and prong_collar_overlap > 0.0
        and prong_root_overlap > 0.0
        and prong_seat_overlap > 0.0
        and tip_crown_overlap > 0.0
    )
    return {
        "shoulder_root_shank_ellipse_value": shoulder_root_ellipse,
        "lower_rail_shank_ellipse_value": lower_rail_ellipse,
        "shoulder_end_collar_centerline_error_mm": shoulder_collar_error,
        "shoulder_vertical_clearance_below_girdle_mm": shoulder_vertical_clearance_below_girdle,
        "petal_upper_collar_overlap_mm": petal_upper_overlap,
        "prong_collar_overlap_mm": prong_collar_overlap,
        "prong_root_lower_rail_overlap_mm": prong_root_overlap,
        "prong_seat_girdle_overlap_mm": prong_seat_overlap,
        "prong_tip_crown_overlap_mm": tip_crown_overlap,
        "source_preflight_ready": ready,
    }


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError("expected one v1 source fragment, found {}: {!r}".format(count, old[:90]))
    return source.replace(old, new, 1)


def _rewrite_source() -> str:
    source = V1_SCRIPT
    replacements = (
        ("goal0011-realism-first-round-solitaire-master-v1", TEMPLATE_ID),
        ('"authoring_status": "owned_rhinocommon_master_pending_visual_review"', '"authoring_status": "owned_rhinocommon_lower_collar_revision_pending_visual_review"'),
        ('"basket_upper_radius": 2.72', '"basket_upper_radius": 2.42'),
        ('"basket_upper_z": 11.30', '"basket_upper_z": 10.90'),
        ('"shoulder_neck_radius": 0.34', '"shoulder_neck_radius": 0.32'),
        ('"shoulder_split_half_gap_neck": 0.62', '"shoulder_split_half_gap_neck": 0.42'),
        ('"surface_continuity": "upper_shank_cathedral_shoulders_into_open_basket_and_inward_seated_prongs"', '"surface_continuity": "split_shoulders_into_lower_gallery_collar_with_independent_inward_seated_prongs"'),
        (
            '''        front = [
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),''',
            '''        front = [
            rg.Point3d(side * 5.35, -0.25, 7.42),
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),''',
        ),
        (
            '''            rg.Point3d(side * 4.48, -0.36, 8.55),
            rg.Point3d(side * 3.82, -0.46, 9.56),
            rg.Point3d(side * 3.18, -0.56, 10.55),
            rg.Point3d(side * 2.6484, -P["shoulder_split_half_gap_neck"], P["basket_upper_z"]),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], 0.54, 0.48, 0.41, P["shoulder_neck_radius"]]''',
            '''            rg.Point3d(side * 4.52, -0.36, 8.44),
            rg.Point3d(side * 3.95, -0.47, 9.18),
            rg.Point3d(side * 3.20, -0.54, 9.95),
            rg.Point3d(side * 2.3833, -P["shoulder_split_half_gap_neck"], P["basket_upper_z"]),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], P["shoulder_root_radius"], 0.54, 0.48, 0.40, P["shoulder_neck_radius"]]''',
        ),
        (
            '''            rg.Point3d(radial.X * 1.58 + tangent.X * 0.08, radial.Y * 1.58 + tangent.Y * 0.08, 10.08),
            rg.Point3d(radial.X * 2.14 + tangent.X * 0.05, radial.Y * 2.14 + tangent.Y * 0.05, 10.76),
            rg.Point3d(radial.X * 2.54, radial.Y * 2.54, P["basket_upper_z"]),''',
            '''            rg.Point3d(radial.X * 1.50 + tangent.X * 0.08, radial.Y * 1.50 + tangent.Y * 0.08, 9.94),
            rg.Point3d(radial.X * 1.85 + tangent.X * 0.05, radial.Y * 1.85 + tangent.Y * 0.05, 10.38),
            rg.Point3d(radial.X * 2.24, radial.Y * 2.24, P["basket_upper_z"]),''',
        ),
        (
            '''            rg.Point3d(radial.X * 1.68, radial.Y * 1.68, 10.08),
            rg.Point3d(radial.X * 2.54, radial.Y * 2.54, 11.20),
            rg.Point3d(radial.X * 3.04, radial.Y * 3.04, P["stone_girdle_z"] - 0.14),
            rg.Point3d(radial.X * 2.88, radial.Y * 2.88, P["stone_girdle_z"] + 0.20),''',
            '''            rg.Point3d(radial.X * 1.66, radial.Y * 1.66, 10.02),
            rg.Point3d(radial.X * 2.24, radial.Y * 2.24, 10.82),
            rg.Point3d(radial.X * 3.06, radial.Y * 3.06, P["stone_girdle_z"] - 0.18),
            rg.Point3d(radial.X * 2.86, radial.Y * 2.86, P["stone_girdle_z"] + 0.18),''',
        ),
        ("solitaire_realism_master_v1.mtl", MASTER_FILE_STEM + ".mtl"),
        ("solitaire_realism_master_v1.3dm", MASTER_FILE_STEM + ".3dm"),
        ("solitaire_realism_master_v1.obj", MASTER_FILE_STEM + ".obj"),
        ("solitaire_realism_master_v1_receipt.json", MASTER_FILE_STEM + "_receipt.json"),
        ("myjewl.goal0011.realism_master_receipt.v1", "myjewl.goal0011.realism_master_receipt.v2"),
    )
    for old, new in replacements:
        source = _replace_once(source, old, new)
    return source


SCRIPT = _rewrite_source()


def build(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_python3_ghx(
        SCRIPT,
        inputs=(("RH_IN:json_string", "json_string", "item"), ("RH_IN:job_id", "job_id", "item")),
        outputs=(("RH_OUT:response_json", "response_json"),),
    ))
    return destination


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    built = build(root / "manual" / "rhino_compute_bridge" / "goal0011_realism_master_v2.ghx")
    print(built)
