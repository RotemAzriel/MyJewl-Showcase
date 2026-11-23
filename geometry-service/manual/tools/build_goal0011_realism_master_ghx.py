#!/usr/bin/env python3
"""Build the bounded Goal 0011 realism-first RhinoCommon definition.

The implementation deliberately derives from MyJewl's owned Goal 0010
RhinoCommon source.  It corrects the shoulder, basket, and prong control
geometry while preserving the proven 20-part receipt contract.  Rhino remains
the geometry authority; the generated GHX and its exports are non-certifying
until the required visual review is recorded.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
    from manual.tools.build_goal0010_component_pilot_ghx import SCRIPT as BASE_SCRIPT
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx
    from build_goal0010_component_pilot_ghx import SCRIPT as BASE_SCRIPT


TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v1"
MASTER_FILE_STEM = "solitaire_realism_master_v1"
SOURCE_OWNERSHIP = "myjewl_original_rhinocommon_source"
BASKET_LOWER_RADIUS = 1.32
BASKET_LOWER_Z = 9.70
BASKET_UPPER_RADIUS = 2.72
BASKET_UPPER_Z = 11.30
GALLERY_WIRE_RADIUS = 0.20

# Millimetre-space control geometry shared with the local, non-authoritative
# source-intent renderer.  The Azure Rhino solve remains the sole geometry
# output authority.
SHOULDER_PATH = (
    (4.82, 0.28, 7.72),
    (4.48, 0.36, 8.55),
    (3.82, 0.46, 9.56),
    (3.18, 0.56, 10.55),
    (2.6484, 0.62, 11.30),
)
SHOULDER_RADII = (0.56, 0.54, 0.48, 0.41, 0.34)
PETAL_RADII = (1.32, 1.58, 2.14, 2.54)
PETAL_Z = (9.70, 10.08, 10.76, 11.30)
PETAL_WIRE_RADII = (0.25, 0.235, 0.21, 0.18)
PRONG_RADII = (1.38, 1.68, 2.54, 3.04, 2.88, 2.68)
PRONG_Z = (9.72, 10.08, 11.20, 11.96, 12.30, 12.54)
PRONG_WIRE_RADII = (0.30, 0.29, 0.27, 0.235, 0.20, 0.17)


def connection_metrics() -> dict[str, float | bool]:
    """Return non-certifying control-path continuity diagnostics in mm.

    Negative gaps or positive overlap margins mean the authored source paths
    overlap at the named interface. These are preflight diagnostics, not
    manufacturing clearances or calibrated geometry gates.
    """
    inner_radius = 16.91 / 2.0
    shank_center_radius = inner_radius + 1.65 / 2.0
    shank_radial_radius = 1.65 / 2.0
    shank_y_radius = 2.05 / 2.0

    shoulder_x, shoulder_y, shoulder_z = SHOULDER_PATH[0]
    shoulder_root_ellipse = (
        ((math.hypot(shoulder_x, shoulder_z) - shank_center_radius) / shank_radial_radius) ** 2
        + (shoulder_y / shank_y_radius) ** 2
    )
    # The lower gallery meets the shank near its +/-X arcs.
    lower_rail_x = PETAL_RADII[0]
    lower_rail_ellipse = (
        ((math.hypot(lower_rail_x, PETAL_Z[0]) - shank_center_radius) / shank_radial_radius) ** 2
    )
    shoulder_end = SHOULDER_PATH[-1]
    shoulder_seat_centerline_error = math.hypot(shoulder_end[0], shoulder_end[1]) - BASKET_UPPER_RADIUS
    petal_upper_overlap = PETAL_WIRE_RADII[-1] + GALLERY_WIRE_RADIUS * 0.84 - abs(BASKET_UPPER_RADIUS - PETAL_RADII[-1])
    prong_root_overlap = PRONG_WIRE_RADII[0] + GALLERY_WIRE_RADIUS - abs(PRONG_RADII[0] - PETAL_RADII[0])
    # The fourth prong control section seats against the lower girdle edge.
    stone_girdle_bottom = 12.10 - 0.18 / 2.0
    prong_seat_distance = math.hypot(3.25 - PRONG_RADII[3], stone_girdle_bottom - PRONG_Z[3])
    prong_seat_overlap = PRONG_WIRE_RADII[3] - prong_seat_distance
    # The final inward turn overlaps the star/crown ring at the same height.
    crown_star_radius = 3.25 * 0.79
    crown_star_z = 12.10 + 0.96 * 0.46
    tip_radius = 0.17 * 1.08
    tip_crown_distance = math.hypot(PRONG_RADII[-1] - crown_star_radius, PRONG_Z[-1] - crown_star_z)
    tip_crown_overlap = tip_radius - tip_crown_distance
    ready = (
        shoulder_root_ellipse < 1.0
        and lower_rail_ellipse < 1.0
        and abs(shoulder_seat_centerline_error) < 0.001
        and petal_upper_overlap > 0.0
        and prong_root_overlap > 0.0
        and prong_seat_overlap > 0.0
        and tip_crown_overlap > 0.0
    )
    return {
        "shoulder_root_shank_ellipse_value": shoulder_root_ellipse,
        "lower_rail_shank_ellipse_value": lower_rail_ellipse,
        "shoulder_end_seat_centerline_error_mm": shoulder_seat_centerline_error,
        "petal_upper_rail_overlap_mm": petal_upper_overlap,
        "prong_root_lower_rail_overlap_mm": prong_root_overlap,
        "prong_seat_girdle_overlap_mm": prong_seat_overlap,
        "prong_tip_crown_overlap_mm": tip_crown_overlap,
        "source_preflight_ready": ready,
    }


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError("expected one source fragment, found {}: {!r}".format(count, old[:80]))
    return source.replace(old, new, 1)


def _rewrite_source() -> str:
    source = BASE_SCRIPT
    replacements = (
        (r'C:\MyJewlGenerated\goal0010_component_pilot', r'C:\MyJewlGenerated\goal0011_realism_master'),
        ('"template_id": "goal0010-split-ring-classic-basket-candidate-v1"', '"template_id": "{}"'.format(TEMPLATE_ID)),
        ('"authoring_status": "local_rhinocommon_candidate_pending_visual_review"', '"authoring_status": "owned_rhinocommon_master_pending_visual_review"'),
        ('"shoulder_root_radius": 0.52', '"shoulder_root_radius": 0.56'),
        ('"shoulder_neck_radius": 0.38', '"shoulder_neck_radius": 0.34'),
        ('"shoulder_split_half_gap_root": 0.20', '"shoulder_split_half_gap_root": 0.28'),
        ('"shoulder_contact_z": 0.72', '"shoulder_contact_z": 7.72'),
        ('"basket_lower_radius": 1.30', '"basket_lower_radius": 1.32'),
        ('"basket_lower_z": 9.62', '"basket_lower_z": 9.70'),
        ('"basket_upper_z": 11.34', '"basket_upper_z": 11.30'),
        ('"gallery_wire_radius": 0.17', '"gallery_wire_radius": 0.20'),
        ('"prong_root_radius": 0.32', '"prong_root_radius": 0.30'),
        ('"prong_seat_radius": 0.255', '"prong_seat_radius": 0.235'),
        ('"prong_tip_radius": 0.19', '"prong_tip_radius": 0.17'),
        ('"prong_tip_height": 0.46', '"prong_tip_height": 0.48'),
        ('"surface_continuity": "candidate_integrated_shoulder_sweeps_and_open_basket"', '"surface_continuity": "upper_shank_cathedral_shoulders_into_open_basket_and_inward_seated_prongs"'),
        ('"manufacturing_authority": False,', '"manufacturing_authority": False,\n    "source_ownership": "{}",'.format(SOURCE_OWNERSHIP)),
        (
            '''        front = [
            rg.Point3d(side * 4.86, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.78, -0.34, 1.72),
            rg.Point3d(side * 4.38, -0.48, 4.20),
            rg.Point3d(side * 3.62, -0.56, 7.20),
            rg.Point3d(side * 2.72, -P["shoulder_split_half_gap_neck"], 10.56),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], 0.50, 0.46, 0.42, P["shoulder_neck_radius"]]''',
            '''        front = [
            rg.Point3d(side * 4.82, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.48, -0.36, 8.55),
            rg.Point3d(side * 3.82, -0.46, 9.56),
            rg.Point3d(side * 3.18, -0.56, 10.55),
            rg.Point3d(side * 2.6484, -P["shoulder_split_half_gap_neck"], P["basket_upper_z"]),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], 0.54, 0.48, 0.41, P["shoulder_neck_radius"]]''',
        ),
        (
            '''        supports = [
            rg.Point3d(radial.X * 1.28, radial.Y * 1.28, 9.66),
            rg.Point3d(radial.X * 1.58 + tangent.X * 0.10, radial.Y * 1.58 + tangent.Y * 0.10, 10.05),
            rg.Point3d(radial.X * 2.16 + tangent.X * 0.07, radial.Y * 2.16 + tangent.Y * 0.07, 10.76),
            rg.Point3d(radial.X * 2.55, radial.Y * 2.55, P["basket_upper_z"]),
        ]
        parts.append(("basket_petal_{:02d}".format(index + 1), "metal", tapered_sweep(supports, [0.235, 0.22, 0.20, 0.17])))
        prongs = [
            rg.Point3d(radial.X * 1.46, radial.Y * 1.46, 9.88),
            rg.Point3d(radial.X * 1.94, radial.Y * 1.94, 10.42),
            rg.Point3d(radial.X * 2.52, radial.Y * 2.52, 11.34),
            rg.Point3d(radial.X * 2.96, radial.Y * 2.96, P["stone_girdle_z"] - 0.05),
            rg.Point3d(radial.X * 2.90, radial.Y * 2.90, P["stone_girdle_z"] + P["prong_tip_height"]),
        ]
        parts.append(("prong_{:02d}".format(index + 1), "metal", tapered_sweep(prongs, [P["prong_root_radius"], 0.30, 0.27, P["prong_seat_radius"], P["prong_tip_radius"]])))''',
            '''        supports = [
            rg.Point3d(radial.X * 1.32, radial.Y * 1.32, P["basket_lower_z"]),
            rg.Point3d(radial.X * 1.58 + tangent.X * 0.08, radial.Y * 1.58 + tangent.Y * 0.08, 10.08),
            rg.Point3d(radial.X * 2.14 + tangent.X * 0.05, radial.Y * 2.14 + tangent.Y * 0.05, 10.76),
            rg.Point3d(radial.X * 2.54, radial.Y * 2.54, P["basket_upper_z"]),
        ]
        parts.append(("basket_petal_{:02d}".format(index + 1), "metal", tapered_sweep(supports, [0.25, 0.235, 0.21, 0.18])))
        prongs = [
            rg.Point3d(radial.X * 1.38, radial.Y * 1.38, 9.72),
            rg.Point3d(radial.X * 1.68, radial.Y * 1.68, 10.08),
            rg.Point3d(radial.X * 2.54, radial.Y * 2.54, 11.20),
            rg.Point3d(radial.X * 3.04, radial.Y * 3.04, P["stone_girdle_z"] - 0.14),
            rg.Point3d(radial.X * 2.88, radial.Y * 2.88, P["stone_girdle_z"] + 0.20),
            rg.Point3d(radial.X * 2.68, radial.Y * 2.68, P["stone_girdle_z"] + P["prong_tip_height"]),
        ]
        parts.append(("prong_{:02d}".format(index + 1), "metal", tapered_sweep(prongs, [P["prong_root_radius"], 0.29, 0.27, P["prong_seat_radius"], 0.20, P["prong_tip_radius"]])))''',
        ),
        ('P["prong_tip_radius"] * 1.03', 'P["prong_tip_radius"] * 1.08'),
        (', 1.0, 1.0, 0.68))', ', 1.0, 1.0, 0.72))'),
        ('MyJewl Goal0010 ', 'MyJewl Goal0011 '),
        ('solitaire_component_pilot_v1.mtl', MASTER_FILE_STEM + '.mtl'),
        ('G0010_CANDIDATE_METAL', 'G0011_REALISM_MASTER_METAL'),
        ('G0010_CANDIDATE_STONE', 'G0011_REALISM_MASTER_STONE'),
        ('solitaire_component_pilot_v1.3dm', MASTER_FILE_STEM + '.3dm'),
        ('solitaire_component_pilot_v1.obj', MASTER_FILE_STEM + '.obj'),
        ('myjewl.goal0010.component_pilot_receipt.v1', 'myjewl.goal0011.realism_master_receipt.v1'),
        ('local Rhino 8 RhinoCommon candidate', 'Azure Rhino.Compute 8 /grasshopper from MyJewl-owned RhinoCommon source'),
        ('solitaire_component_pilot_v1_receipt.json', MASTER_FILE_STEM + '_receipt.json'),
        ('goal0010_local_candidate_source_ready', 'goal0011_realism_master_source_ready'),
        ('goal0010_local_candidate_source_failed', 'goal0011_realism_master_source_failed'),
    )
    for old, new in replacements:
        source = _replace_once(source, old, new)
    authority = '"preview_only_not_manufacturing"'
    if source.count(authority) != 3:
        raise RuntimeError("unexpected Goal 0010 authority marker count")
    source = source.replace(authority, '"rhino_master_candidate_not_manufacturing"')
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
    built = build(root / "manual" / "rhino_compute_bridge" / "goal0011_realism_master_v1.ghx")
    print(built)
