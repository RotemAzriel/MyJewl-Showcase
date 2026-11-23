#!/usr/bin/env python3
"""Build the small native Rhino.Compute GHX used by M4 Phase C.1.

The definition deliberately has a narrow Resthopper contract:
``RH_IN:json_string`` and ``RH_IN:job_id`` are item string inputs and
``RH_OUT:response_json`` is the adapter-compatible response.  Geometry is
written by the native Python 3 Script component as receipt-backed STL files,
which keeps the smoke definition independent of panel state.
"""

from pathlib import Path

try:
    from manual.tools.build_ghx import build_python3_ghx
except ModuleNotFoundError:  # direct execution from manual/tools
    from build_ghx import build_python3_ghx


SCRIPT = r'''import base64
import hashlib
import json
import math
import os
import struct
import uuid


def _vec_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normal(a, b, c):
    n = _cross(_vec_sub(b, a), _vec_sub(c, a))
    length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
    return (n[0] / length, n[1] / length, n[2] / length)


class Mesh(object):
    def __init__(self):
        self.vertices = []
        self.triangles = []

    def add(self, other):
        offset = len(self.vertices)
        self.vertices.extend(other.vertices)
        self.triangles.extend((a + offset, b + offset, c + offset)
                              for a, b, c in other.triangles)


def _torus(major, radial, vertical, segments=48, tubes=12):
    mesh = Mesh()
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        for j in range(tubes):
            phi = 2.0 * math.pi * j / tubes
            radius = major + radial * math.cos(phi)
            mesh.vertices.append((radius * math.cos(theta),
                                  radius * math.sin(theta),
                                  vertical * math.sin(phi)))
    for i in range(segments):
        ni = (i + 1) % segments
        for j in range(tubes):
            nj = (j + 1) % tubes
            a = i * tubes + j
            b = ni * tubes + j
            c = ni * tubes + nj
            d = i * tubes + nj
            mesh.triangles.extend(((a, b, c), (a, c, d)))
    return mesh


def _stone(length, width, depth, center_z, segments=32, rings=8):
    mesh = Mesh()
    # A closed, faceted ellipsoid.  The independent x/y radii preserve shape
    # diversity for round, oval, emerald, and radiant smoke configs.
    for ring in range(1, rings):
        phi = -math.pi / 2.0 + math.pi * ring / rings
        z = center_z + depth * 0.5 * math.sin(phi)
        scale = math.cos(phi)
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            mesh.vertices.append((length * 0.5 * scale * math.cos(theta),
                                  width * 0.5 * scale * math.sin(theta), z))
    bottom = len(mesh.vertices)
    mesh.vertices.append((0.0, 0.0, center_z - depth * 0.5))
    top = len(mesh.vertices)
    mesh.vertices.append((0.0, 0.0, center_z + depth * 0.5))
    for ring in range(rings - 2):
        next_ring = ring + 1
        for i in range(segments):
            ni = (i + 1) % segments
            a = ring * segments + i
            b = ring * segments + ni
            c = next_ring * segments + ni
            d = next_ring * segments + i
            mesh.triangles.extend(((a, b, c), (a, c, d)))
    first = 0
    last = (rings - 2) * segments
    for i in range(segments):
        ni = (i + 1) % segments
        mesh.triangles.append((bottom, ni, i))
        mesh.triangles.append((top, last + i, last + ni))
    return mesh


def _cylinder(radius, z0, z1, x, y, segments=16):
    mesh = Mesh()
    for z in (z0, z1):
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            mesh.vertices.append((x + radius * math.cos(theta),
                                  y + radius * math.sin(theta), z))
    bottom = len(mesh.vertices)
    mesh.vertices.append((x, y, z0))
    top = len(mesh.vertices)
    mesh.vertices.append((x, y, z1))
    for i in range(segments):
        ni = (i + 1) % segments
        mesh.triangles.extend(((i, ni, segments + ni),
                               (i, segments + ni, segments + i)))
        mesh.triangles.append((bottom, ni, i))
        mesh.triangles.append((top, segments + i, segments + ni))
    return mesh


def _part_meshes(spec):
    ring = spec["ring"]
    setting = spec["setting"]
    center = next(stone for stone in spec["stones"] if stone["role"] == "center")
    dimensions = center["dimensions_mm"]
    size = float(ring["size"])
    inner_diameter = 11.63 + 0.8128 * size
    band_width = float(ring["band_width_mm"])
    band_thickness = float(ring["band_thickness_mm"])
    major = inner_diameter * 0.5 + band_thickness * 0.5
    length = float(dimensions["length"])
    width = float(dimensions["width"])
    depth = float(dimensions.get("depth", min(length, width) * 0.6))
    center_z = band_width * 0.5 + depth * 0.5 + 1.8
    band = _torus(major, band_thickness * 0.5, band_width * 0.5)
    stone = _stone(length, width, depth, center_z)
    prongs = Mesh()
    count = int(setting.get("prong_count") or 0)
    if count <= 0:
        count = 4
    prong_radius = max(0.18, min(length, width) * 0.045)
    prong_z0 = center_z - depth * 0.38
    prong_z1 = center_z + depth * 0.65
    for i in range(count):
        theta = 2.0 * math.pi * i / count
        x = length * 0.36 * math.cos(theta)
        y = width * 0.36 * math.sin(theta)
        prongs.add(_cylinder(prong_radius, prong_z0, prong_z1, x, y))
    return band, stone, prongs, {
        "ring_size": size,
        "band_width_mm": band_width,
        "band_thickness_mm": band_thickness,
        "stone_shape": center["shape"],
        "stone_dimensions_mm": dimensions,
        "prong_count": count,
    }


def _write_stl(path, mesh, name):
    with open(path, "wb") as handle:
        handle.write((name[:80]).ljust(80, " ").encode("ascii", "replace"))
        handle.write(struct.pack("<I", len(mesh.triangles)))
        for a, b, c in mesh.triangles:
            va, vb, vc = mesh.vertices[a], mesh.vertices[b], mesh.vertices[c]
            handle.write(struct.pack("<3f", *_normal(va, vb, vc)))
            handle.write(struct.pack("<9f", *(va + vb + vc)))
            handle.write(struct.pack("<H", 0))


def _facts(path, mesh):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    xs = [v[0] for v in mesh.vertices]
    ys = [v[1] for v in mesh.vertices]
    zs = [v[2] for v in mesh.vertices]
    return {
        "sha256": digest.hexdigest(),
        "byte_size": os.path.getsize(path),
        "triangle_count": len(mesh.triangles),
        "bounds_mm": [[min(xs), min(ys), min(zs)],
                      [max(xs), max(ys), max(zs)]],
    }


def _run():
    spec_value = json_string if json_string else "{}"
    spec = json.loads(spec_value)
    job = str(job_id or spec["job"]["job_id"])
    safe_job = "".join(char if char.isalnum() or char in "._-" else "_" for char in job)
    export_id = str(uuid.uuid4())
    root = os.environ.get("MYJEWL_NATIVE_OUTPUT_DIR", r"C:\MyJewlGenerated\m4_phase_c1_native")
    export_dir = os.path.join(root, safe_job, export_id)
    if not os.path.isdir(export_dir):
        os.makedirs(export_dir)
    band, stone, prongs, debug = _part_meshes(spec)
    combined = Mesh()
    combined.add(band)
    combined.add(stone)
    combined.add(prongs)
    meshes = {"band.stl": band, "stone.stl": stone, "prongs.stl": prongs, "model.stl": combined}
    files = {}
    for filename, mesh in meshes.items():
        path = os.path.join(export_dir, filename)
        _write_stl(path, mesh, safe_job + "_" + filename)
        files[filename] = _facts(path, mesh)
    encoded_files = {}
    for filename in meshes:
        with open(os.path.join(export_dir, filename), "rb") as handle:
            encoded_files[filename] = base64.b64encode(handle.read()).decode("ascii")
    receipt = {
        "schema_version": 1,
        "export_id": export_id,
        "job_id": job,
        "gh_definition_path": os.environ.get("MYJEWL_GH_DEFINITION_PATH", "embedded:phase_c1_native"),
        "gh_definition_sha256": os.environ.get("MYJEWL_GH_DEFINITION_SHA256", "0" * 64),
        "gh_definition_version": os.environ.get("MYJEWL_GH_DEFINITION_VERSION", "phase_c1_native_rh_in_v1"),
        "exported_at_utc": "compute",
        "files": files,
    }
    receipt_path = os.path.join(export_dir, "export_receipt.json")
    with open(receipt_path, "w") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
    response = {
        "execution_status": "completed",
        "reason_code": "phase_c1_native_rh_in_export_executed",
        "assets": {"STL": {"path": os.path.join(export_dir, "model.stl")}},
        "geometry_gates": [{"code": code, "status": "pending", "reason_code": "check_not_implemented"}
                           for code in (pending_geometry_checks or [])],
        "debug": {"job_id": job, "export_id": export_id, "export_dir": export_dir,
                  "files": files, "files_base64": encoded_files, **debug},
    }
    return json.dumps(response, sort_keys=True)


try:
    response_json = _run()
except Exception as exc:
    response_json = json.dumps({"execution_status": "failed",
                                "reason_code": "phase_c1_native_export_error",
                                "debug": {"error": str(exc)}}, sort_keys=True)
'''


def build(output: Path) -> None:
    ghx = build_python3_ghx(
        script_source=SCRIPT,
        inputs=[
            ("json_string", "json_string", "item"),
            ("job_id", "job_id", "item"),
            ("pending_geometry_checks", "pending_geometry_checks", "list"),
        ],
        outputs=[("response_json", "response_json")],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(ghx)


if __name__ == "__main__":
    build(Path("manual/rhino_compute_bridge/phase_c1_native_rh_in_v1.ghx"))
