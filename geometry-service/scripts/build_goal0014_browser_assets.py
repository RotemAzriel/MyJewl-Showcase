#!/usr/bin/env python3
"""Build one browser mesh per receipt-backed Goal 0014 V5 configuration.

Goal 0015. The Goal 0011 builder emits a single asset from a single receipt
because the V4 master had exactly one configuration to emit. The V5 master is
parametric, so this builder walks the received Azure exports and produces one
independent asset per solved configuration.

Each asset preserves every OBJ position, normal, triangle, named-part range, and
receipt role for its own configuration. It authors no jewelry geometry and
assigns no manufacturing verdict. The container format (magic ``MYJV4B1\\0`` and
delivery-manifest schema ``myjewl.goal0011.receipt_backed_browser_mesh.v1``) is
the one the pinned Three.js r166 client parses; that string names the *format*,
not the source goal, and provenance is carried by ``template_id`` and the
receipt hash instead.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from array import array
from pathlib import Path
from typing import Any

from scripts.build_goal0011_browser_asset import MAGIC, parse_obj, sha256

CONTAINER_SCHEMA = "myjewl.goal0011.receipt_backed_browser_mesh.v1"
RECEIPT_SCHEMA = "myjewl.goal0014.parametric_master_receipt.v5"
TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"
RECEIPT_NAME = "solitaire_parametric_master_v5_receipt.json"
OBJ_NAME = "solitaire_parametric_master_v5.obj"
ASSET_STEM = "solitaire_parametric_master_v5"


def load_receipt(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("receipt must contain a JSON object")
    if value.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("expected a Goal 0014 parametric master V5 receipt")
    if value.get("template", {}).get("template_id") != TEMPLATE_ID:
        raise ValueError("receipt template does not match the V5 master")
    if value.get("manufacturing_gates") != "pending_not_run_needs_calibration":
        raise ValueError("receipt manufacturing boundary changed unexpectedly")
    if len(value.get("parts") or ()) != 20:
        raise ValueError("receipt does not list the expected 20 named parts")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    return float(value)


def configuration_of(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the typed configuration this receipt was solved for.

    The V5 receipt records the request it actually received, which is the whole
    point of the Goal 0014 fix. Reading the configuration back out of the receipt
    (rather than a sidecar request file) keeps the asset bound to the geometry
    that was really produced.
    """
    spec = (receipt.get("request") or {}).get("canonical_spec")
    if not isinstance(spec, dict):
        raise ValueError("receipt records no canonical_spec request")
    ring = spec.get("ring")
    stones = spec.get("stones")
    if not isinstance(ring, dict) or not isinstance(stones, list) or len(stones) != 1:
        raise ValueError("V5 assets require one ring and exactly one center stone")
    stone = stones[0]
    dimensions = stone.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        raise ValueError("center stone carries no dimensions_mm")
    if ring.get("size_standard") != "US" or stone.get("shape") != "round":
        raise ValueError("the V5 master is bounded to US sizes and round center stones")
    return {
        "ring": {
            "size_standard": "US",
            "size": _number(ring.get("size"), "ring.size"),
            "band_width_mm": _number(ring.get("band_width_mm"), "ring.band_width_mm"),
            "band_thickness_mm": _number(
                ring.get("band_thickness_mm"), "ring.band_thickness_mm"
            ),
        },
        "center_stone": {
            "shape": "round",
            "length_mm": _number(dimensions.get("length"), "stone length"),
            "width_mm": _number(dimensions.get("width"), "stone width"),
            "depth_mm": _number(dimensions.get("depth"), "stone depth"),
        },
    }


def _decimal_token(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def config_slug(configuration: dict[str, Any]) -> str:
    """Return a deterministic, filesystem-safe id for one solved configuration."""
    size = _decimal_token(configuration["ring"]["size"])
    diameter = _decimal_token(configuration["center_stone"]["length_mm"])
    return f"us{size}_round{diameter}"


def write_asset(
    source_obj: Path,
    receipt_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    receipt = load_receipt(receipt_path)
    configuration = configuration_of(receipt)
    slug = config_slug(configuration)
    parsed = parse_obj(source_obj, receipt)
    output_dir.mkdir(parents=True, exist_ok=True)
    binary_name = f"{ASSET_STEM}_{slug}.meshbin"
    manifest_name = f"{ASSET_STEM}_{slug}.browser_mesh.json"
    binary_path = output_dir / binary_name
    manifest_path = output_dir / manifest_name

    index_count = _write_binary(binary_path, parsed)

    manifest = {
        "schema": CONTAINER_SCHEMA,
        "authority": "receipt_backed_rhino_derived_browser_preview",
        "manufacturing_authority": False,
        "manufacturing_gates": "pending_not_run_needs_calibration",
        "template_id": TEMPLATE_ID,
        "configuration_id": slug,
        "configuration": configuration,
        "derivation_version": receipt.get("derivation_version"),
        "source": "Azure Rhino.Compute named-part OBJ",
        "source_obj": source_obj.name,
        "source_obj_sha256": sha256(source_obj),
        "source_receipt": receipt_path.name,
        "source_receipt_sha256": sha256(receipt_path),
        "binary": {
            "file": binary_name,
            "sha256": sha256(binary_path),
            "byte_size": binary_path.stat().st_size,
            "header_bytes": 16,
            "position_component_type": "float32",
            "normal_component_type": "float32",
            "index_component_type": "uint32",
        },
        "vertex_count": parsed["vertex_count"],
        "index_count": index_count,
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
    return {
        "configuration_id": slug,
        "binary_path": binary_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
    }


def _write_binary(binary_path: Path, parsed: dict[str, Any]) -> int:
    """Write the meshbin container and return its index count."""
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
    return len(indices)


def build_all(received_root: Path, output_dir: Path) -> list[dict[str, Any]]:
    """Build one asset per received configuration directory, in slug order."""
    directories = sorted(
        path
        for path in Path(received_root).iterdir()
        if path.is_dir() and (path / RECEIPT_NAME).is_file()
    )
    if not directories:
        raise ValueError(f"no V5 configuration exports under {received_root}")
    results = [
        write_asset(directory / OBJ_NAME, directory / RECEIPT_NAME, output_dir)
        for directory in directories
    ]
    slugs = [result["configuration_id"] for result in results]
    if len(set(slugs)) != len(slugs):
        raise ValueError("two received configurations produced the same slug")
    return sorted(results, key=lambda result: result["configuration_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "received_root",
        type=Path,
        help="directory holding one subdirectory per received V5 configuration",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    results = build_all(args.received_root, args.output_dir)
    print(json.dumps({
        "status": "ready",
        "assets": [
            {
                "configuration_id": result["configuration_id"],
                "binary": str(result["binary_path"]),
                "manifest": str(result["manifest_path"]),
                "source_receipt_sha256": result["manifest"]["source_receipt_sha256"],
                "parts": len(result["manifest"]["parts"]),
                "triangles": result["manifest"]["triangle_count"],
            }
            for result in results
        ],
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
