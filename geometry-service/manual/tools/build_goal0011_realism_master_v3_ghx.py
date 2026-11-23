#!/usr/bin/env python3
"""Build Goal 0011 v3 with shoulders terminating on the lower basket rail.

V2 was structurally different but visually too close to V1. This stronger,
MyJewl-original revision moves the split-shoulder terminal junction from the
upper seat collar to the lower basket rail. Gallery petals and prongs rise
independently above that junction. The source remains a bounded visual
candidate, not a manufacturing verdict.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0011_realism_master_v2_ghx import SCRIPT as V2_SCRIPT
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx
    from build_goal0011_realism_master_v2_ghx import SCRIPT as V2_SCRIPT


TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v3"
MASTER_FILE_STEM = "solitaire_realism_master_v3"
SOURCE_OWNERSHIP = "myjewl_original_rhinocommon_source"
BASKET_LOWER_RADIUS = 1.32
BASKET_LOWER_Z = 9.70
BASKET_UPPER_RADIUS = 2.42
BASKET_UPPER_Z = 10.90
GALLERY_WIRE_RADIUS = 0.20
SHOULDER_CONNECTION_RAIL = "lower"

SHOULDER_PATH = (
    (5.35, 0.25, 7.42),
    (4.82, 0.28, 7.72),
    (4.30, 0.38, 8.15),
    (3.45, 0.48, 8.72),
    (2.35, 0.44, 9.25),
    (1.28996, 0.28, 9.70),
)
SHOULDER_RADII = (0.56, 0.56, 0.52, 0.44, 0.34, 0.26)
PETAL_RADII = (1.32, 1.50, 1.85, 2.24)
PETAL_Z = (9.70, 9.94, 10.38, 10.90)
PETAL_WIRE_RADII = (0.25, 0.235, 0.21, 0.18)
PRONG_RADII = (1.38, 1.66, 2.24, 3.06, 2.86, 2.68)
PRONG_Z = (9.72, 10.02, 10.82, 11.92, 12.28, 12.54)
PRONG_WIRE_RADII = (0.30, 0.29, 0.27, 0.235, 0.20, 0.17)


def connection_metrics() -> dict[str, float | bool]:
    """Return non-calibrated source-interface diagnostics for local preflight."""
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
    shoulder_lower_rail_error = math.hypot(shoulder_end[0], shoulder_end[1]) - BASKET_LOWER_RADIUS
    stone_girdle_bottom = 12.10 - 0.18 / 2.0
    shoulder_vertical_clearance = stone_girdle_bottom - (
        shoulder_end[2] + SHOULDER_RADII[-1]
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
        and abs(shoulder_lower_rail_error) < 0.001
        and shoulder_vertical_clearance > 1.5
        and petal_upper_overlap > 0.0
        and prong_collar_overlap > 0.0
        and prong_root_overlap > 0.0
        and prong_seat_overlap > 0.0
        and tip_crown_overlap > 0.0
    )
    return {
        "shoulder_root_shank_ellipse_value": shoulder_root_ellipse,
        "lower_rail_shank_ellipse_value": lower_rail_ellipse,
        "shoulder_end_lower_rail_centerline_error_mm": shoulder_lower_rail_error,
        "shoulder_vertical_clearance_below_girdle_mm": shoulder_vertical_clearance,
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
        raise RuntimeError("expected one v2 source fragment, found {}: {!r}".format(count, old[:90]))
    return source.replace(old, new, 1)


def _rewrite_source() -> str:
    source = V2_SCRIPT
    replacements = (
        ("goal0011-realism-first-round-solitaire-master-v2", TEMPLATE_ID),
        ('"authoring_status": "owned_rhinocommon_lower_collar_revision_pending_visual_review"', '"authoring_status": "owned_rhinocommon_lower_rail_revision_pending_visual_review"'),
        ('"shoulder_neck_radius": 0.32', '"shoulder_neck_radius": 0.26'),
        ('"shoulder_split_half_gap_neck": 0.42', '"shoulder_split_half_gap_neck": 0.28'),
        ('"surface_continuity": "split_shoulders_into_lower_gallery_collar_with_independent_inward_seated_prongs"', '"surface_continuity": "split_shoulders_into_lower_basket_rail_with_independent_gallery_and_prongs"'),
        (
            '''        front = [
            rg.Point3d(side * 5.35, -0.25, 7.42),
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.52, -0.36, 8.44),
            rg.Point3d(side * 3.95, -0.47, 9.18),
            rg.Point3d(side * 3.20, -0.54, 9.95),
            rg.Point3d(side * 2.3833, -P["shoulder_split_half_gap_neck"], P["basket_upper_z"]),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], P["shoulder_root_radius"], 0.54, 0.48, 0.40, P["shoulder_neck_radius"]]''',
            '''        front = [
            rg.Point3d(side * 5.35, -0.25, 7.42),
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.30, -0.38, 8.15),
            rg.Point3d(side * 3.45, -0.48, 8.72),
            rg.Point3d(side * 2.35, -0.44, 9.25),
            rg.Point3d(side * 1.28996, -P["shoulder_split_half_gap_neck"], P["basket_lower_z"]),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], P["shoulder_root_radius"], 0.52, 0.44, 0.34, P["shoulder_neck_radius"]]''',
        ),
        ("solitaire_realism_master_v2.mtl", MASTER_FILE_STEM + ".mtl"),
        ("solitaire_realism_master_v2.3dm", MASTER_FILE_STEM + ".3dm"),
        ("solitaire_realism_master_v2.obj", MASTER_FILE_STEM + ".obj"),
        ("solitaire_realism_master_v2_receipt.json", MASTER_FILE_STEM + "_receipt.json"),
        ("myjewl.goal0011.realism_master_receipt.v2", "myjewl.goal0011.realism_master_receipt.v3"),
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
    built = build(root / "manual" / "rhino_compute_bridge" / "goal0011_realism_master_v3.ghx")
    print(built)
