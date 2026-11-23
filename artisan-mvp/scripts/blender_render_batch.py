#!/usr/bin/env python3
"""
blender_render_batch.py — Headless Blender 4.2 render script for MyJewl M3.

Run on RunPod GPU pod via:
  blender -b --python /workspace/scripts/blender_render_batch.py -- \
    --config-name <name> \
    --stl /workspace/stl/model.stl \
    --params /workspace/configs/<name>/parameters.json \
    --hdri-dir /workspace/hdri/ \
    --output-dir /workspace/output/<name>/ \
    --samples 128 \
    [--max-views 1]    # limit views for single-view test

IMPORTANT: This script runs inside Blender's embedded Python interpreter.
           Never import from jewelry_gaas_foundation.
           Allowed imports: bpy, sys, os, math, random, json, argparse, time, pathlib.
"""

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

# bpy is only available inside Blender — imported after arg parse to allow --help outside Blender
import bpy


# ---------------------------------------------------------------------------
# Argument parsing (args after "--" are passed to the script by Blender)
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="MyJewl headless Blender render")
    parser.add_argument("--config-name", required=True)
    parser.add_argument("--stl", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--hdri-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=128)
    parser.add_argument("--max-views", type=int, default=50,
                        help="Stop after N views (1 for single-view test)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint.json if present")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# GPU setup
# ---------------------------------------------------------------------------

def setup_gpu_device(device_type: str = "OPTIX") -> None:
    """Configure Cycles to use GPU rendering. Falls back to CUDA then CPU."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"

    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = device_type

    try:
        prefs.get_devices()
        gpu_found = False
        for device in prefs.devices:
            if device.type != "CPU":
                device.use = True
                gpu_found = True
            else:
                device.use = False  # disable CPU when GPU is available

        if gpu_found:
            scene.cycles.device = "GPU"
            print(f"[GPU] Cycles device: {device_type}, GPU rendering enabled.")
        else:
            print(f"[GPU] No {device_type} devices found, falling back to CPU.")
            scene.cycles.device = "CPU"
    except Exception as exc:
        print(f"[GPU] Device setup error ({exc}), falling back to CPU.")
        scene.cycles.device = "CPU"


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------

def clear_scene() -> None:
    """Remove all default objects from the scene."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)


def import_stl(stl_path: Path) -> bpy.types.Object:
    """Import STL file using Blender 4.2 operator. Returns the imported mesh object."""
    before = set(bpy.data.objects)
    # Blender 4.2: wm.stl_import (replaces import_mesh.stl from Blender 3.x)
    # global_scale=0.001 converts STL mm coordinates to Blender meters
    bpy.ops.wm.stl_import(filepath=str(stl_path), global_scale=0.001)
    new_objs = list(set(bpy.data.objects) - before)
    if not new_objs:
        raise RuntimeError(f"STL import produced no objects: {stl_path}")
    mesh_obj = new_objs[0]
    # Apply smooth shading
    mesh_obj.data.polygons.foreach_set("use_smooth", [True] * len(mesh_obj.data.polygons))
    mesh_obj.data.update()
    print(f"[STL] Imported: {mesh_obj.name} ({len(mesh_obj.data.polygons)} polygons)")
    return mesh_obj


def assign_pass_index(mesh_obj: bpy.types.Object, index: int = 1) -> None:
    """Set object pass index for compositor mask pass.
    M3 limitation: single STL → one object → all pixels share the same index.
    Per-part segmentation requires per-part STL (M4 concern).
    """
    mesh_obj.pass_index = index


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

METAL_CONFIGS = {
    "14k_yellow": {"base_color": (1.000, 0.766, 0.336, 1.0), "roughness": (0.02, 0.35)},
    "18k_yellow": {"base_color": (1.000, 0.766, 0.336, 1.0), "roughness": (0.02, 0.35)},
    "14k_white":  {"base_color": (0.950, 0.950, 0.950, 1.0), "roughness": (0.02, 0.35)},
    "18k_white":  {"base_color": (0.950, 0.950, 0.950, 1.0), "roughness": (0.02, 0.35)},
    "14k_rose":   {"base_color": (1.000, 0.650, 0.550, 1.0), "roughness": (0.02, 0.35)},
    "18k_rose":   {"base_color": (1.000, 0.650, 0.550, 1.0), "roughness": (0.02, 0.35)},
    "pt950":      {"base_color": (0.960, 0.960, 0.960, 1.0), "roughness": (0.02, 0.25)},
    "silver":     {"base_color": (0.972, 0.960, 0.915, 1.0), "roughness": (0.03, 0.35)},
}


def _log_uniform(lo: float, hi: float) -> float:
    return math.exp(random.uniform(math.log(lo), math.log(hi)))


def _make_metal_material(metal_alloy: str) -> bpy.types.Material:
    """Create Principled BSDF metallic material for the ring band."""
    key = metal_alloy.lower().replace(" ", "_").replace("gold", "").strip("_")
    # Normalize common aliases
    for k in METAL_CONFIGS:
        if k in metal_alloy.lower().replace(" ", "_"):
            key = k
            break

    cfg = METAL_CONFIGS.get(key, METAL_CONFIGS["14k_yellow"])
    roughness = _log_uniform(*cfg["roughness"])

    mat = bpy.data.materials.new(name=f"mat_{key}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    bsdf.inputs["Base Color"].default_value = cfg["base_color"]
    bsdf.inputs["Metallic"].default_value = 1.0
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["IOR"].default_value = 1.45
    # "Specular IOR Level" is the Blender 4.x name (was "Specular" in 3.x)
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 1.0

    return mat


def _make_diamond_material() -> bpy.types.Material:
    """Create diamond/glass material. Uses 'Transmission Weight' (Blender 4.0+ name)."""
    mat = bpy.data.materials.new(name="mat_diamond")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.0
    bsdf.inputs["IOR"].default_value = 2.417
    # "Transmission Weight" is the Blender 4.0+ name (was "Transmission" in 3.x)
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 1.0
    elif "Transmission" in bsdf.inputs:
        bsdf.inputs["Transmission"].default_value = 1.0  # fallback for older Blender
    mat.use_backface_culling = False

    return mat


def setup_material(mesh_obj: bpy.types.Object, metal_alloy: str) -> None:
    """Apply metal + diamond materials to the mesh object.
    M3 limitation: single STL uses one material for the entire ring.
    Using metal material for the whole ring (stone material requires per-part STL).
    """
    mesh_obj.data.materials.clear()
    metal_mat = _make_metal_material(metal_alloy)
    mesh_obj.data.materials.append(metal_mat)
    print(f"[MAT] Applied {metal_mat.name} to {mesh_obj.name}")


# ---------------------------------------------------------------------------
# HDRI world environment
# ---------------------------------------------------------------------------

def setup_hdri(hdri_dir: Path) -> None:
    """Pick a random HDRI and set it as the world environment with random rotation."""
    hdr_files = list(hdri_dir.glob("*.hdr")) + list(hdri_dir.glob("*.HDR"))
    if not hdr_files:
        raise FileNotFoundError(f"No .hdr files found in {hdri_dir}")

    hdri_path = random.choice(hdr_files)
    rotation_z = random.uniform(0, 2 * math.pi)

    world = bpy.context.scene.world
    world.use_nodes = True
    tree = world.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    tex_coord  = nodes.new("ShaderNodeTexCoord")
    mapping    = nodes.new("ShaderNodeMapping")
    env_tex    = nodes.new("ShaderNodeTexEnvironment")
    background = nodes.new("ShaderNodeBackground")
    world_out  = nodes.new("ShaderNodeOutputWorld")

    env_tex.image = bpy.data.images.load(str(hdri_path), check_existing=False)
    mapping.inputs["Rotation"].default_value[2] = rotation_z

    links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], env_tex.inputs["Vector"])
    links.new(env_tex.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], world_out.inputs["Surface"])
    background.inputs["Strength"].default_value = 1.0

    print(f"[HDRI] {hdri_path.name} (Z rotation {math.degrees(rotation_z):.1f}°)")


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def position_camera(
    cam_obj: bpy.types.Object,
    azimuth_deg: float,
    elevation_deg: float,
    focal_length_mm: float,
    fstop: float,
    radius_mm: float = 45.0,
    target: tuple = (0.0, 0.0, 4.05),
) -> None:
    """Position camera at spherical coordinates (azimuth, elevation) looking at target."""
    from mathutils import Vector

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)

    # Blender: Z-up, Y-forward. Azimuth 0 = camera at +Y looking toward origin.
    x = radius_mm / 1000.0 * math.cos(el) * math.sin(az)      # convert mm → m
    y = -radius_mm / 1000.0 * math.cos(el) * math.cos(az)
    z = target[2] / 1000.0 + radius_mm / 1000.0 * math.sin(el)

    cam_obj.location = (x, y, z)

    target_m = Vector((target[0] / 1000.0, target[1] / 1000.0, target[2] / 1000.0))
    direction = target_m - Vector((x, y, z))
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()

    cam_data = cam_obj.data
    cam_data.lens = focal_length_mm
    cam_data.lens_unit = "MILLIMETERS"
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = fstop
    cam_data.dof.focus_distance = math.sqrt(
        (x - target[0] / 1000.0) ** 2 +
        (y - target[1] / 1000.0) ** 2 +
        (z - target[2] / 1000.0) ** 2
    )


def add_camera(scene: bpy.types.Scene) -> bpy.types.Object:
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.clip_start = 0.001   # 1mm — ring is ~60mm from camera
    cam_data.clip_end = 10.0      # 10m
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    return cam_obj


def generate_camera_schedule() -> list:
    """
    Returns list of (azimuth_deg, elevation_deg, focal_length_mm, fstop) tuples.
    50 total views: 21 systematic + 1 top + 1 under + 27 random.
    """
    schedule = []

    # 21 systematic: 7 azimuths × 3 elevations
    for az in [0, 30, 60, 90, 120, 150, 180]:
        for el in [0, 30, 60]:
            fl = math.exp(random.uniform(math.log(35), math.log(100)))
            fs = random.uniform(2.8, 16.0)
            schedule.append((az, el, fl, fs))

    # 1 top-down
    schedule.append((0, 90, 50.0, 11.0))

    # 1 under-ring
    schedule.append((0, -30, 50.0, 11.0))

    # 27 random
    for _ in range(27):
        az = random.uniform(0, 359)
        el = random.uniform(-15, 75)
        fl = math.exp(random.uniform(math.log(35), math.log(100)))
        fs = random.uniform(2.8, 16.0)
        schedule.append((az, el, fl, fs))

    return schedule


# ---------------------------------------------------------------------------
# Compositor setup (outputs: RGB + mask + depth EXR + normal EXR)
# ---------------------------------------------------------------------------

def setup_compositor(scene: bpy.types.Scene, output_dir: Path):
    """Configure compositor with File Output nodes for all 4 passes.
    Returns (out_rgb, out_mask, out_depth, out_normal) node references.
    """
    view_layer = scene.view_layers["ViewLayer"]
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = True
    view_layer.use_pass_object_index = True

    scene.use_nodes = True
    scene.render.use_compositing = True
    tree = scene.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    rl = nodes.new("CompositorNodeRLayers")

    def _file_out(label, fmt, color_mode, color_depth, slot_path):
        n = nodes.new("CompositorNodeOutputFile")
        n.label = label
        n.base_path = str(output_dir)
        n.format.file_format = fmt
        n.format.color_mode = color_mode
        n.format.color_depth = color_depth
        n.file_slots[0].path = slot_path
        return n

    out_rgb    = _file_out("RGB",    "PNG",       "RGB", "8",  "view_")
    out_mask   = _file_out("Mask",   "PNG",       "BW",  "8",  "mask_")
    out_depth  = _file_out("Depth",  "OPEN_EXR",  "RGB", "32", "depth_")
    out_normal = _file_out("Normal", "OPEN_EXR",  "RGB", "32", "normal_")

    links.new(rl.outputs["Image"],   out_rgb.inputs[0])
    links.new(rl.outputs["IndexOB"], out_mask.inputs[0])
    links.new(rl.outputs["Depth"],   out_depth.inputs[0])
    links.new(rl.outputs["Normal"],  out_normal.inputs[0])

    return out_rgb, out_mask, out_depth, out_normal


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def configure_render_settings(scene: bpy.types.Scene, samples: int) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.cycles.use_denoising = True


def render_view(
    scene: bpy.types.Scene,
    view_idx: int,
    out_rgb, out_mask, out_depth, out_normal,
) -> float:
    """Render one view at view_idx (1-based). Returns wall-clock seconds."""
    # Set frame_current = view_idx so Blender appends it to output filenames.
    # Output: view_0001.png, mask_0001.png, depth_0001.exr, normal_0001.exr
    scene.frame_current = view_idx
    scene.frame_start = view_idx
    scene.frame_end = view_idx

    t0 = time.monotonic()
    bpy.ops.render.render(write_still=False)
    elapsed = time.monotonic() - t0
    print(f"[RENDER] View {view_idx:04d} completed in {elapsed:.1f}s")
    return elapsed


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint(output_dir: Path, config_name: str) -> set:
    """Return set of completed view indices from checkpoint.json."""
    cp_path = output_dir / "checkpoint.json"
    if cp_path.exists():
        try:
            with open(cp_path) as f:
                data = json.load(f)
            if data.get("config_name") == config_name:
                return set(data.get("completed_views", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_checkpoint(output_dir: Path, config_name: str, completed_views: list) -> None:
    cp_path = output_dir / "checkpoint.json"
    with open(cp_path, "w") as f:
        json.dump({"config_name": config_name, "completed_views": completed_views}, f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    output_dir = Path(args.output_dir)
    renders_dir = output_dir / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== MyJewl M3 Blender Render ===")
    print(f"Config:    {args.config_name}")
    print(f"STL:       {args.stl}")
    print(f"HDRI dir:  {args.hdri_dir}")
    print(f"Output:    {renders_dir}")
    print(f"Samples:   {args.samples}")
    print(f"Max views: {args.max_views}")
    print(f"Blender:   {bpy.app.version_string}")
    print()

    # Load checkpoint if resuming
    completed = load_checkpoint(output_dir, args.config_name) if args.resume else set()
    if completed:
        print(f"[RESUME] {len(completed)} views already completed: {sorted(completed)}")

    # Load parameters for metal type
    with open(args.params) as f:
        params = json.load(f)
    metal_alloy = params.get("metal_alloy", "14k_yellow")
    print(f"[PARAMS] metal_alloy={metal_alloy}")

    # Verify HDRI count
    hdr_files = list(Path(args.hdri_dir).glob("*.hdr"))
    print(f"[HDRI] Found {len(hdr_files)} HDRI files")
    if len(hdr_files) < 10:
        print(f"[HDRI] WARNING: fewer than 10 HDRIs — rendering will use limited variety")

    scene = bpy.context.scene

    # GPU and render settings
    setup_gpu_device("OPTIX")
    configure_render_settings(scene, args.samples)

    # Clear default scene
    clear_scene()

    # Import STL
    mesh_obj = import_stl(args.stl)
    assign_pass_index(mesh_obj, index=1)

    # Add camera
    cam_obj = add_camera(scene)

    # Setup compositor (File Output nodes for all 4 passes)
    out_rgb, out_mask, out_depth, out_normal = setup_compositor(scene, renders_dir)

    # Generate camera schedule
    random.seed(hash(args.config_name))  # deterministic per config
    schedule = generate_camera_schedule()

    # Render loop
    n_views = min(args.max_views, len(schedule))
    completed_list = sorted(completed)
    total_seconds = 0.0

    for view_idx, (az, el, fl, fs) in enumerate(schedule[:n_views], start=1):
        if view_idx in completed:
            print(f"[SKIP] View {view_idx:04d} already completed")
            continue

        print(f"\n[VIEW {view_idx:04d}/{n_views}] az={az:.1f}° el={el:.1f}° fl={fl:.1f}mm f/{fs:.1f}")

        try:
            # Apply fresh HDRI and material per view for randomization
            setup_hdri(Path(args.hdri_dir))
            setup_material(mesh_obj, metal_alloy)

            # Position camera
            position_camera(cam_obj, az, el, fl, fs)

            # Render
            elapsed = render_view(scene, view_idx, out_rgb, out_mask, out_depth, out_normal)
            total_seconds += elapsed
            completed_list.append(view_idx)

        except Exception as exc:
            print(f"[ERROR] View {view_idx} failed: {exc}. Continuing...")
            import traceback
            traceback.print_exc()

        # Save checkpoint after every view
        save_checkpoint(output_dir, args.config_name, completed_list)

    # Summary
    render_count = len(completed_list)
    print(f"\n=== Render Complete ===")
    print(f"Config:          {args.config_name}")
    print(f"Views rendered:  {render_count}/{n_views}")
    print(f"Total time:      {total_seconds:.1f}s")
    if render_count > 0:
        print(f"Time/view:       {total_seconds/render_count:.1f}s")
    print(f"Output:          {renders_dir}")
    print(f"Checkpoint:      {output_dir / 'checkpoint.json'}")


if __name__ == "__main__":
    main()
