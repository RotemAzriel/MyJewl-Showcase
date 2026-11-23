#!/usr/bin/env python3
"""Render the Azure Goal 0010 candidate as non-certifying comparison evidence."""

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


def parse_args():
    forwarded = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--master-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--samples", type=int, default=128)
    return parser.parse_args(forwarded)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_candidate(master_dir: Path):
    source = master_dir / "solitaire_component_pilot_v1.obj"
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=str(source), global_scale=0.001, forward_axis="NEGATIVE_Y", up_axis="Z")
    objects = [obj for obj in set(bpy.data.objects) - before if obj.type == "MESH"]
    if len(objects) != 20:
        raise RuntimeError("expected 20 named Azure candidate parts, imported {}".format(len(objects)))
    gold = BASE.gold_material()
    diamond = BASE.diamond_material()
    for obj in objects:
        is_stone = "stone" in obj.name.lower() or "diamond" in obj.name.lower()
        obj.data.materials.clear()
        obj.data.materials.append(diamond if is_stone else gold)
        for polygon in obj.data.polygons:
            polygon.use_smooth = not is_stone
        obj["goal"] = "0010"
        obj["authority"] = "azure_rhino_compute_preview_only"
        obj["preview_role"] = "stone" if is_stone else "metal"
        if not is_stone:
            bevel = obj.modifiers.new("Microscopic edge highlight", "BEVEL")
            bevel.width = 0.000025
            bevel.segments = 2
            bevel.limit_method = "ANGLE"
    return sorted(objects, key=lambda item: item.name)


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    BASE.clear_scene()
    objects = import_candidate(args.master_dir)
    BASE.smart_uv(objects)
    lod0_path = args.output_dir / "solitaire_component_pilot_v1_lod0.glb"
    BASE.export_glb(lod0_path, objects)
    lod1 = BASE.build_lod1(objects)
    lod1_path = args.output_dir / "solitaire_component_pilot_v1_lod1.glb"
    BASE.export_glb(lod1_path, lod1)
    for obj in lod1:
        bpy.data.objects.remove(obj, do_unlink=True)
    for obj in objects:
        obj.hide_render = False
        obj.hide_set(False)
    BASE.apply_cycles_diamond_glass(objects)
    BASE.add_studio()
    render_path = args.output_dir / "cycles_component_pilot_{}.png".format(args.resolution)
    BASE.configure_cycles(bpy.context.scene, args.resolution, args.samples, render_path)
    started = time.monotonic()
    bpy.ops.render.render(write_still=True)
    elapsed = round(time.monotonic() - started, 3)
    receipt_path = args.master_dir / "solitaire_component_pilot_v1_receipt.json"
    evidence = {
        "schema": "myjewl.goal0010.cycles_component_pilot.v1",
        "authority": "comparison_only_not_manufacturing",
        "source": "Azure Rhino.Compute named-part OBJ",
        "source_receipt_sha256": sha256(receipt_path),
        "blender_version": bpy.app.version_string,
        "render_engine": "CYCLES",
        "resolution": [args.resolution, args.resolution],
        "samples": args.samples,
        "elapsed_seconds": elapsed,
        "fixed_camera": {"location_m": [0.044, -0.052, 0.029], "target_m": [0, 0, 0.0028], "lens_mm": 72},
        "evaluated_geometry_bounds": BASE.evaluated_bounds(objects),
        "outputs": {
            lod0_path.name: {"sha256": sha256(lod0_path), "byte_size": lod0_path.stat().st_size},
            lod1_path.name: {"sha256": sha256(lod1_path), "byte_size": lod1_path.stat().st_size},
            render_path.name: {"sha256": sha256(render_path), "byte_size": render_path.stat().st_size},
        },
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    evidence_path = args.output_dir / "cycles_component_pilot_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
