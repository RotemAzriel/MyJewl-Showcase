#!/usr/bin/env python3
"""Build the local Goal 0010 RhinoCommon component-pilot definition.

The definition is a human-maintained candidate source for one bounded
solitaire reference. Rhino remains the geometry authority; this file never
certifies manufacturability and the generated GHX is not a finished master
until it passes the visual review gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import traceback
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


OUTPUT_ROOT = r"C:\MyJewlGenerated\goal0010_component_pilot"
TOLERANCE = 0.001
ANGLE_TOLERANCE = math.radians(0.25)
P = {
    "template_id": "goal0010-split-ring-classic-basket-candidate-v1",
    "reference_band_type": "split_ring_solitaire",
    "reference_head_type": "classic_basket",
    "authoring_status": "local_rhinocommon_candidate_pending_visual_review",
    "units": "millimeters",
    "ring_size_us": 6.5,
    "inner_diameter": 16.91,
    "shank_width": 2.05,
    "shank_radial_thickness": 1.65,
    "shoulder_root_radius": 0.52,
    "shoulder_neck_radius": 0.38,
    "shoulder_split_half_gap_root": 0.20,
    "shoulder_split_half_gap_neck": 0.62,
    "shoulder_contact_z": 0.72,
    "stone_diameter": 6.50,
    "stone_total_depth": 3.90,
    "stone_table_ratio": 0.56,
    "stone_crown_height": 0.96,
    "stone_girdle_thickness": 0.18,
    "stone_girdle_z": 12.10,
    "basket_lower_radius": 1.30,
    "basket_upper_radius": 2.72,
    "basket_lower_z": 9.62,
    "basket_upper_z": 11.34,
    "gallery_wire_radius": 0.17,
    "prong_root_radius": 0.32,
    "prong_seat_radius": 0.255,
    "prong_tip_radius": 0.19,
    "prong_tip_height": 0.46,
    "pavilion_clearance": 0.58,
    "surface_continuity": "candidate_integrated_shoulder_sweeps_and_open_basket",
    "manufacturing_authority": False,
}


def curve(points, degree=3):
    return rg.Curve.CreateInterpolatedCurve(points, degree)


def pipe(curve_value, radius, cap=rg.PipeCapMode.Round):
    pieces = rg.Brep.CreatePipe(curve_value, radius, False, cap, True, TOLERANCE, ANGLE_TOLERANCE)
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
        ok, plane = rail.PerpendicularFrameAt(rail.Domain.ParameterAt(fraction))
        if not ok:
            plane = rg.Plane(rail.PointAt(fraction), rail.TangentAt(fraction))
        sections.append(rg.Circle(plane, radius).ToNurbsCurve())
    lofts = rg.Brep.CreateFromLoft(
        sections, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False,
    )
    if not lofts:
        raise RuntimeError("tapered sweep loft failed")
    return lofts[0].CapPlanarHoles(TOLERANCE) or lofts[0]


def shank():
    inner_radius = P["inner_diameter"] / 2.0
    section_center = inner_radius + P["shank_radial_thickness"] / 2.0
    plane = rg.Plane(rg.Point3d(section_center, 0, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis)
    section = rg.Ellipse(
        plane, P["shank_radial_thickness"] / 2.0, P["shank_width"] / 2.0,
    ).ToNurbsCurve()
    revolved = rg.RevSurface.Create(
        section, rg.Line(rg.Point3d(0, -20, 0), rg.Point3d(0, 20, 0)), 0, math.pi * 2.0,
    )
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
    top = P["stone_girdle_z"] + P["stone_girdle_thickness"] / 2.0
    bottom = P["stone_girdle_z"] - P["stone_girdle_thickness"] / 2.0
    pavilion_depth = P["stone_total_depth"] - P["stone_crown_height"] - P["stone_girdle_thickness"]
    break_z = bottom - pavilion_depth * 0.58
    culet_z = bottom - pavilion_depth
    rings = {}

    def add_ring(key, count, ring_radius, z, phase):
        rings[key] = [mesh.Vertices.Add(
            math.cos(phase + math.pi * 2.0 * index / count) * ring_radius,
            math.sin(phase + math.pi * 2.0 * index / count) * ring_radius,
            z,
        ) for index in range(count)]

    add_ring("table", 8, radius * P["stone_table_ratio"], crown_z, math.pi / 8.0)
    add_ring("star", 8, radius * 0.79, P["stone_girdle_z"] + P["stone_crown_height"] * 0.46, math.pi / 8.0)
    add_ring("girdle_top", 32, radius, top, 0.0)
    add_ring("girdle_bottom", 32, radius, bottom, 0.0)
    add_ring("pavilion_break", 16, radius * 0.57, break_z, math.pi / 16.0)
    culet = mesh.Vertices.Add(0, 0, culet_z)
    table_center = mesh.Vertices.Add(0, 0, crown_z)
    for index in range(8):
        nxt = (index + 1) % 8
        mesh.Faces.AddFace(table_center, rings["table"][index], rings["table"][nxt])
        mesh.Faces.AddFace(rings["table"][index], rings["star"][index], rings["table"][nxt])
        mesh.Faces.AddFace(rings["table"][nxt], rings["star"][index], rings["girdle_top"][(4 * index + 2) % 32], rings["star"][nxt])
        left = (4 * index) % 32
        mesh.Faces.AddFace(rings["star"][index], rings["girdle_top"][left], rings["girdle_top"][(left + 1) % 32])
        mesh.Faces.AddFace(rings["star"][index], rings["girdle_top"][(left + 1) % 32], rings["girdle_top"][(left + 2) % 32])
    for index in range(32):
        nxt = (index + 1) % 32
        mesh.Faces.AddFace(rings["girdle_top"][index], rings["girdle_bottom"][index], rings["girdle_bottom"][nxt], rings["girdle_top"][nxt])
    for index in range(16):
        nxt = (index + 1) % 16
        left = (2 * index) % 32
        mid = (left + 1) % 32
        right = (left + 2) % 32
        mesh.Faces.AddFace(rings["girdle_bottom"][left], rings["pavilion_break"][index], rings["girdle_bottom"][mid])
        mesh.Faces.AddFace(rings["girdle_bottom"][mid], rings["pavilion_break"][index], rings["girdle_bottom"][right])
        mesh.Faces.AddFace(rings["pavilion_break"][index], rings["girdle_bottom"][right], rings["pavilion_break"][nxt])
        mesh.Faces.AddFace(rings["pavilion_break"][index], culet, rings["pavilion_break"][nxt])
    mesh.UnifyNormals()
    mesh.Normals.ComputeNormals()
    if not mesh.IsClosed:
        mesh.FillHoles()
        mesh.UnifyNormals()
        mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


def build_parts():
    parts = [("shank", "metal", shank())]
    for side in (-1.0, 1.0):
        suffix = "L" if side < 0 else "R"
        front = [
            rg.Point3d(side * 4.86, -P["shoulder_split_half_gap_root"], P["shoulder_contact_z"]),
            rg.Point3d(side * 4.78, -0.34, 1.72),
            rg.Point3d(side * 4.38, -0.48, 4.20),
            rg.Point3d(side * 3.62, -0.56, 7.20),
            rg.Point3d(side * 2.72, -P["shoulder_split_half_gap_neck"], 10.56),
        ]
        rear = [rg.Point3d(point.X, -point.Y, point.Z) for point in front]
        radii = [P["shoulder_root_radius"], 0.50, 0.46, 0.42, P["shoulder_neck_radius"]]
        parts.append(("shoulder_front_" + suffix, "metal", tapered_sweep(front, radii)))
        parts.append(("shoulder_rear_" + suffix, "metal", tapered_sweep(rear, radii)))

    parts.append(("basket_lower_rail", "metal", circle_pipe(P["basket_lower_radius"], P["basket_lower_z"], P["gallery_wire_radius"])))
    parts.append(("stone_seat_rail", "metal", circle_pipe(P["basket_upper_radius"], P["basket_upper_z"], P["gallery_wire_radius"] * 0.84)))
    for index in range(4):
        angle = math.pi / 4.0 + index * math.pi / 2.0
        radial = rg.Vector3d(math.cos(angle), math.sin(angle), 0)
        tangent = rg.Vector3d(-math.sin(angle), math.cos(angle), 0)
        supports = [
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
        parts.append(("prong_{:02d}".format(index + 1), "metal", tapered_sweep(prongs, [P["prong_root_radius"], 0.30, 0.27, P["prong_seat_radius"], P["prong_tip_radius"]])))
        tip = rg.Sphere(prongs[-1], P["prong_tip_radius"] * 1.03).ToBrep()
        tip.Transform(rg.Transform.Scale(rg.Plane(prongs[-1], rg.Vector3d.XAxis, rg.Vector3d.YAxis), 1.0, 1.0, 0.68))
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
    header = ("MyJewl Goal0010 " + name).encode("ascii", "replace")[:80].ljust(80, b"\0")
    with open(path, "wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", mesh.Faces.Count))
        for face in mesh.Faces:
            a, b, c = (mesh.Vertices[index] for index in (face.A, face.B, face.C))
            normal = rg.Vector3d.CrossProduct(rg.Vector3d(b - a), rg.Vector3d(c - a))
            normal.Unitize()
            stream.write(struct.pack("<3f", normal.X, normal.Y, normal.Z))
            for vertex in (a, b, c):
                stream.write(struct.pack("<3f", vertex.X, vertex.Y, vertex.Z))
            stream.write(struct.pack("<H", 0))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_obj(path, meshes):
    mtl_name = "solitaire_component_pilot_v1.mtl"
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
                stream.write("f " + " ".join("{}//{}".format(vertex_offset + item, vertex_offset + item) for item in ids) + "\n")
            vertex_offset += mesh.Vertices.Count
    with open(os.path.join(os.path.dirname(path), mtl_name), "w", encoding="utf-8", newline="\n") as stream:
        stream.write("newmtl yellow_gold_preview\nKd 0.83 0.57 0.20\nKs 1 1 1\nNs 900\n\n")
        stream.write("newmtl diamond_preview\nKd 0.92 0.97 1.0\nKs 1 1 1\nNs 1000\nd 0.28\nNi 2.417\n")


def write_3dm(path, parts):
    model = Rhino.FileIO.File3dm()
    model.Settings.ModelUnitSystem = Rhino.UnitSystem.Millimeters
    metal_layer = Rhino.DocObjects.Layer()
    metal_layer.Name = "G0010_CANDIDATE_METAL"
    model.Layers.Add(metal_layer)
    metal_index = model.Layers.Count - 1
    stone_layer = Rhino.DocObjects.Layer()
    stone_layer.Name = "G0010_CANDIDATE_STONE"
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
    if str(request.get("template_id", P["template_id"])) != P["template_id"]:
        raise ValueError("unsupported template_id")
    safe_job = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id or "goal0010-component-pilot"))[:80]
    output_dir = os.path.join(OUTPUT_ROOT, safe_job)
    os.makedirs(output_dir, exist_ok=True)
    parts = build_parts()
    meshes = [(name, role, mesh_geometry(geometry)) for name, role, geometry in parts]
    master_3dm = os.path.join(output_dir, "solitaire_component_pilot_v1.3dm")
    master_obj = os.path.join(output_dir, "solitaire_component_pilot_v1.obj")
    write_3dm(master_3dm, parts)
    write_obj(master_obj, meshes)
    files = []
    for name, role, mesh in meshes:
        stl_path = os.path.join(output_dir, name + ".stl")
        write_binary_stl(stl_path, mesh, name)
        box = mesh.GetBoundingBox(True)
        files.append({"name": name, "role": role, "file": os.path.basename(stl_path), "sha256": sha256(stl_path), "byte_size": os.path.getsize(stl_path), "vertices": mesh.Vertices.Count, "triangles": mesh.Faces.Count, "closed": bool(mesh.IsClosed), "bounds_mm": {"min": [box.Min.X, box.Min.Y, box.Min.Z], "max": [box.Max.X, box.Max.Y, box.Max.Z]}})
    receipt = {"schema": "myjewl.goal0010.component_pilot_receipt.v1", "generated_by": "local Rhino 8 RhinoCommon candidate", "job_id": safe_job, "template": P, "authority": "preview_only_not_manufacturing", "manufacturing_gates": "pending_not_run_needs_calibration", "parts": files}
    receipt_path = os.path.join(output_dir, "solitaire_component_pilot_v1_receipt.json")
    with open(receipt_path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"schema_version": 1, "execution_status": "completed", "reason_code": "goal0010_local_candidate_source_ready", "job_id": safe_job, "template_id": P["template_id"], "output_dir": output_dir, "part_count": len(parts), "receipt_sha256": sha256(receipt_path), "authority": "preview_only_not_manufacturing", "manufacturing_gates": "pending_not_run_needs_calibration"}


try:
    response_json = json.dumps(run(), sort_keys=True)
except Exception as exc:
    response_json = json.dumps({"schema_version": 1, "execution_status": "failed", "reason_code": "goal0010_local_candidate_source_failed", "error": str(exc), "traceback": traceback.format_exc()}, sort_keys=True)
'''


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
    build("manual/rhino_compute_bridge/goal0010_component_pilot_v1.ghx")
