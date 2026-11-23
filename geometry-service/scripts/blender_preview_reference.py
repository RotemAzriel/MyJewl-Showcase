#!/usr/bin/env python3
"""Render exported WebGL preview triangles as local Cycles comparison evidence.

The input OBJ files come from ``export_preview_reference.mjs``. This script
does not generate product geometry, call Rhino, certify manufacturing, or use
paid compute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    forwarded = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--resolution", type=int, default=512)
    return parser.parse_args(forwarded)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)


def principled_material(name: str, *, color, metallic: float, roughness: float):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    return material


def diamond_material():
    material = principled_material(
        "Preview diamond — visual approximation", color=(0.92, 0.97, 1.0),
        metallic=0.0, roughness=0.025,
    )
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["IOR"].default_value = 2.417
    transmission = shader.inputs.get("Transmission Weight") or shader.inputs.get("Transmission")
    if transmission:
        transmission.default_value = 0.94
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Clearcoat")
    if coat:
        coat.default_value = 0.08
    return material


def metal_material(preview: dict):
    family = preview.get("metal_family", "yellow")
    finish = preview.get("metal_finish", "polished")
    colors = {
        "yellow": (0.83, 0.57, 0.18),
        "white": (0.72, 0.76, 0.80),
        "rose": (0.77, 0.39, 0.31),
    }
    roughness = {"polished": 0.13, "matte": 0.42, "brushed": 0.26}[finish]
    material = principled_material(
        f"Preview {family} {finish}", color=colors[family], metallic=1.0,
        roughness=roughness,
    )
    shader = material.node_tree.nodes.get("Principled BSDF")
    anisotropic = shader.inputs.get("Anisotropic IOR Level") or shader.inputs.get("Anisotropic")
    if anisotropic and finish == "brushed":
        anisotropic.default_value = 0.72
    return material


def look_at(obj, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area(name: str, location, energy: float, size: float, color) -> None:
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    look_at(obj, Vector((0, 0, 0.25)))


def configure_scene(scene, resolution: int, samples: int, output_path: Path) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(output_path)
    scene.render.film_transparent = False
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.world.color = (0.035, 0.045, 0.055)


def import_preview(path: Path, preview: dict):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=str(path))
    objects = [obj for obj in set(bpy.data.objects) - before if obj.type == "MESH"]
    metal = metal_material(preview)
    diamond = diamond_material()
    for obj in objects:
        lowered = obj.name.lower()
        is_stone = any(token in lowered for token in (
            "stone", "diamond", "faceted-cut", "internal-reflection-shell",
        ))
        obj.data.materials.clear()
        obj.data.materials.append(diamond if is_stone else metal)
        for polygon in obj.data.polygons:
            polygon.use_smooth = not is_stone
    return objects


def bounds(objects):
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def add_studio(objects) -> None:
    low, high = bounds(objects)
    center = (low + high) / 2
    span = max((high - low).length, 1.0)

    bpy.ops.mesh.primitive_plane_add(size=span * 3.8, location=(center.x, center.y, low.z - span * 0.035))
    floor = bpy.context.object
    floor.name = "Neutral contact-shadow floor"
    floor.data.materials.append(principled_material(
        "Neutral floor", color=(0.055, 0.065, 0.075), metallic=0.0, roughness=0.48,
    ))

    camera_data = bpy.data.cameras.new("Fixed comparison camera")
    camera = bpy.data.objects.new("Fixed comparison camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = center + Vector((span * 0.72, -span * 1.38, span * 0.62))
    camera.data.lens = 58
    look_at(camera, center + Vector((0, 0, span * 0.03)))
    bpy.context.scene.camera = camera

    add_area("White key card", center + Vector((-span, -span, span * 1.25)), 950, span * 1.25, (1.0, 0.98, 0.95))
    add_area("Cool fill card", center + Vector((span * 1.25, -span * 0.15, span * 0.55)), 650, span, (0.78, 0.88, 1.0))
    add_area("Rim card", center + Vector((0, span, span * 1.35)), 850, span * 0.8, (1.0, 1.0, 1.0))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not manifest.get("comparison_only") or manifest.get("manufacturing_authority"):
        raise ValueError("Manifest must be explicitly comparison-only")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for case in manifest["cases"]:
        clear_scene()
        objects = import_preview(args.manifest.parent / case["obj"], case["preview"])
        add_studio(objects)
        output_path = args.output_dir / f"cycles_{case['id']}.png"
        configure_scene(bpy.context.scene, args.resolution, args.samples, output_path)
        started = time.monotonic()
        bpy.ops.render.render(write_still=True)
        elapsed = round(time.monotonic() - started, 3)
        results.append({
            "id": case["id"],
            "render": output_path.name,
            "sha256": sha256(output_path),
            "elapsed_seconds": elapsed,
            "cycles_samples": args.samples,
            "resolution": [args.resolution, args.resolution],
            "geometry_signature": case["geometry_signature"],
            "fixed_pose_radians": case["fixed_pose_radians"],
            "authority": "comparison_only_not_manufacturing",
        })
        print(f"[CYCLES] {case['id']} in {elapsed:.1f}s -> {output_path}")

    evidence = {
        "schema": "myjewl.preview_cycles_evidence.v1",
        "blender_version": bpy.app.version_string,
        "render_engine": "CYCLES",
        "device": "CPU",
        "source_manifest": str(args.manifest),
        "comparison_only": True,
        "manufacturing_authority": False,
        "results": results,
    }
    evidence_path = args.output_dir / "cycles_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
