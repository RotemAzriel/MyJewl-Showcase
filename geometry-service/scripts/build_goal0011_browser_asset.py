#!/usr/bin/env python3
"""Build the exact Goal 0011 V4 browser mesh from receipt-backed Rhino OBJ.

The output is a compact indexed binary derivative for the local Three.js
preview. It preserves every OBJ position, normal, triangle, named-part range,
and receipt role; it does not author or modify jewelry geometry and it never
assigns a manufacturing verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from array import array
from pathlib import Path
from typing import Any


MAGIC = b"MYJV4B1\0"
SCHEMA = "myjewl.goal0011.receipt_backed_browser_mesh.v1"
TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"
BINARY_NAME = "solitaire_realism_master_v4.meshbin"
MANIFEST_NAME = "solitaire_realism_master_v4.browser_mesh.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_receipt(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("receipt must contain a JSON object")
    if value.get("schema") != "myjewl.goal0011.realism_master_receipt.v4":
        raise ValueError("expected the isolated Goal 0011 V4 receipt")
    if value.get("template", {}).get("template_id") != TEMPLATE_ID:
        raise ValueError("receipt template does not match approved V4")
    if value.get("manufacturing_gates") != "pending_not_run_needs_calibration":
        raise ValueError("receipt manufacturing boundary changed unexpectedly")
    return value


def parse_face_vertex(token: str) -> tuple[int, int]:
    fields = token.split("/")
    if len(fields) != 3 or not fields[0] or not fields[2]:
        raise ValueError(f"unsupported OBJ face vertex: {token!r}")
    vertex_index = int(fields[0])
    normal_index = int(fields[2])
    if vertex_index <= 0 or normal_index <= 0:
        raise ValueError("negative/relative OBJ indices are not supported")
    return vertex_index - 1, normal_index - 1


def parse_obj(path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    positions = array("f")
    normals = array("f")
    indices = array("I")
    part_roles = {part["name"]: part["role"] for part in receipt["parts"]}
    expected_triangles = {part["name"]: part["triangles"] for part in receipt["parts"]}
    parts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("v "):
                values = line.split()
                if len(values) != 4:
                    raise ValueError(f"OBJ vertex on line {line_number} is not VEC3")
                positions.extend(float(value) for value in values[1:])
            elif line.startswith("vn "):
                values = line.split()
                if len(values) != 4:
                    raise ValueError(f"OBJ normal on line {line_number} is not VEC3")
                normals.extend(float(value) for value in values[1:])
            elif line.startswith("o "):
                name = line[2:].strip()
                if name not in part_roles:
                    raise ValueError(f"unexpected OBJ object {name!r}")
                if current is not None:
                    current["index_count"] = len(indices) - current["index_start"]
                current = {
                    "name": name,
                    "role": part_roles[name],
                    "index_start": len(indices),
                    "index_count": 0,
                }
                parts.append(current)
            elif line.startswith("f "):
                if current is None:
                    raise ValueError(f"OBJ face on line {line_number} has no named object")
                tokens = line.split()[1:]
                if len(tokens) != 3:
                    raise ValueError(f"OBJ face on line {line_number} is not a triangle")
                for token in tokens:
                    vertex_index, normal_index = parse_face_vertex(token)
                    if vertex_index != normal_index:
                        raise ValueError(
                            "receipt-backed OBJ requires aligned position/normal indices"
                        )
                    indices.append(vertex_index)

    if current is not None:
        current["index_count"] = len(indices) - current["index_start"]

    vertex_count = len(positions) // 3
    normal_count = len(normals) // 3
    if vertex_count != normal_count:
        raise ValueError("OBJ position and normal counts differ")
    if indices and max(indices) >= vertex_count:
        raise ValueError("OBJ index exceeds available vertices")
    if len(parts) != 20 or {part["name"] for part in parts} != set(part_roles):
        raise ValueError("OBJ does not contain the exact 20 receipt-listed named parts")

    for part in parts:
        if part["index_count"] % 3:
            raise ValueError(f"part {part['name']} has a non-triangular index range")
        part["triangles"] = part["index_count"] // 3
        if part["triangles"] != expected_triangles[part["name"]]:
            raise ValueError(
                f"part {part['name']} triangle count differs from the V4 receipt"
            )

    expected_total = sum(expected_triangles.values())
    if len(indices) // 3 != expected_total:
        raise ValueError("OBJ total triangle count differs from the V4 receipt")

    position_values = list(positions)
    bounds = {
        "min": [min(position_values[axis::3]) for axis in range(3)],
        "max": [max(position_values[axis::3]) for axis in range(3)],
    }
    return {
        "positions": positions,
        "normals": normals,
        "indices": indices,
        "parts": parts,
        "vertex_count": vertex_count,
        "triangle_count": len(indices) // 3,
        "bounds_mm": bounds,
    }


def write_asset(
    source_obj: Path,
    receipt_path: Path,
    output_dir: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    receipt = load_receipt(receipt_path)
    parsed = parse_obj(source_obj, receipt)
    output_dir.mkdir(parents=True, exist_ok=True)
    binary_path = output_dir / BINARY_NAME
    manifest_path = output_dir / MANIFEST_NAME

    positions: array[float] = parsed.pop("positions")
    normals: array[float] = parsed.pop("normals")
    indices: array[int] = parsed.pop("indices")
    if sys.byteorder != "little":
        positions.byteswap()
        normals.byteswap()
        indices.byteswap()
    with binary_path.open("wb") as stream:
        stream.write(struct.pack("<8sII", MAGIC, parsed["vertex_count"], len(indices)))
        positions.tofile(stream)
        normals.tofile(stream)
        indices.tofile(stream)

    manifest = {
        "schema": SCHEMA,
        "authority": "receipt_backed_rhino_derived_browser_preview",
        "manufacturing_authority": False,
        "manufacturing_gates": "pending_not_run_needs_calibration",
        "template_id": TEMPLATE_ID,
        "source": "Azure Rhino.Compute named-part OBJ",
        "source_obj": source_obj.name,
        "source_obj_sha256": sha256(source_obj),
        "source_receipt": receipt_path.name,
        "source_receipt_sha256": sha256(receipt_path),
        "binary": {
            "file": BINARY_NAME,
            "sha256": sha256(binary_path),
            "byte_size": binary_path.stat().st_size,
            "header_bytes": 16,
            "position_component_type": "float32",
            "normal_component_type": "float32",
            "index_component_type": "uint32",
        },
        "vertex_count": parsed["vertex_count"],
        "index_count": len(indices),
        "triangle_count": parsed["triangle_count"],
        "parts": parsed["parts"],
        "bounds_mm": parsed["bounds_mm"],
        "source_to_preview_transform": {
            "scale": 0.1,
            "rotation_x_radians": -1.5707963267948966,
            "basis": "Rhino Z-up to Three.js Y-up",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return binary_path, manifest_path, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_obj", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    binary_path, manifest_path, manifest = write_asset(
        args.source_obj,
        args.receipt,
        args.output_dir,
    )
    print(json.dumps({
        "status": "ready",
        "binary": str(binary_path),
        "manifest": str(manifest_path),
        "source_receipt_sha256": manifest["source_receipt_sha256"],
        "parts": len(manifest["parts"]),
        "triangles": manifest["triangle_count"],
        "manufacturing_gates": manifest["manufacturing_gates"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
