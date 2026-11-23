#!/usr/bin/env python3
"""Adapter-compatible Solitaire prototype geometry smoke bridge.

This manual helper is intentionally outside the foundation package. It creates
simple prototype mesh geometry with the Python standard library, writes requested
GLB/STL files only when possible, and returns the existing /solve response
shape. It is not manufacturing CAD and does not import Rhino or Grasshopper.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8766
DEFAULT_DEFINITION_ID = "solitaire_prototype_smoke_v0"
DEFAULT_OUTPUT_DIR = (
    Path(r"C:\MyJewlGenerated\solitaire_prototype_smoke_v0")
    if os.name == "nt"
    else Path("/private/tmp/myjewl_generated/solitaire_prototype_smoke_v0")
)
REQUIRED_PAYLOAD_FIELDS = {
    "definition_id",
    "job_id",
    "archetype",
    "spec",
    "pending_geometry_checks",
    "output_targets",
}


Vector = tuple[float, float, float]
Triangle = tuple[int, int, int]


@dataclass
class Mesh:
    vertices: list[Vector]
    triangles: list[Triangle]

    def append(self, other: "Mesh") -> None:
        offset = len(self.vertices)
        self.vertices.extend(other.vertices)
        self.triangles.extend(
            (a + offset, b + offset, c + offset) for a, b, c in other.triangles
        )


def build_solitaire_response(payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Build smoke geometry, write requested assets, and return bridge JSON."""

    spec = payload["spec"]
    job_id = str(payload.get("job_id") or spec.get("job", {}).get("job_id", "job"))
    output_targets = payload.get("output_targets", [])
    if not isinstance(output_targets, list):
        output_targets = []

    mesh, geometry_debug = build_solitaire_mesh(spec)
    safe_job_id = safe_filename(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    assets: dict[str, dict[str, str]] = {}
    export_errors: dict[str, str] = {}
    requested_targets = [target for target in output_targets if target in {"GLB", "STL"}]
    for target in requested_targets:
        path = output_dir / f"{safe_job_id}_{DEFAULT_DEFINITION_ID}.{target.lower()}"
        try:
            if target == "STL":
                write_ascii_stl(path, mesh, name=f"{safe_job_id}_{DEFAULT_DEFINITION_ID}")
            elif target == "GLB":
                write_glb(path, mesh)
        except OSError as exc:
            export_errors[target] = str(exc)
            continue

        if path.exists() and path.is_file() and path.stat().st_size > 0:
            assets[target] = {"path": str(path)}
        else:
            export_errors[target] = "export_did_not_create_file"

    gate_codes = [
        code
        for code in payload.get("pending_geometry_checks", [])
        if isinstance(code, str)
    ]

    return {
        "execution_status": "completed",
        "reason_code": "solitaire_prototype_geometry_smoke_executed",
        "assets": assets,
        "geometry_gates": [
            {
                "code": code,
                "status": "pending",
                "reason_code": "check_not_implemented",
                "details": {
                    "executed_check": DEFAULT_DEFINITION_ID,
                    "calibrated": False,
                },
            }
            for code in gate_codes
        ],
        "debug": {
            "job_id": job_id,
            "archetype": payload.get("archetype"),
            "definition_id": DEFAULT_DEFINITION_ID,
            "mesh_vertex_count": len(mesh.vertices),
            "mesh_triangle_count": len(mesh.triangles),
            "export_errors": export_errors,
            **geometry_debug,
        },
    }


def build_solitaire_mesh(spec: dict[str, Any]) -> tuple[Mesh, dict[str, object]]:
    ring = spec["ring"]
    setting = spec["setting"]
    center = next(stone for stone in spec["stones"] if stone["role"] == "center")
    dimensions = center["dimensions_mm"]

    ring_size_standard = str(ring["size_standard"])
    ring_size = ring["size"]
    if ring_size_standard != "US" or not isinstance(ring_size, (int, float)):
        raise ValueError("solitaire smoke v0 supports numeric US ring size only")

    inner_diameter = 11.63 + 0.8128 * float(ring_size)
    band_width = float(ring["band_width_mm"])
    band_thickness = float(ring["band_thickness_mm"])
    centerline_radius = inner_diameter / 2.0 + band_thickness / 2.0

    stone_length = float(dimensions["length"])
    stone_width = float(dimensions["width"])
    stone_depth = float(dimensions.get("depth", min(stone_length, stone_width) * 0.6))
    stone_center_y = centerline_radius + band_thickness / 2.0 + stone_width * 0.18

    mesh = Mesh(vertices=[], triangles=[])
    mesh.append(
        make_elliptical_torus(
            major_radius=centerline_radius,
            radial_radius=band_thickness / 2.0,
            z_radius=band_width / 2.0,
            segments=96,
            tube_segments=16,
        )
    )
    mesh.append(
        make_stone_proxy(
            length=stone_length,
            width=stone_width,
            depth=stone_depth,
            center_y=stone_center_y,
            radial_segments=32,
        )
    )

    prong_count = int(setting.get("prong_count") or 0)
    prong_radius = max(0.18, min(stone_length, stone_width) * 0.045)
    prong_height = stone_depth + band_thickness * 1.4
    prong_center_y = stone_center_y - stone_depth * 0.15
    prong_offsets = prong_layout(prong_count, stone_length, stone_width, prong_radius)
    for x, z in prong_offsets:
        mesh.append(
            make_cylinder(
                radius=prong_radius,
                height=prong_height,
                center=(x, prong_center_y, z),
                axis="y",
                segments=16,
            )
        )

    return mesh, {
        "ring_size_standard": ring_size_standard,
        "ring_size": ring_size,
        "inner_diameter_mm": round(inner_diameter, 4),
        "band_centerline_radius_mm": round(centerline_radius, 4),
        "center_stone_shape": center["shape"],
        "center_stone_dimensions_mm": dimensions,
        "prong_count": prong_count,
    }


def make_elliptical_torus(
    major_radius: float,
    radial_radius: float,
    z_radius: float,
    segments: int,
    tube_segments: int,
) -> Mesh:
    vertices: list[Vector] = []
    triangles: list[Triangle] = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        for j in range(tube_segments):
            phi = 2.0 * math.pi * j / tube_segments
            radial_offset = radial_radius * math.cos(phi)
            z = z_radius * math.sin(phi)
            radius = major_radius + radial_offset
            vertices.append((radius * cos_t, radius * sin_t, z))

    for i in range(segments):
        ni = (i + 1) % segments
        for j in range(tube_segments):
            nj = (j + 1) % tube_segments
            a = i * tube_segments + j
            b = ni * tube_segments + j
            c = ni * tube_segments + nj
            d = i * tube_segments + nj
            triangles.extend(((a, b, c), (a, c, d)))
    return Mesh(vertices, triangles)


def make_stone_proxy(
    length: float,
    width: float,
    depth: float,
    center_y: float,
    radial_segments: int,
) -> Mesh:
    vertices: list[Vector] = []
    triangles: list[Triangle] = []

    table_y = center_y + depth * 0.36
    girdle_y = center_y
    culet_y = center_y - depth * 0.64
    table_rx = length * 0.28
    table_rz = width * 0.28
    girdle_rx = length * 0.5
    girdle_rz = width * 0.5

    table_center = len(vertices)
    vertices.append((0.0, table_y, 0.0))
    table_ring = []
    girdle_ring = []
    for i in range(radial_segments):
        angle = 2.0 * math.pi * i / radial_segments
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        table_ring.append(len(vertices))
        vertices.append((table_rx * cos_a, table_y, table_rz * sin_a))
        girdle_ring.append(len(vertices))
        vertices.append((girdle_rx * cos_a, girdle_y, girdle_rz * sin_a))
    culet = len(vertices)
    vertices.append((0.0, culet_y, 0.0))

    for i in range(radial_segments):
        ni = (i + 1) % radial_segments
        t0 = table_ring[i]
        t1 = table_ring[ni]
        g0 = girdle_ring[i]
        g1 = girdle_ring[ni]
        triangles.append((table_center, t0, t1))
        triangles.extend(((t0, g0, g1), (t0, g1, t1)))
        triangles.append((g0, culet, g1))

    return Mesh(vertices, triangles)


def make_cylinder(
    radius: float,
    height: float,
    center: Vector,
    axis: str,
    segments: int,
) -> Mesh:
    if axis != "y":
        raise ValueError("only y-axis cylinders are supported")

    cx, cy, cz = center
    low_y = cy - height / 2.0
    high_y = cy + height / 2.0
    vertices: list[Vector] = [(cx, low_y, cz), (cx, high_y, cz)]
    low_ring = []
    high_ring = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        x = cx + radius * math.cos(angle)
        z = cz + radius * math.sin(angle)
        low_ring.append(len(vertices))
        vertices.append((x, low_y, z))
        high_ring.append(len(vertices))
        vertices.append((x, high_y, z))

    triangles: list[Triangle] = []
    for i in range(segments):
        ni = (i + 1) % segments
        l0 = low_ring[i]
        l1 = low_ring[ni]
        h0 = high_ring[i]
        h1 = high_ring[ni]
        triangles.append((0, l1, l0))
        triangles.append((1, h0, h1))
        triangles.extend(((l0, l1, h1), (l0, h1, h0)))
    return Mesh(vertices, triangles)


def prong_layout(
    prong_count: int,
    stone_length: float,
    stone_width: float,
    prong_radius: float,
) -> list[tuple[float, float]]:
    if prong_count <= 0:
        return []

    radius_x = stone_length / 2.0 + prong_radius * 0.8
    radius_z = stone_width / 2.0 + prong_radius * 0.8
    if prong_count == 4:
        angles = [math.radians(value) for value in (45, 135, 225, 315)]
    else:
        angles = [2.0 * math.pi * i / prong_count for i in range(prong_count)]
    return [(radius_x * math.cos(angle), radius_z * math.sin(angle)) for angle in angles]


def write_ascii_stl(path: Path, mesh: Mesh, name: str) -> None:
    lines = [f"solid {name}"]
    for a, b, c in mesh.triangles:
        normal = triangle_normal(mesh.vertices[a], mesh.vertices[b], mesh.vertices[c])
        lines.append(
            f"  facet normal {normal[0]:.8g} {normal[1]:.8g} {normal[2]:.8g}"
        )
        lines.append("    outer loop")
        for index in (a, b, c):
            vertex = mesh.vertices[index]
            lines.append(f"      vertex {vertex[0]:.8g} {vertex[1]:.8g} {vertex[2]:.8g}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def write_glb(path: Path, mesh: Mesh) -> None:
    positions = b"".join(
        struct.pack("<fff", float(x), float(y), float(z)) for x, y, z in mesh.vertices
    )
    indices = b"".join(
        struct.pack("<III", int(a), int(b), int(c)) for a, b, c in mesh.triangles
    )
    positions_offset = 0
    indices_offset = align4(len(positions))
    binary = pad4(positions) + indices
    binary = pad4(binary)

    mins = [min(vertex[i] for vertex in mesh.vertices) for i in range(3)]
    maxs = [max(vertex[i] for vertex in mesh.vertices) for i in range(3)]
    gltf = {
        "asset": {"version": "2.0", "generator": DEFAULT_DEFINITION_ID},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "solitaire_prototype_smoke_v0"}],
        "meshes": [
            {
                "name": "solitaire_prototype_smoke_v0",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "mode": 4,
                    }
                ],
            }
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": positions_offset,
                "byteLength": len(positions),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": indices_offset,
                "byteLength": len(indices),
                "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(mesh.vertices),
                "type": "VEC3",
                "min": mins,
                "max": maxs,
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5125,
                "count": len(mesh.triangles) * 3,
                "type": "SCALAR",
            },
        ],
    }
    json_chunk = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary)
    with path.open("wb") as file:
        file.write(struct.pack("<III", 0x46546C67, 2, total_length))
        file.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        file.write(json_chunk)
        file.write(struct.pack("<I4s", len(binary), b"BIN\x00"))
        file.write(binary)


def triangle_normal(a: Vector, b: Vector, c: Vector) -> Vector:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def align4(value: int) -> int:
    return (value + 3) & ~3


def pad4(value: bytes, pad_byte: bytes = b"\x00") -> bytes:
    padding = align4(len(value)) - len(value)
    return value + pad_byte * padding


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "job"


def validate_payload(
    payload: object,
    expected_definition_id: str,
) -> tuple[bool, str | None]:
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"

    missing_fields = sorted(REQUIRED_PAYLOAD_FIELDS.difference(payload))
    if missing_fields:
        return False, "missing_required_fields"

    if payload.get("definition_id") != expected_definition_id:
        return False, "rhino_definition_not_executed"

    if payload.get("archetype") != "solitaire":
        return False, "unsupported_archetype_for_solitaire_smoke"

    if not isinstance(payload.get("pending_geometry_checks"), list):
        return False, "pending_geometry_checks_must_be_array"

    if not isinstance(payload.get("output_targets"), list):
        return False, "output_targets_must_be_array"

    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return False, "spec_must_be_object"

    try:
        build_solitaire_mesh(spec)
    except (KeyError, StopIteration, TypeError, ValueError):
        return False, "solitaire_smoke_spec_unsupported"

    return True, None


def handle_solve_request(
    path: str,
    headers: dict[str, str],
    body: bytes,
    api_key: str,
    definition_id: str = DEFAULT_DEFINITION_ID,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[int, dict[str, Any]]:
    if path != "/solve":
        return 404, {
            "execution_status": "failed",
            "reason_code": "endpoint_not_found",
        }

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    if normalized_headers.get("x-api-key") != api_key:
        return 401, {
            "execution_status": "failed",
            "reason_code": "unauthorized",
        }

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    valid, reason_code = validate_payload(payload, definition_id)
    if not valid or not isinstance(payload, dict):
        return 400, {
            "execution_status": "failed",
            "reason_code": reason_code,
        }

    try:
        return 200, build_solitaire_response(payload, output_dir)
    except (OSError, KeyError, StopIteration, TypeError, ValueError) as exc:
        return 500, {
            "execution_status": "failed",
            "reason_code": "solitaire_prototype_geometry_smoke_failed",
            "debug": {"error": str(exc)},
        }


def make_handler(
    api_key: str,
    definition_id: str = DEFAULT_DEFINITION_ID,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
):
    class SolveBridgeHandler(BaseHTTPRequestHandler):
        server_version = "SolitairePrototypeSmokeSolveBridge/0.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            status_code, response = handle_solve_request(
                path=self.path,
                headers=dict(self.headers.items()),
                body=self._read_body(),
                api_key=api_key,
                definition_id=definition_id,
                output_dir=output_dir,
            )
            self._send_json(status_code, response)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_body(self) -> bytes:
            raw_length = self.headers.get("content-length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                return b""

            return self.rfile.read(length)

        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SolveBridgeHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an adapter-compatible Solitaire prototype geometry smoke bridge.",
    )
    parser.add_argument("--host", default=os.getenv("SOLVE_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SOLVE_BRIDGE_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--definition-id",
        default=os.getenv("RHINO_COMPUTE_DEFINITION_ID", DEFAULT_DEFINITION_ID),
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("SOLITAIRE_SMOKE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)),
    )
    return parser.parse_args()


def main() -> int:
    api_key = os.getenv("RHINO_COMPUTE_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "reason_code": "missing_required_environment",
                    "missing_environment": ["RHINO_COMPUTE_API_KEY"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    args = parse_args()
    output_dir = Path(args.output_dir).expanduser()
    handler = make_handler(
        api_key=api_key,
        definition_id=args.definition_id,
        output_dir=output_dir,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        json.dumps(
            {
                "status": "listening",
                "host": args.host,
                "port": args.port,
                "definition_id": args.definition_id,
                "endpoint": "/solve",
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
