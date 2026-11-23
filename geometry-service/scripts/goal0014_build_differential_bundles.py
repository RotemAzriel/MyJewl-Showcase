#!/usr/bin/env python3
"""Turn received Goal 0014 per-config Rhino exports into config-differential bundles.

The Goal 0014 master writes one directory per configuration holding 20 named binary
STL parts plus its receipt. ``scripts/geometry_differential.py`` compares bundles
that carry a ``geometry_manifest.json`` with a ``combined`` mesh and a ``parts``
list, so this adapter writes that manifest and builds ``model.stl`` as the exact
concatenation of the part triangles -- the same "combined == union of parts"
convention the existing bundle validator enforces.

This is packaging of receipt-backed exports, not geometry authoring. Rhino remains
the geometry authority and nothing here certifies manufacturing.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any

STL_HEADER_BYTES = 80
STL_TRIANGLE_BYTES = 50


def read_binary_stl_triangles(path: Path) -> bytes:
    """Return the raw triangle records of a binary STL, without its header."""
    data = path.read_bytes()
    if len(data) < STL_HEADER_BYTES + 4:
        raise ValueError(f"{path} is too short to be a binary STL")
    count = struct.unpack("<I", data[STL_HEADER_BYTES : STL_HEADER_BYTES + 4])[0]
    body = data[STL_HEADER_BYTES + 4 :]
    expected = count * STL_TRIANGLE_BYTES
    if len(body) != expected:
        raise ValueError(
            f"{path} declares {count} triangles ({expected} B) but carries {len(body)} B"
        )
    return body


def write_combined_stl(destination: Path, part_paths: list[Path], label: str) -> int:
    """Concatenate part triangles into one binary STL and return the triangle count."""
    bodies = [read_binary_stl_triangles(path) for path in part_paths]
    total = sum(len(body) // STL_TRIANGLE_BYTES for body in bodies)
    header = ("MyJewl Goal0014 combined " + label).encode("ascii", "replace")[
        :STL_HEADER_BYTES
    ].ljust(STL_HEADER_BYTES, b"\0")
    with destination.open("wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", total))
        for body in bodies:
            stream.write(body)
    return total


def build_bundle(config_dir: Path, receipt_name: str) -> dict[str, Any]:
    receipt_path = config_dir / receipt_name
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    parts = receipt["parts"]
    if not parts:
        raise ValueError(f"{receipt_path} lists no parts")

    part_paths = []
    manifest_parts = []
    for index, part in enumerate(parts):
        filename = part["file"]
        path = config_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"receipt part missing on disk: {path}")
        part_paths.append(path)
        manifest_parts.append(
            {
                "role": part["name"],
                "path": filename,
                "material_role": part["role"],
                "mask_class": 2 if part["role"] == "stone" else 1,
            }
        )

    job_id = receipt["job_id"]
    triangles = write_combined_stl(config_dir / "model.stl", part_paths, job_id)
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "export_id": receipt.get("receipt_sha256", job_id),
        "units": "mm",
        "coordinate_system": "Rhino_Z_up",
        "receipt": receipt_name,
        "combined": {"path": "model.stl"},
        "parts": manifest_parts,
        "authority": "receipt_backed_rhino_export_not_manufacturing",
    }
    (config_dir / "geometry_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "bundle_dir": str(config_dir),
        "job_id": job_id,
        "part_count": len(manifest_parts),
        "combined_triangles": triangles,
        "closed_parts": sum(1 for part in parts if part.get("closed") is True),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_dirs", nargs="+", type=Path)
    parser.add_argument(
        "--receipt-name", default="solitaire_parametric_master_v5_receipt.json"
    )
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)

    summary = {
        "schema": "myjewl.goal0014.differential_bundles.v1",
        "bundles": [build_bundle(item, args.receipt_name) for item in args.config_dirs],
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
