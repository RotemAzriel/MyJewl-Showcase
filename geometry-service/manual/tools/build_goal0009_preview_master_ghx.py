#!/usr/bin/env python3
"""Build the Goal 0009 preview-master definition for Azure Rhino.Compute 8.

The generated GHX exposes the repository's stable Resthopper contract and
executes only RhinoCommon geometry on the Azure worker. The definition writes
a named-part 3DM, OBJ/MTL interchange mesh, per-part STL files, and a hashed
receipt to a fixed VM output root. It is visualization authority only.
"""

from __future__ import annotations

from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
except ModuleNotFoundError:
    from build_ghx import build_python3_ghx


SCRIPT = r'''import hashlib
import json
import math
import os
import re
import struct
import traceback

import Rhino
import Rhino.Geometry as rg


OUTPUT_ROOT = r"C:\MyJewlGenerated\goal0009_preview_master"
TOLERANCE = 0.001
ANGLE_TOLERANCE = math.radians(0.25)
P = {
    "template_id": "goal0009-split-ring-classic-basket-v2",
    "reference_band_type": "split_ring_solitaire",
    "reference_head_type": "classic_basket",
    "units": "millimeters",
    "ring_size_us": 6.5,
    "inner_diameter": 16.91,
    "shank_width": 2.05,
    "shank_radial_thickness": 1.65,
    "shoulder_root_radius": 0.58,
    "shoulder_neck_radius": 0.40,
    "shoulder_split_half_gap_root": 0.18,
    "shoulder_split_half_gap_neck": 0.64,
    "shoulder_rise": 2.12,
    "stone_diameter": 6.50,
    "stone_total_depth": 3.90,
    "stone_table_ratio": 0.56,
    "stone_crown_height": 0.96,
    "stone_girdle_thickness": 0.18,
    "stone_girdle_z": 12.10,
    "basket_lower_radius": 1.32,
    "basket_upper_radius": 2.86,
    "basket_lower_z": 9.68,
    "basket_upper_z": 11.34,
    "gallery_wire_radius": 0.18,
    "prong_root_radius": 0.34,
    "prong_seat_radius": 0.285,
    "prong_tip_radius": 0.225,
    "prong_tip_height": 0.57,
    "pavilion_clearance": 0.58,
    "surface_continuity": "visual_G1_sweeps_and_overlapped_junctions",
    "manufacturing_authority": False,
}


def curve(points, degree=3):
    return rg.Curve.CreateInterpolatedCurve(points, degree)


def pipe(curve_value, radius, cap=rg.PipeCapMode.Round):
    pieces = rg.Brep.CreatePipe(
        curve_value, radius, False, cap, True, TOLERANCE, ANGLE_TOLERANCE,
    )
    if not pieces:
        raise RuntimeError("pipe construction failed")
    if len(pieces) == 1:
        return pieces[0]
    joined = rg.Brep.JoinBreps(pieces, TOLERANCE)
    if not joined:
        raise RuntimeError("pipe join failed")
    return joined[0]


def tapered_sweep(points, radii):
    rail = curve(points)
    sections = []
    for index, radius in enumerate(radii):
        fraction = index / float(len(radii) - 1)
        parameter = rail.Domain.ParameterAt(fraction)
        frame_result = rail.PerpendicularFrameAt(parameter)
        ok = frame_result[0]
        plane = frame_result[1]
        if not ok:
            plane = rg.Plane(rail.PointAt(parameter), rail.TangentAt(parameter))
        sections.append(rg.Circle(plane, radius).ToNurbsCurve())
    lofts = rg.Brep.CreateFromLoft(
        sections, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False,
    )
    if not lofts:
        raise RuntimeError("tapered sweep loft failed")
    capped = lofts[0].CapPlanarHoles(TOLERANCE)
    return capped or lofts[0]


def shank():
    inner_radius = P["inner_diameter"] / 2.0
    section_center = inner_radius + P["shank_radial_thickness"] / 2.0
    plane = rg.Plane(
        rg.Point3d(section_center, 0, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis,
    )
    section = rg.Ellipse(
        plane, P["shank_radial_thickness"] / 2.0, P["shank_width"] / 2.0,
    ).ToNurbsCurve()
    axis = rg.Line(rg.Point3d(0, -20, 0), rg.Point3d(0, 20, 0))
    revolved = rg.RevSurface.Create(section, axis, 0, math.pi * 2.0)
    if not revolved:
        raise RuntimeError("shank revolve failed")
    return revolved.ToBrep()


def circle_pipe(radius, z, wire_radius):
    plane = rg.Plane(rg.Point3d(0, 0, z), rg.Vector3d.ZAxis)
    return pipe(rg.Circle(plane, radius).ToNurbsCurve(), wire_radius)


def round_brilliant():
    mesh = rg.Mesh()
    radius = P["stone_diameter"] / 2.0
    crown_z = P["stone_girdle_z"] + P["stone_crown_height"]
    girdle_top = P["stone_girdle_z"] + P["stone_girdle_thickness"] / 2.0
    girdle_bottom = P["stone_girdle_z"] - P["stone_girdle_thickness"] / 2.0
    pavilion_depth = P["stone_total_depth"] - P["stone_crown_height"] - P["stone_girdle_thickness"]
    pavilion_break_z = girdle_bottom - pavilion_depth * 0.58
    culet_z = girdle_bottom - pavilion_depth
    rings = {}

    def add_ring(key, count, ring_radius, z, phase):
        indices = []
        for index in range(count):
            angle = phase + math.pi * 2.0 * index / count
            indices.append(mesh.Vertices.Add(
                math.cos(angle) * ring_radius,
                math.sin(angle) * ring_radius,
                z,
            ))
        rings[key] = indices

    add_ring("table", 8, radius * P["stone_table_ratio"], crown_z, math.pi / 8.0)
    add_ring("star", 8, radius * 0.79, P["stone_girdle_z"] + P["stone_crown_height"] * 0.46, math.pi / 8.0)
    # A 32-sided girdle avoids the visibly polygonal outline of the first
    # preview while retaining explicit, flat round-brilliant facet regions.
    add_ring("girdle_top", 32, radius, girdle_top, 0.0)
    add_ring("girdle_bottom", 32, radius, girdle_bottom, 0.0)
    add_ring("pavilion_break", 16, radius * 0.57, pavilion_break_z, math.pi / 16.0)
    culet = mesh.Vertices.Add(0, 0, culet_z)
    table_center = mesh.Vertices.Add(0, 0, crown_z)

    for index in range(8):
        nxt = (index + 1) % 8
        mesh.Faces.AddFace(table_center, rings["table"][index], rings["table"][nxt])
        mesh.Faces.AddFace(rings["table"][index], rings["star"][index], rings["table"][nxt])
        mesh.Faces.AddFace(
            rings["table"][nxt], rings["star"][index],
            rings["girdle_top"][(4 * index + 2) % 32], rings["star"][nxt],
        )
        left = (4 * index) % 32
        mid_left = (4 * index + 1) % 32
        mid = (4 * index + 2) % 32
        mid_right = (4 * index + 3) % 32
        right = (4 * index + 4) % 32
        mesh.Faces.AddFace(rings["star"][index], rings["girdle_top"][left], rings["girdle_top"][mid_left])
        mesh.Faces.AddFace(rings["star"][index], rings["girdle_top"][mid_left], rings["girdle_top"][mid])
        mesh.Faces.AddFace(rings["star"][nxt], rings["girdle_top"][mid], rings["girdle_top"][mid_right])
        mesh.Faces.AddFace(rings["star"][nxt], rings["girdle_top"][mid_right], rings["girdle_top"][right])

    for index in range(32):
        nxt = (index + 1) % 32
        mesh.Faces.AddFace(
            rings["girdle_top"][index], rings["girdle_bottom"][index],
            rings["girdle_bottom"][nxt], rings["girdle_top"][nxt],
        )

    for index in range(16):
        nxt = (index + 1) % 16
        left = (2 * index) % 32
        mid = (2 * index + 1) % 32
        right = (2 * index + 2) % 32
        mesh.Faces.AddFace(rings["girdle_bottom"][left], rings["pavilion_break"][index], rings["girdle_bottom"][mid])
        mesh.Faces.AddFace(rings["girdle_bottom"][mid], rings["pavilion_break"][index], rings["girdle_bottom"][right])
        mesh.Faces.AddFace(
            rings["pavilion_break"][index],
            rings["girdle_bottom"][right],
            rings["pavilion_break"][nxt],
        )
        mesh.Faces.AddFace(rings["pavilion_break"][index], culet, rings["pavilion_break"][nxt])
    mesh.UnifyNormals()
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def build_parts():
    parts = [("shank", "metal", shank())]
    centerline = P["inner_diameter"] / 2.0 + P["shank_radial_thickness"] / 2.0
    shoulder_z = math.sqrt(centerline ** 2 - 4.95 ** 2)
    for side in (-1.0, 1.0):
        suffix = "L" if side < 0 else "R"
        front = [
            rg.Point3d(side * 4.95, -P["shoulder_split_half_gap_root"], shoulder_z),
            rg.Point3d(side * 4.20, -0.32, 8.64),
            rg.Point3d(side * 3.42, -0.50, 9.62),
            rg.Point3d(side * 2.72, -P["shoulder_split_half_gap_neck"], 10.56),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], 0.52, 0.46, P["shoulder_neck_radius"]]
        parts.append(("shoulder_front_" + suffix, "metal", tapered_sweep(front, radii)))
        parts.append(("shoulder_rear_" + suffix, "metal", tapered_sweep(rear, radii)))

    parts.append((
        "basket_lower_rail", "metal",
        circle_pipe(P["basket_lower_radius"], P["basket_lower_z"], P["gallery_wire_radius"]),
    ))
    parts.append((
        "stone_seat_rail", "metal",
        circle_pipe(P["basket_upper_radius"], P["basket_upper_z"], P["gallery_wire_radius"] * 0.92),
    ))

    for index in range(4):
        angle = math.pi / 4.0 + index * math.pi / 2.0
        radial = rg.Vector3d(math.cos(angle), math.sin(angle), 0)
        tangent = rg.Vector3d(-math.sin(angle), math.cos(angle), 0)
        supports = [
            rg.Point3d(radial.X * P["basket_lower_radius"], radial.Y * P["basket_lower_radius"], P["basket_lower_z"]),
            rg.Point3d(radial.X * 1.62 + tangent.X * 0.13, radial.Y * 1.62 + tangent.Y * 0.13, 10.06),
            rg.Point3d(radial.X * 2.08 + tangent.X * 0.08, radial.Y * 2.08 + tangent.Y * 0.08, 10.71),
            rg.Point3d(radial.X * 2.56, radial.Y * 2.56, P["basket_upper_z"]),
        ]
        parts.append((
            "basket_petal_{:02d}".format(index + 1), "metal",
            tapered_sweep(supports, [0.255, 0.235, 0.215, 0.19]),
        ))
        prongs = [
            rg.Point3d(radial.X * 2.34, radial.Y * 2.34, P["basket_lower_z"] + 0.08),
            rg.Point3d(radial.X * 2.72, radial.Y * 2.72, 10.72),
            rg.Point3d(radial.X * 3.18, radial.Y * 3.18, P["stone_girdle_z"] - 0.08),
            rg.Point3d(radial.X * 3.02, radial.Y * 3.02, P["stone_girdle_z"] + P["prong_tip_height"]),
        ]
        parts.append((
            "prong_{:02d}".format(index + 1), "metal",
            tapered_sweep(prongs, [P["prong_root_radius"], 0.35, P["prong_seat_radius"], P["prong_tip_radius"]]),
        ))
        tip = rg.Sphere(prongs[-1], P["prong_tip_radius"] * 1.04).ToBrep()
        tip_plane = rg.Plane(prongs[-1], rg.Vector3d.XAxis, rg.Vector3d.YAxis)
        tip.Transform(rg.Transform.Scale(tip_plane, 1.0, 1.0, 0.72))
        parts.append(("prong_tip_{:02d}".format(index + 1), "metal", tip))

    parts.append(("center_stone_round_brilliant", "stone", round_brilliant()))
    return parts


def mesh_geometry(geometry):
    if isinstance(geometry, rg.Mesh):
        meshes = [geometry.DuplicateMesh()]
    else:
        meshes = list(rg.Mesh.CreateFromBrep(geometry, rg.MeshingParameters.QualityRenderMesh) or [])
    if not meshes:
        raise RuntimeError("meshing failed")
    joined = rg.Mesh()
    for item in meshes:
        joined.Append(item)
    joined.Faces.ConvertQuadsToTriangles()
    joined.UnifyNormals()
    joined.Normals.ComputeNormals()
    joined.Compact()
    return joined


def write_binary_stl(path, mesh, name):
    header = ("MyJewl Goal0009 " + name).encode("ascii", "replace")[:80].ljust(80, b"\0")
    with open(path, "wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", mesh.Faces.Count))
        for face in mesh.Faces:
            indices = (face.A, face.B, face.C)
            a = mesh.Vertices[indices[0]]
            b = mesh.Vertices[indices[1]]
            c = mesh.Vertices[indices[2]]
            normal = rg.Vector3d.CrossProduct(rg.Vector3d(b - a), rg.Vector3d(c - a))
            normal.Unitize()
            stream.write(struct.pack("<3f", normal.X, normal.Y, normal.Z))
            for vertex in (a, b, c):
                stream.write(struct.pack("<3f", vertex.X, vertex.Y, vertex.Z))
            stream.write(struct.pack("<H", 0))


def write_obj(path, meshes):
    mtl_name = "solitaire_reference_master_v2.mtl"
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("mtllib " + mtl_name + "\n")
        vertex_offset = 1
        for name, role, mesh in meshes:
            stream.write("o " + name + "\nusemtl " + ("diamond_preview" if role == "stone" else "yellow_gold_preview") + "\n")
            for vertex in mesh.Vertices:
                stream.write("v {:.9f} {:.9f} {:.9f}\n".format(vertex.X, vertex.Y, vertex.Z))
            for normal in mesh.Normals:
                stream.write("vn {:.9f} {:.9f} {:.9f}\n".format(normal.X, normal.Y, normal.Z))
            for face in mesh.Faces:
                ids = [face.A, face.B, face.C]
                stream.write("f " + " ".join(
                    "{}//{}".format(vertex_offset + item, vertex_offset + item) for item in ids
                ) + "\n")
            vertex_offset += mesh.Vertices.Count
    with open(os.path.join(os.path.dirname(path), mtl_name), "w", encoding="utf-8", newline="\n") as stream:
        stream.write("newmtl yellow_gold_preview\nKd 0.83 0.57 0.20\nKs 1 1 1\nNs 900\n\n")
        stream.write("newmtl diamond_preview\nKd 0.92 0.97 1.0\nKs 1 1 1\nNs 1000\nd 0.28\nNi 2.417\n")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_3dm(path, parts):
    model = Rhino.FileIO.File3dm()
    model.Settings.ModelUnitSystem = Rhino.UnitSystem.Millimeters
    metal_layer = Rhino.DocObjects.Layer()
    metal_layer.Name = "G0009_METAL"
    model.Layers.Add(metal_layer)
    metal_index = model.Layers.Count - 1
    stone_layer = Rhino.DocObjects.Layer()
    stone_layer.Name = "G0009_STONE"
    model.Layers.Add(stone_layer)
    stone_index = model.Layers.Count - 1
    for name, role, geometry in parts:
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.Name = name
        attrs.LayerIndex = stone_index if role == "stone" else metal_index
        attrs.SetUserString("authority", "preview_only_not_manufacturing")
        attrs.SetUserString("template_id", P["template_id"])
        if isinstance(geometry, rg.Mesh):
            model.Objects.AddMesh(geometry, attrs)
        else:
            model.Objects.AddBrep(geometry, attrs)
    if not model.Write(path, 8):
        raise RuntimeError("3DM write failed")


def run():
    request = json.loads(str(json_string)) if json_string else {}
    requested_template = str(request.get("template_id", P["template_id"]))
    if requested_template != P["template_id"]:
        raise ValueError("unsupported template_id")
    safe_job = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id or "goal0009-reference"))[:80]
    output_dir = os.path.join(OUTPUT_ROOT, safe_job)
    os.makedirs(output_dir, exist_ok=True)
    parts = build_parts()
    meshes = [(name, role, mesh_geometry(geometry)) for name, role, geometry in parts]
    master_3dm = os.path.join(output_dir, "solitaire_reference_master_v2.3dm")
    master_obj = os.path.join(output_dir, "solitaire_reference_master_v2.obj")
    write_3dm(master_3dm, parts)
    write_obj(master_obj, meshes)
    files = []
    for name, role, mesh in meshes:
        stl_path = os.path.join(output_dir, name + ".stl")
        write_binary_stl(stl_path, mesh, name)
        box = mesh.GetBoundingBox(True)
        files.append({
            "name": name,
            "role": role,
            "file": os.path.basename(stl_path),
            "sha256": sha256(stl_path),
            "byte_size": os.path.getsize(stl_path),
            "vertices": mesh.Vertices.Count,
            "triangles": mesh.Faces.Count,
            "closed": bool(mesh.IsClosed),
            "bounds_mm": {
                "min": [box.Min.X, box.Min.Y, box.Min.Z],
                "max": [box.Max.X, box.Max.Y, box.Max.Z],
            },
        })
    derivative_files = {}
    for path in (master_3dm, master_obj, os.path.join(output_dir, "solitaire_reference_master_v2.mtl")):
        derivative_files[os.path.basename(path)] = {
            "sha256": sha256(path), "byte_size": os.path.getsize(path),
        }
    receipt = {
        "schema": "myjewl.goal0009.preview_master_receipt.v2",
        "generated_by": "Azure Rhino.Compute 8 /grasshopper",
        "rhino_version": str(Rhino.RhinoApp.Version),
        "job_id": safe_job,
        "template": P,
        "authority": "preview_only_not_manufacturing",
        "manufacturing_gates": "pending_not_run_needs_calibration",
        "parts": files,
        "derivatives": derivative_files,
    }
    receipt_path = os.path.join(output_dir, "solitaire_reference_master_v2_receipt.json")
    with open(receipt_path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {
        "schema_version": 1,
        "execution_status": "completed",
        "reason_code": "goal0009_azure_rhino_preview_master_exported",
        "job_id": safe_job,
        "template_id": P["template_id"],
        "output_dir": output_dir,
        "part_count": len(parts),
        "receipt_sha256": sha256(receipt_path),
        "authority": "preview_only_not_manufacturing",
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


try:
    response_json = json.dumps(run(), sort_keys=True)
except Exception as exc:
    response_json = json.dumps({
        "schema_version": 1,
        "execution_status": "failed",
        "reason_code": "goal0009_azure_rhino_preview_master_failed",
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }, sort_keys=True)
'''


def build(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_python3_ghx(
        SCRIPT,
        inputs=(
            ("RH_IN:json_string", "json_string", "item"),
            ("RH_IN:job_id", "job_id", "item"),
        ),
        outputs=(("RH_OUT:response_json", "response_json"),),
    ))
    return destination


if __name__ == "__main__":
    build("manual/rhino_compute_bridge/goal0009_preview_master_v2.ghx")
