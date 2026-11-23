#!/usr/bin/env python3
"""Render three fixed Goal 0011 views from the Azure Rhino named-part OBJ.

This script assigns comparison-only materials and lighting.  It consumes
Rhino-derived geometry and never authors or modifies product geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "scripts" / "blender_goal0009_reference.py"
SPEC = importlib.util.spec_from_file_location("blender_goal0009_reference", BASE_PATH)
BASE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BASE)

MASTER_STEMS = {
    "v1": "solitaire_realism_master_v1",
    "v2": "solitaire_realism_master_v2",
    "v4": "solitaire_realism_master_v4",
    # Goal 0014: the parametric master emits the same 20 named parts, so the same
    # fixed-pose comparison rig renders it for the extremes visual review.
    "v5": "solitaire_parametric_master_v5",
}
VIEWS = {
    "front_three_quarter": {
        "location_m": [0.044, -0.052, 0.030],
        "target_m": [0.0, 0.0, 0.0038],
        "lens_mm": 78,
    },
    "side_profile": {
        "location_m": [0.064, -0.004, 0.025],
        "target_m": [0.0, 0.0, 0.0040],
        "lens_mm": 82,
    },
    "top_stone_seating": {
        "location_m": [0.009, -0.012, 0.067],
        "target_m": [0.0, 0.0, 0.0106],
        "lens_mm": 86,
    },
}


def parse_args():
    forwarded = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--revision", choices=tuple(MASTER_STEMS), default="v1")
    return parser.parse_args(forwarded)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gold_material():
    material = BASE.principled_material(
        "G0011 controlled 14k yellow gold — comparison only",
        (0.62, 0.255, 0.035), 1.0, 0.19,
    )
    shader = material.node_tree.nodes.get("Principled BSDF")
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Clearcoat")
    if coat:
        coat.default_value = 0.10
    coat_roughness = shader.inputs.get("Coat Roughness") or shader.inputs.get("Clearcoat Roughness")
    if coat_roughness:
        coat_roughness.default_value = 0.075
    return material


def import_master(master_dir: Path, master_stem: str):
    source = master_dir / (master_stem + ".obj")
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(
        filepath=str(source), global_scale=0.001,
        forward_axis="NEGATIVE_Y", up_axis="Z",
    )
    objects = [obj for obj in set(bpy.data.objects) - before if obj.type == "MESH"]
    if len(objects) != 20:
        raise RuntimeError("expected 20 named Azure Rhino parts, imported {}".format(len(objects)))
    gold = gold_material()
    diamond = BASE.diamond_material()
    for obj in objects:
        is_stone = "stone" in obj.name.lower() or "diamond" in obj.name.lower()
        obj.data.materials.clear()
        obj.data.materials.append(diamond if is_stone else gold)
        for polygon in obj.data.polygons:
            polygon.use_smooth = not is_stone
        obj["goal"] = "0011"
        obj["authority"] = "azure_rhino_compute_comparison_only"
        obj["preview_role"] = "stone" if is_stone else "metal"
        if not is_stone:
            bevel = obj.modifiers.new("Microscopic edge highlight", "BEVEL")
            bevel.width = 0.000018
            bevel.segments = 2
            bevel.limit_method = "ANGLE"
    return sorted(objects, key=lambda item: item.name)


def add_studio():
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, -0.01028))
    floor = bpy.context.object
    floor.name = "G0011 neutral contact surface — render only"
    floor.data.materials.append(BASE.neutral_material(
        "G0011 neutral sweep", (0.16, 0.18, 0.22), 0.78,
    ))

    # Neutral cards and deliberately modest energy preserve the gold form
    # instead of clipping it into the warm white background.
    BASE.add_area("G0011 neutral key", (-0.046, -0.036, 0.052), 0.82, 0.050, (1.0, 0.97, 0.93))
    BASE.add_area("G0011 cool fill", (0.043, -0.040, 0.027), 0.34, 0.042, (0.88, 0.94, 1.0))
    BASE.add_area("G0011 top strip", (-0.025, 0.012, 0.061), 0.58, 0.032, (1.0, 1.0, 1.0), (0, 0, 0.0115))
    BASE.add_area("G0011 rear edge", (0.033, 0.037, 0.039), 0.30, 0.030, (1.0, 0.94, 0.86))
    BASE.add_area("G0011 diamond return", (-0.025, 0.023, 0.026), 0.30, 0.027, (0.82, 0.91, 1.0), (0, 0, 0.0118))
    BASE.add_reflection_card("G0011 left contrast", (-0.031, 0.008, 0.025), 0.026, (0, 0, 0.0106))
    BASE.add_reflection_card("G0011 right contrast", (0.034, 0.015, 0.020), 0.023, (0, 0, 0.0106))
    BASE.add_reflection_card("G0011 table contrast", (0.0, 0.003, 0.050), 0.027, (0, 0, 0.0122))

    camera_data = bpy.data.cameras.new("G0011 fixed review camera")
    camera = bpy.data.objects.new("G0011 fixed review camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.data.sensor_width = 36
    camera.data.clip_start = 0.001
    camera.data.clip_end = 1.0
    camera.data.dof.use_dof = True
    focus = bpy.data.objects.new("G0011 focus target", None)
    bpy.context.collection.objects.link(focus)
    focus.location = (0, 0, 0.0112)
    camera.data.dof.focus_object = focus
    camera.data.dof.aperture_fstop = 24.0
    bpy.context.scene.camera = camera
    return camera


def configure_cycles(scene, resolution: int, samples: int):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.seed = 11
    scene.cycles.use_animated_seed = False
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 24
    scene.cycles.transmission_bounces = 16
    scene.cycles.glossy_bounces = 12
    scene.cycles.transparent_max_bounces = 16
    scene.cycles.caustics_reflective = True
    scene.cycles.caustics_refractive = True
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.75
    world = bpy.data.worlds.new("G0011 controlled neutral world") if not scene.world else scene.world
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.045, 0.055, 0.075, 1.0)
    background.inputs["Strength"].default_value = 0.18


def render_views(camera, output_dir: Path, resolution: int) -> tuple[dict, float]:
    outputs = {}
    total_elapsed = 0.0
    for name, view in VIEWS.items():
        camera.location = view["location_m"]
        camera.data.lens = view["lens_mm"]
        BASE.look_at(camera, view["target_m"])
        render_path = output_dir / (name + "_{}.png".format(resolution))
        bpy.context.scene.render.filepath = str(render_path)
        started = time.monotonic()
        bpy.ops.render.render(write_still=True)
        elapsed = round(time.monotonic() - started, 3)
        total_elapsed += elapsed
        outputs[name] = {
            "file": render_path.name,
            "sha256": sha256(render_path),
            "byte_size": render_path.stat().st_size,
            "elapsed_seconds": elapsed,
            "camera": view,
        }
    return outputs, round(total_elapsed, 3)


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    BASE.clear_scene()
    master_stem = MASTER_STEMS[args.revision]
    objects = import_master(args.master_dir, master_stem)
    BASE.apply_cycles_diamond_glass(objects)
    camera = add_studio()
    configure_cycles(bpy.context.scene, args.resolution, args.samples)
    outputs, elapsed = render_views(camera, args.output_dir, args.resolution)
    receipt_path = args.master_dir / (master_stem + "_receipt.json")
    evidence = {
        "schema": "myjewl.goal0011.cycles_fixed_views.v1",
        "authority": "comparison_only_not_manufacturing",
        "source": "Azure Rhino.Compute named-part OBJ",
        "source_revision": args.revision,
        "source_master_stem": master_stem,
        "source_receipt_sha256": sha256(receipt_path),
        "blender_version": bpy.app.version_string,
        "render_engine": "CYCLES",
        "resolution": [args.resolution, args.resolution],
        "samples": args.samples,
        "elapsed_seconds": elapsed,
        "evaluated_geometry_bounds": BASE.evaluated_bounds(objects),
        "views": outputs,
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    evidence_path = args.output_dir / "cycles_fixed_views_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
