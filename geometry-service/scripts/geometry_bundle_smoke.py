#!/usr/bin/env python3
"""Run the local geometry-bundle/render-input smoke pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jewelry_gaas_foundation.stl_gates import (
    GateResult,
    check_mesh_closedness,
    check_stone_metal_collision,
    check_stone_stone_collision,
)
from scripts.geometry_bundle import read_json, validate_bundle, write_json_atomic
from scripts.render_geometry_manifest import expected_mask_values, load_render_manifest


def _gate_to_json(result: GateResult) -> dict[str, Any]:
    return asdict(result)


def run_geometry_bundle_smoke(
    bundle_dir: str | Path,
    *,
    triangle_tolerance_mm: float = 1e-5,
) -> dict[str, Any]:
    """Validate one bundle and prove it is consumable as per-part render input."""
    root = Path(bundle_dir)
    contract_report = validate_bundle(
        root,
        triangle_tolerance_mm=triangle_tolerance_mm,
    )
    raw_manifest = read_json(root / "geometry_manifest.json")
    render_manifest = load_render_manifest(root / "geometry_manifest.json")

    combined_path = root / str(raw_manifest["combined"]["path"])
    mesh_entries = [
        ("combined", combined_path),
        *((part.role, part.path) for part in render_manifest.parts),
    ]
    gate1_entries: list[dict[str, Any]] = []
    for role, path in mesh_entries:
        result = check_mesh_closedness(path)
        gate1_entries.append(
            {
                "role": role,
                "path": path.name,
                **_gate_to_json(result),
            }
        )
    gate1_status = "pass" if all(item["status"] == "pass" for item in gate1_entries) else "fail"

    stones = [part for part in render_manifest.parts if part.material_role == "diamond"]
    metals = [part for part in render_manifest.parts if part.material_role == "metal"]
    center_stone = next((part for part in render_manifest.parts if part.role == "center_stone"), None)
    band = next((part for part in render_manifest.parts if part.role == "band"), None)
    prongs = next((part for part in render_manifest.parts if part.role == "prongs"), None)
    extra_metals = [
        part.path
        for part in metals
        if part.role not in {"band", "prongs"}
    ]
    stone_metal = check_stone_metal_collision(
        center_stone.path if center_stone else None,
        band.path if band else None,
        prongs.path if prongs else None,
        metal_stl_paths=extra_metals,
    )
    stone_stone = check_stone_stone_collision([part.path for part in stones])

    hard_collision_failure = any(
        result.status == "fail"
        for result in (stone_metal, stone_stone)
    )
    status = (
        "pass"
        if contract_report.get("status") == "valid"
        and gate1_status == "pass"
        and not hard_collision_failure
        else "fail"
    )

    return {
        "status": status,
        "bundle_dir": str(root),
        "contract": contract_report,
        "gate_1_closedness": {
            "status": gate1_status,
            "meshes": gate1_entries,
        },
        "collision_gates": {
            "stone_metal_collision": _gate_to_json(stone_metal),
            "stone_stone_collision": _gate_to_json(stone_stone),
            "official_geometry_gate_state_modified": False,
        },
        "render_input": {
            "source_mode": "geometry_manifest",
            "manifest": str(root / "geometry_manifest.json"),
            "job_id": render_manifest.job_id,
            "export_id": render_manifest.export_id,
            "parts": [
                {
                    "role": part.role,
                    "path": part.path.name,
                    "material_role": part.material_role,
                    "mask_class": part.mask_class,
                    "object_name": part.object_name,
                }
                for part in render_manifest.parts
            ],
            "expected_mask_values": sorted(expected_mask_values(render_manifest)),
        },
        "official_geometry_gate_state_modified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--triangle-tolerance-mm", type=float, default=1e-5)
    args = parser.parse_args(argv)

    report = run_geometry_bundle_smoke(
        args.bundle_dir,
        triangle_tolerance_mm=args.triangle_tolerance_mm,
    )
    if args.report_json:
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
