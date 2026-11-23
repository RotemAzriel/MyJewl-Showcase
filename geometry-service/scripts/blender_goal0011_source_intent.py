#!/usr/bin/env python3
"""Render a local source-intent preview for the Goal 0011 Rhino candidate.

This script mirrors human-maintained RhinoCommon control paths only to catch
obvious visual defects before a paid solve.  Its Blender geometry is generated
evidence, never product or manufacturing authority.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manual.tools import build_goal0011_realism_master_ghx as MASTER_V1  # noqa: E402
from manual.tools import build_goal0011_realism_master_v2_ghx as MASTER_V2  # noqa: E402
from manual.tools import build_goal0011_realism_master_v3_ghx as MASTER_V3  # noqa: E402
from manual.tools import build_goal0011_realism_master_v4_ghx as MASTER_V4  # noqa: E402

MASTER = MASTER_V1

RENDER_PATH = ROOT / "scripts" / "blender_goal0011_realism_master.py"
SPEC = importlib.util.spec_from_file_location("blender_goal0011_realism_master", RENDER_PATH)
RENDER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RENDER)

MM = 0.001
HEAD_CLOSEUP_VIEWS = {
    "head_front_three_quarter": {
        "location_m": [0.026, -0.032, 0.022],
        "target_m": [0.0, 0.0, 0.0104],
        "lens_mm": 85,
    },
    "head_side_profile": {
        "location_m": [0.036, -0.002, 0.017],
        "target_m": [0.0, 0.0, 0.0105],
        "lens_mm": 90,
    },
    "head_low_three_quarter": {
        "location_m": [0.026, -0.032, 0.0075],
        "target_m": [0.0, 0.0, 0.0104],
        "lens_mm": 85,
    },
}


def parse_args():
    forwarded = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resolution", type=int, default=768)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--revision", choices=("v1", "v2", "v3", "v4"), default="v1")
    parser.add_argument("--head-closeups", action="store_true")
    return parser.parse_args(forwarded)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_tube(name, points_mm, radius_mm, material):
    curve = bpy.data.curves.new(name + " curve", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 16
    curve.bevel_depth = radius_mm * MM
    curve.bevel_resolution = 6
    curve.use_fill_caps = True
    curve.resolution_u = 18
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points_mm) - 1)
    for point, value in zip(spline.bezier_points, points_mm):
        point.co = tuple(axis * MM for axis in value)
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj["authority"] = "local_source_intent_only"
    obj["preview_role"] = "metal"
    return obj


def union_preview_components(base, components):
    """Resolve authored overlaps for a readable, non-authoritative preview."""
    bpy.ops.object.select_all(action="DESELECT")
    base.select_set(True)
    bpy.context.view_layer.objects.active = base
    if base.type != "MESH":
        bpy.ops.object.convert(target="MESH")
    for component in components:
        bpy.ops.object.select_all(action="DESELECT")
        component.select_set(True)
        bpy.context.view_layer.objects.active = component
        if component.type != "MESH":
            bpy.ops.object.convert(target="MESH")
        bpy.ops.object.select_all(action="DESELECT")
        base.select_set(True)
        bpy.context.view_layer.objects.active = base
        modifier = base.modifiers.new("source intent union", type="BOOLEAN")
        modifier.operation = "UNION"
        modifier.solver = "EXACT"
        modifier.object = component
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(component, do_unlink=True)
    base["preview_operation"] = "boolean_union_for_legibility_only"


def add_torus(name, major_radius_mm, minor_radius_mm, z_mm, material, *, rotate_to_y=False, y_scale=1.0):
    bpy.ops.mesh.primitive_torus_add(
        align="WORLD",
        major_segments=192,
        minor_segments=40,
        location=(0, 0, z_mm * MM),
        major_radius=major_radius_mm * MM,
        minor_radius=minor_radius_mm * MM,
    )
    obj = bpy.context.object
    obj.name = name
    if rotate_to_y:
        obj.rotation_euler.x = math.pi / 2.0
        # Bake the X rotation first so the following scale is applied on the
        # ring's world-Y thickness axis, matching the Rhino ellipse contract.
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        obj.scale.y = y_scale
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj["authority"] = "local_source_intent_only"
    obj["preview_role"] = "metal"
    return obj


def add_tip(name, point_mm, radius_mm, material):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=64, ring_count=32,
        radius=radius_mm * MM,
        location=tuple(axis * MM for axis in point_mm),
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale.z = 0.72
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj["authority"] = "local_source_intent_only"
    obj["preview_role"] = "metal"
    return obj


def add_round_brilliant(material):
    radius = 3.25 * MM
    girdle_z = 12.10 * MM
    crown_height = 0.96 * MM
    girdle_thickness = 0.18 * MM
    total_depth = 3.90 * MM
    table_ratio = 0.56
    crown_z = girdle_z + crown_height
    top = girdle_z + girdle_thickness / 2.0
    bottom = girdle_z - girdle_thickness / 2.0
    pavilion_depth = total_depth - crown_height - girdle_thickness
    break_z = bottom - pavilion_depth * 0.58
    culet_z = bottom - pavilion_depth
    vertices = []
    rings = {}

    def add_ring(key, count, ring_radius, z, phase):
        rings[key] = []
        for index in range(count):
            angle = phase + math.tau * index / count
            rings[key].append(len(vertices))
            vertices.append((math.cos(angle) * ring_radius, math.sin(angle) * ring_radius, z))

    add_ring("table", 8, radius * table_ratio, crown_z, math.pi / 8.0)
    add_ring("star", 8, radius * 0.79, girdle_z + crown_height * 0.46, math.pi / 8.0)
    add_ring("girdle_top", 32, radius, top, 0.0)
    add_ring("girdle_bottom", 32, radius, bottom, 0.0)
    add_ring("pavilion_break", 16, radius * 0.57, break_z, math.pi / 16.0)
    culet = len(vertices)
    vertices.append((0, 0, culet_z))
    table_center = len(vertices)
    vertices.append((0, 0, crown_z))
    faces = []
    for index in range(8):
        nxt = (index + 1) % 8
        faces.append((table_center, rings["table"][index], rings["table"][nxt]))
        faces.append((rings["table"][index], rings["star"][index], rings["table"][nxt]))
        faces.append((rings["table"][nxt], rings["star"][index], rings["girdle_top"][(4 * index + 2) % 32], rings["star"][nxt]))
        left = (4 * index) % 32
        faces.append((rings["star"][index], rings["girdle_top"][left], rings["girdle_top"][(left + 1) % 32]))
        faces.append((rings["star"][index], rings["girdle_top"][(left + 1) % 32], rings["girdle_top"][(left + 2) % 32]))
    for index in range(32):
        nxt = (index + 1) % 32
        faces.append((rings["girdle_top"][index], rings["girdle_bottom"][index], rings["girdle_bottom"][nxt], rings["girdle_top"][nxt]))
    for index in range(16):
        nxt = (index + 1) % 16
        left = (2 * index) % 32
        mid = (left + 1) % 32
        right = (left + 2) % 32
        faces.append((rings["girdle_bottom"][left], rings["pavilion_break"][index], rings["girdle_bottom"][mid]))
        faces.append((rings["girdle_bottom"][mid], rings["pavilion_break"][index], rings["girdle_bottom"][right]))
        faces.append((rings["pavilion_break"][index], rings["girdle_bottom"][right], rings["pavilion_break"][nxt]))
        faces.append((rings["pavilion_break"][index], culet, rings["pavilion_break"][nxt]))
    mesh = bpy.data.meshes.new("center_stone_round_brilliant mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("center_stone_round_brilliant", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj["authority"] = "local_source_intent_only"
    obj["preview_role"] = "stone"
    return obj


def build_intent():
    gold = RENDER.gold_material()
    diamond = RENDER.BASE.diamond_material()
    objects = []
    inner_radius = 16.91 / 2.0
    shank = add_torus(
        "shank", inner_radius + 1.65 / 2.0, 1.65 / 2.0, 0.0, gold,
        rotate_to_y=True, y_scale=2.05 / 1.65,
    )
    objects.append(shank)
    shoulders = []
    for side in (-1.0, 1.0):
        suffix = "L" if side < 0 else "R"
        for front_sign, label in ((-1.0, "front"), (1.0, "rear")):
            points = [(side * x, front_sign * y, z) for x, y, z in MASTER.SHOULDER_PATH]
            shoulder = add_tube(
                "shoulder_{}_{}".format(label, suffix), points,
                sum(MASTER.SHOULDER_RADII) / len(MASTER.SHOULDER_RADII), gold,
            )
            shoulders.append(shoulder)
            objects.append(shoulder)
    lower_rail = add_torus(
        "basket_lower_rail", MASTER.BASKET_LOWER_RADIUS,
        MASTER.GALLERY_WIRE_RADIUS, MASTER.BASKET_LOWER_Z, gold,
    )
    objects.append(lower_rail)
    seat_rail = add_torus(
        "stone_seat_rail", MASTER.BASKET_UPPER_RADIUS,
        MASTER.GALLERY_WIRE_RADIUS * 0.84, MASTER.BASKET_UPPER_Z, gold,
    )
    objects.append(seat_rail)
    # The Rhino source keeps named closed parts with intentional overlap. Merge
    # only these preview meshes so internal caps cannot masquerade as gaps.
    connection_mode = getattr(MASTER, "SHOULDER_CONNECTION_RAIL", "upper")
    merge_petals = connection_mode == "petals"
    if connection_mode in {"lower", "upper"}:
        connection_rail = lower_rail if connection_mode == "lower" else seat_rail
        union_preview_components(shank, shoulders + [connection_rail])
        objects = [shank, seat_rail if connection_rail is lower_rail else lower_rail]
    else:
        objects = [shank, lower_rail, seat_rail]
    petal_objects = []
    for index in range(4):
        angle = math.pi / 4.0 + index * math.pi / 2.0
        radial = (math.cos(angle), math.sin(angle))
        tangent = (-math.sin(angle), math.cos(angle))
        supports = []
        for point_index, (radius, z) in enumerate(zip(MASTER.PETAL_RADII, MASTER.PETAL_Z)):
            offsets = getattr(MASTER, "PETAL_TANGENT_OFFSETS", (0.0, 0.08, 0.05, 0.0))
            offset = offsets[point_index]
            supports.append((
                radial[0] * radius + tangent[0] * offset,
                radial[1] * radius + tangent[1] * offset,
                z,
            ))
        petal = add_tube(
            "basket_petal_{:02d}".format(index + 1), supports,
            sum(MASTER.PETAL_WIRE_RADII) / len(MASTER.PETAL_WIRE_RADII), gold,
        )
        petal_objects.append(petal)
        if not merge_petals:
            objects.append(petal)
        prongs = [
            (radial[0] * radius, radial[1] * radius, z)
            for radius, z in zip(MASTER.PRONG_RADII, MASTER.PRONG_Z)
        ]
        objects.append(add_tube(
            "prong_{:02d}".format(index + 1), prongs,
            sum(MASTER.PRONG_WIRE_RADII) / len(MASTER.PRONG_WIRE_RADII), gold,
        ))
        objects.append(add_tip("prong_tip_{:02d}".format(index + 1), prongs[-1], 0.17 * 1.08, gold))
    if merge_petals:
        union_preview_components(shank, shoulders + petal_objects)
    objects.append(add_round_brilliant(diamond))
    return objects


def main():
    global MASTER
    args = parse_args()
    MASTER = {
        "v1": MASTER_V1,
        "v2": MASTER_V2,
        "v3": MASTER_V3,
        "v4": MASTER_V4,
    }[args.revision]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    RENDER.BASE.clear_scene()
    objects = build_intent()
    RENDER.BASE.apply_cycles_diamond_glass(objects)
    camera = RENDER.add_studio()
    RENDER.configure_cycles(bpy.context.scene, args.resolution, args.samples)
    if args.head_closeups:
        RENDER.VIEWS = HEAD_CLOSEUP_VIEWS
        # Structural review needs every shoulder/collar interface legible;
        # macro depth of field can falsely read an overlapping joint as a gap.
        camera.data.dof.use_dof = False
    outputs, elapsed = RENDER.render_views(camera, args.output_dir, args.resolution)
    builder_path = Path(MASTER.__file__).resolve()
    evidence = {
        "schema": "myjewl.goal0011.local_source_intent.{}".format(args.revision),
        "authority": "generated_preview_only_not_rhino_geometry",
        "source": "mirror of MyJewl-owned RhinoCommon control paths",
        "template_id": MASTER.TEMPLATE_ID,
        "builder_source": str(builder_path.relative_to(ROOT)),
        "builder_source_sha256": sha256(builder_path),
        "blender_version": bpy.app.version_string,
        "resolution": [args.resolution, args.resolution],
        "samples": args.samples,
        "view_set": "head_closeups" if args.head_closeups else "whole_ring_fixed",
        "depth_of_field": "disabled_for_structural_review" if args.head_closeups else "studio_default",
        "elapsed_seconds": elapsed,
        "views": outputs,
        "object_count": len(objects),
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    evidence_path = args.output_dir / "local_source_intent_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
