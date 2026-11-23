#!/usr/bin/env python3
"""Build manifest/canonical documents for one receipt-backed geometry export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.validation import validate_job_spec
from scripts.geometry_bundle import read_json, validate_bundle, write_json_atomic


DEFAULT_PARTS = (
    ("band", "band.stl", "metal", 1),
    ("center_stone", "stone.stl", "diamond", 2),
    ("prongs", "prongs.stl", "metal", 3),
)
OPTIONAL_METAL_PARTS = (
    ("setting", "setting.stl"),
    ("gallery", "gallery.stl"),
    ("bezel", "bezel.stl"),
    ("basket", "basket.stl"),
)


def build_bundle_documents(bundle_dir: Path, parameters_path: Path) -> dict[str, Any]:
    flat = read_json(parameters_path)
    canonical = flat_to_canonical(flat)
    schema_result = validate_job_spec(canonical)
    if not schema_result.accepted:
        messages = "; ".join(issue.message for issue in schema_result.issues)
        raise ValueError(f"Adapter-generated canonical spec failed schema validation: {messages}")
    constraint_result = constraints.validate(canonical)
    if not constraint_result.accepted:
        messages = "; ".join(
            f"{issue.rule_id}: {issue.message}"
            for issue in constraint_result.hard_violations
        )
        raise ValueError(f"Adapter-generated canonical spec failed constraints: {messages}")

    receipt = read_json(bundle_dir / "export_receipt.json")
    job_id = canonical["job"]["job_id"]
    if receipt.get("job_id") != job_id:
        raise ValueError("Receipt job_id does not match adapter-generated canonical spec")

    parts = [
        {
            "role": role,
            "path": path,
            "material_role": material,
            "mask_class": mask_class,
        }
        for role, path, material, mask_class in DEFAULT_PARTS
    ]
    for role, filename in OPTIONAL_METAL_PARTS:
        if (bundle_dir / filename).is_file():
            parts.append(
                {
                    "role": role,
                    "path": filename,
                    "material_role": "metal",
                    "mask_class": 1,
                }
            )

    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "export_id": receipt["export_id"],
        "units": "mm",
        "coordinate_system": "Rhino_Z_up",
        "receipt": "export_receipt.json",
        "combined": {"path": "model.stl"},
        "parts": parts,
    }
    write_json_atomic(bundle_dir / "canonical_spec.json", canonical)
    write_json_atomic(bundle_dir / "geometry_manifest.json", manifest)
    return validate_bundle(bundle_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate an authoritative geometry bundle."
    )
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)

    report = build_bundle_documents(args.bundle_dir, args.parameters)
    if args.report_json:
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

