#!/usr/bin/env python3
"""Build Goal 0011 v4 with split shoulders joined to mid-basket supports.

V4 is the middle-height response to the V2/V3 review: each shoulder branch
joins a gallery petal below the stone-seat rail but above the lower basket
rail. This preserves a cathedral transition while keeping the shoulder metal
out of the diamond-retention zone. It is a bounded visual candidate only.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0011_realism_master_v3_ghx import SCRIPT as V3_SCRIPT
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx
    from build_goal0011_realism_master_v3_ghx import SCRIPT as V3_SCRIPT


TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"
MASTER_FILE_STEM = "solitaire_realism_master_v4"
SOURCE_OWNERSHIP = "myjewl_original_rhinocommon_source"
BASKET_LOWER_RADIUS = 1.32
BASKET_LOWER_Z = 9.70
BASKET_UPPER_RADIUS = 2.42
BASKET_UPPER_Z = 10.90
GALLERY_WIRE_RADIUS = 0.20
SHOULDER_CONNECTION_RAIL = "petals"

PETAL_RADII = (1.32, 1.50, 1.85, 2.24)
PETAL_Z = (9.70, 9.94, 10.38, 10.90)
PETAL_TANGENT_OFFSETS = (0.0, 0.08, 0.0, 0.0)
PETAL_WIRE_RADII = (0.25, 0.235, 0.21, 0.18)
PETAL_JOIN_XY = PETAL_RADII[2] / math.sqrt(2.0)

SHOULDER_PATH = (
    (5.35, 0.25, 7.42),
    (4.82, 0.28, 7.72),
    (4.35, 0.42, 8.30),
    (3.55, 0.66, 9.05),
    (2.45, 0.98, 9.78),
    (PETAL_JOIN_XY, PETAL_JOIN_XY, PETAL_Z[2]),
)
SHOULDER_RADII = (0.56, 0.56, 0.52, 0.45, 0.35, 0.28)
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
    petal_join = (PETAL_JOIN_XY, PETAL_JOIN_XY, PETAL_Z[2])
    shoulder_petal_error = math.sqrt(sum((a - b) ** 2 for a, b in zip(shoulder_end, petal_join)))
    stone_girdle_bottom = 12.10 - 0.18 / 2.0
    shoulder_vertical_clearance = stone_girdle_bottom - (
        shoulder_end[2] + SHOULDER_RADII[-1]
    )
    shoulder_petal_overlap = SHOULDER_RADII[-1] + PETAL_WIRE_RADII[2] - shoulder_petal_error
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
        and shoulder_petal_error < 0.001
        and shoulder_vertical_clearance > 1.0
        and shoulder_petal_overlap > 0.0
        and petal_upper_overlap > 0.0
        and prong_collar_overlap > 0.0
        and prong_root_overlap > 0.0
        and prong_seat_overlap > 0.0
        and tip_crown_overlap > 0.0
    )
    return {
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
        "source_preflight_ready": ready,
    }


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError("expected one v3 source fragment, found {}: {!r}".format(count, old[:90]))
    return source.replace(old, new, 1)


def _rewrite_source() -> str:
    source = V3_SCRIPT
    replacements = (
        ("goal0011-realism-first-round-solitaire-master-v3", TEMPLATE_ID),
        ('"authoring_status": "owned_rhinocommon_lower_rail_revision_pending_visual_review"', '"authoring_status": "owned_rhinocommon_mid_petal_revision_pending_visual_review"'),
        ('"shoulder_neck_radius": 0.26', '"shoulder_neck_radius": 0.28'),
        ('"shoulder_split_half_gap_neck": 0.28', '"shoulder_split_half_gap_neck": 1.30815'),
        ('"surface_continuity": "split_shoulders_into_lower_basket_rail_with_independent_gallery_and_prongs"', '"surface_continuity": "split_shoulders_into_mid_basket_petals_below_independent_prongs"'),
        (
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
            '''        front = [
            rg.Point3d(side * 5.35, -0.25, 7.42),
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.35, -0.42, 8.30),
            rg.Point3d(side * 3.55, -0.66, 9.05),
            rg.Point3d(side * 2.45, -0.98, 9.78),
            rg.Point3d(side * 1.30815, -P["shoulder_split_half_gap_neck"], 10.38),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], P["shoulder_root_radius"], 0.52, 0.45, 0.35, P["shoulder_neck_radius"]]''',
        ),
        (
            '''            rg.Point3d(radial.X * 1.85 + tangent.X * 0.05, radial.Y * 1.85 + tangent.Y * 0.05, 10.38),''',
            '''            rg.Point3d(radial.X * 1.85, radial.Y * 1.85, 10.38),''',
        ),
        ("solitaire_realism_master_v3.mtl", MASTER_FILE_STEM + ".mtl"),
        ("solitaire_realism_master_v3.3dm", MASTER_FILE_STEM + ".3dm"),
        ("solitaire_realism_master_v3.obj", MASTER_FILE_STEM + ".obj"),
        ("solitaire_realism_master_v3_receipt.json", MASTER_FILE_STEM + "_receipt.json"),
        ("myjewl.goal0011.realism_master_receipt.v3", "myjewl.goal0011.realism_master_receipt.v4"),
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
    built = build(root / "manual" / "rhino_compute_bridge" / "goal0011_realism_master_v4.ghx")
    print(built)
