#!/usr/bin/env python3
"""Create Goal 0009 GLB LODs and a Cycles product reference from Rhino OBJ.

This script imports only the Azure Rhino.Compute-derived OBJ. It assigns
visual materials, adds render-only studio objects, exports named-part GLBs,
and renders a fixed high-key comparison pose. It never generates jewelry
geometry or changes manufacturing status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    forwarded = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
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


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for block in list(datablocks):
            datablocks.remove(block)


def principled_material(name, color, metallic, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    return material


def gold_material():
    material = principled_material(
        "G0009 14k yellow gold polished — visual calibration",
        (0.78, 0.36, 0.055), 1.0, 0.155,
    )
    shader = material.node_tree.nodes.get("Principled BSDF")
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Clearcoat")
    if coat:
        coat.default_value = 0.18
    coat_rough = shader.inputs.get("Coat Roughness") or shader.inputs.get("Clearcoat Roughness")
    if coat_rough:
        coat_rough.default_value = 0.04
    return material


def diamond_material():
    material = principled_material(
        "G0009 diamond — IOR 2.417 multi-bounce preview",
        (0.96, 0.985, 1.0), 0.0, 0.008,
    )
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["IOR"].default_value = 2.417
    transmission = shader.inputs.get("Transmission Weight") or shader.inputs.get("Transmission")
    if transmission:
        transmission.default_value = 1.0
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Clearcoat")
    if coat:
        coat.default_value = 0.08
    material.diffuse_color = (0.96, 0.985, 1.0, 0.22)
    return material


def neutral_material(name, color, roughness):
    return principled_material(name, color, 0.0, roughness)


def import_master(master_dir: Path):
    source = master_dir / "solitaire_reference_master_v2.obj"
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(
        filepath=str(source), global_scale=0.001,
        forward_axis="NEGATIVE_Y", up_axis="Z",
    )
    objects = [obj for obj in set(bpy.data.objects) - before if obj.type == "MESH"]
    if len(objects) != 20:
        raise RuntimeError("expected 20 named Rhino parts, imported {}".format(len(objects)))
    gold = gold_material()
    diamond = diamond_material()
    for obj in objects:
        is_stone = "stone" in obj.name.lower() or "diamond" in obj.name.lower()
        obj.data.materials.clear()
        obj.data.materials.append(diamond if is_stone else gold)
        for polygon in obj.data.polygons:
            polygon.use_smooth = not is_stone
        obj["goal"] = "0009"
        obj["authority"] = "azure_rhino_compute_preview_only"
        obj["preview_role"] = "stone" if is_stone else "metal"
        if not is_stone:
            bevel = obj.modifiers.new("Microscopic edge highlight", "BEVEL")
            bevel.width = 0.000025
            bevel.segments = 2
            bevel.limit_method = "ANGLE"
    return sorted(objects, key=lambda item: item.name)


def select_only(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def smart_uv(objects):
    for obj in objects:
        select_only([obj])
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.015)
        bpy.ops.object.mode_set(mode="OBJECT")


def export_glb(path: Path, objects):
    select_only(objects)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_materials="EXPORT",
        export_tangents=True,
        export_yup=True,
        export_animations=False,
        export_cameras=False,
        export_lights=False,
    )
    canonicalize_glb_tangents(path)


def canonicalize_glb_tangents(path: Path, decimals: int = 7):
    """Replace unstable exporter tangents with a deterministic valid basis.

    Blender's MikkTSpace result varies between otherwise identical processes.
    These preview materials do not use normal maps, so a normal-derived
    orthogonal basis is sufficient while preserving explicit GLB tangents.
    """
    payload = bytearray(path.read_bytes())
    magic, version, total_length = struct.unpack_from("<III", payload, 0)
    if magic != 0x46546C67 or version != 2 or total_length != len(payload):
        raise RuntimeError("unexpected GLB header")
    json_length, json_type = struct.unpack_from("<II", payload, 12)
    if json_type != 0x4E4F534A:
        raise RuntimeError("GLB JSON chunk absent")
    json_start = 20
    document = json.loads(bytes(payload[json_start:json_start + json_length]).rstrip(b" \x00"))
    bin_header = json_start + json_length
    bin_length, bin_type = struct.unpack_from("<II", payload, bin_header)
    if bin_type != 0x004E4942:
        raise RuntimeError("GLB binary chunk absent")
    bin_start = bin_header + 8
    tangent_normal_pairs = {
        (
            primitive.get("attributes", {}).get("TANGENT"),
            primitive.get("attributes", {}).get("NORMAL"),
        )
        for mesh in document.get("meshes", [])
        for primitive in mesh.get("primitives", [])
        if primitive.get("attributes", {}).get("TANGENT") is not None
    }
    for tangent_index, normal_index in sorted(tangent_normal_pairs):
        tangent_accessor = document["accessors"][tangent_index]
        normal_accessor = document["accessors"][normal_index]
        if tangent_accessor.get("componentType") != 5126 or tangent_accessor.get("type") != "VEC4":
            raise RuntimeError("unexpected tangent accessor encoding")
        if normal_accessor.get("componentType") != 5126 or normal_accessor.get("type") != "VEC3":
            raise RuntimeError("unexpected normal accessor encoding")
        if tangent_accessor["count"] != normal_accessor["count"]:
            raise RuntimeError("normal/tangent count mismatch")
        tangent_view = document["bufferViews"][tangent_accessor["bufferView"]]
        normal_view = document["bufferViews"][normal_accessor["bufferView"]]
        tangent_stride = tangent_view.get("byteStride", 16)
        normal_stride = normal_view.get("byteStride", 12)
        tangent_start = bin_start + tangent_view.get("byteOffset", 0) + tangent_accessor.get("byteOffset", 0)
        normal_start = bin_start + normal_view.get("byteOffset", 0) + normal_accessor.get("byteOffset", 0)
        for index in range(tangent_accessor["count"]):
            nx, ny, nz = struct.unpack_from("<3f", payload, normal_start + index * normal_stride)
            # Cross a fixed reference vector with the normal, changing the
            # reference only near the pole to avoid a degenerate basis.
            if abs(nz) < 0.9:
                tx, ty, tz = -ny, nx, 0.0
            else:
                tx, ty, tz = 0.0, -nz, ny
            length = math.sqrt(tx * tx + ty * ty + tz * tz)
            if length <= 1e-12:
                tx, ty, tz, length = 1.0, 0.0, 0.0, 1.0
            tangent = (
                round(tx / length, decimals),
                round(ty / length, decimals),
                round(tz / length, decimals),
                1.0,
            )
            struct.pack_into("<4f", payload, tangent_start + index * tangent_stride, *tangent)
    path.write_bytes(payload)


def build_lod1(objects):
    duplicates = []
    for source in objects:
        duplicate = source.copy()
        duplicate.data = source.data.copy()
        duplicate.name = source.name + "_lod1"
        bpy.context.collection.objects.link(duplicate)
        if duplicate.get("preview_role") != "stone":
            decimate = duplicate.modifiers.new("LOD1 deterministic reduction", "DECIMATE")
            decimate.ratio = 0.38
            decimate.use_collapse_triangulate = True
            select_only([duplicate])
            bpy.ops.object.modifier_apply(modifier=decimate.name)
        duplicates.append(duplicate)
    return duplicates


def apply_cycles_diamond_glass(objects):
    for obj in objects:
        if obj.get("preview_role") != "stone":
            continue
        material = obj.data.materials[0]
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        glass = nodes.new("ShaderNodeBsdfGlass")
        glass.distribution = "BECKMANN"
        glass.inputs["Color"].default_value = (0.92, 0.97, 1.0, 1.0)
        glass.inputs["Roughness"].default_value = 0.004
        glass.inputs["IOR"].default_value = 2.417
        links.new(glass.outputs["BSDF"], output.inputs["Surface"])


def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area(name, location, energy, size, color, target=(0, 0, 0.002)):
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.shape = "RECTANGLE"
    data.size = size
    data.size_y = size * 0.66
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    look_at(obj, target)


def add_reflection_card(name, location, size, target):
    """Add a camera-invisible dark card for readable metal/diamond contrast."""
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    card = bpy.context.object
    card.name = name
    card.data.materials.append(neutral_material(name + " material", (0.008, 0.009, 0.012), 0.48))
    look_at(card, target)
    if hasattr(card, "visible_camera"):
        card.visible_camera = False
    if hasattr(card, "visible_shadow"):
        card.visible_shadow = False
    return card


def add_studio():
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, -0.01025))
    floor = bpy.context.object
    floor.name = "High-key contact surface — render only"
    floor.data.materials.append(neutral_material("Warm white studio sweep", (0.84, 0.835, 0.82), 0.68))

    # Large cards make controlled continuous highlights across the gold.
    add_area("Key softbox", (-0.042, -0.036, 0.055), 1.35, 0.045, (1.0, 0.91, 0.78))
    add_area("Front fill", (0.040, -0.045, 0.025), 0.72, 0.040, (0.88, 0.94, 1.0))
    add_area("Top strip", (-0.038, 0.012, 0.064), 1.10, 0.030, (1.0, 1.0, 1.0), (0, 0, 0.0115))
    add_area("Rear rim", (0.035, 0.035, 0.040), 1.0, 0.030, (1.0, 0.86, 0.68))
    add_area("Diamond return card", (-0.025, 0.025, 0.025), 0.58, 0.025, (0.78, 0.90, 1.0), (0, 0, 0.0115))
    add_reflection_card("Left contrast flag", (-0.030, 0.010, 0.024), 0.026, (0, 0, 0.0105))
    add_reflection_card("Right contrast flag", (0.034, 0.016, 0.018), 0.022, (0, 0, 0.0105))
    # The table is nearly horizontal, so a controlled overhead dark return is
    # required to reveal the facet boundaries instead of reflecting the top
    # softbox as one featureless white disc.
    add_reflection_card("Diamond table contrast flag", (0.0, 0.002, 0.052), 0.030, (0, 0, 0.0122))
    add_reflection_card(
        "Diamond transmission contrast flag",
        (0.0, 0.011, 0.0122), 0.018, (0.0, -0.050, 0.0122),
    )

    camera_data = bpy.data.cameras.new("G0009 fixed comparison camera")
    camera = bpy.data.objects.new("G0009 fixed comparison camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (0.044, -0.052, 0.029)
    camera.data.lens = 72
    camera.data.sensor_width = 36
    camera.data.clip_start = 0.001
    camera.data.clip_end = 1.0
    look_at(camera, (0, 0, 0.0028))
    camera.data.dof.use_dof = True
    focus = bpy.data.objects.new("G0009 focus target", None)
    bpy.context.collection.objects.link(focus)
    focus.location = (0, 0, 0.0115)
    camera.data.dof.focus_object = focus
    camera.data.dof.aperture_fstop = 22.0
    bpy.context.scene.camera = camera


def configure_cycles(scene, resolution, samples, output_path):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.seed = 9
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
    scene.render.filepath = str(output_path)
    scene.render.film_transparent = False
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -1.95
    world = bpy.data.worlds.new("High-key neutral world") if not scene.world else scene.world
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.94, 0.955, 0.98, 1.0)
    background.inputs["Strength"].default_value = 0.20


def evaluated_bounds(objects):
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    low = [min(point[index] for point in points) for index in range(3)]
    high = [max(point[index] for point in points) for index in range(3)]
    return {"min_m": low, "max_m": high, "extent_m": [high[i] - low[i] for i in range(3)]}


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    clear_scene()
    objects = import_master(args.master_dir)
    smart_uv(objects)
    lod0_path = args.output_dir / "solitaire_reference_master_v2_lod0.glb"
    export_glb(lod0_path, objects)
    lod1 = build_lod1(objects)
    lod1_path = args.output_dir / "solitaire_reference_master_v2_lod1.glb"
    export_glb(lod1_path, lod1)
    for obj in lod1:
        bpy.data.objects.remove(obj, do_unlink=True)
    for obj in objects:
        obj.hide_render = False
        obj.hide_set(False)

    apply_cycles_diamond_glass(objects)
    add_studio()
    render_path = args.output_dir / "cycles_solitaire_reference_{}.png".format(args.resolution)
    configure_cycles(bpy.context.scene, args.resolution, args.samples, render_path)
    started = time.monotonic()
    bpy.ops.render.render(write_still=True)
    elapsed = round(time.monotonic() - started, 3)
    receipt_path = args.master_dir / "solitaire_reference_master_v2_receipt.json"
    evidence = {
        "schema": "myjewl.goal0009.cycles_reference.v2",
        "authority": "comparison_only_not_manufacturing",
        "source": "Azure Rhino.Compute named-part OBJ",
        "source_receipt_sha256": sha256(receipt_path),
        "blender_version": bpy.app.version_string,
        "render_engine": "CYCLES",
        "resolution": [args.resolution, args.resolution],
        "samples": args.samples,
        "elapsed_seconds": elapsed,
        "fixed_camera": {
            "location_m": [0.044, -0.052, 0.029],
            "target_m": [0, 0, 0.0028],
            "lens_mm": 72,
        },
        "evaluated_geometry_bounds": evaluated_bounds(objects),
        "outputs": {
            lod0_path.name: {"sha256": sha256(lod0_path), "byte_size": lod0_path.stat().st_size},
            lod1_path.name: {"sha256": sha256(lod1_path), "byte_size": lod1_path.stat().st_size},
            render_path.name: {"sha256": sha256(render_path), "byte_size": render_path.stat().st_size},
        },
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    evidence_path = args.output_dir / "cycles_reference_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
