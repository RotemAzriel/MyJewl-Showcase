#!/usr/bin/env python3
"""Collect receipt-backed Rhino export directories into local bundle folders."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from scripts.build_geometry_bundle import build_bundle_documents
from scripts.geometry_bundle import read_json, write_json_atomic


def discover_export_dirs(source_root: str | Path) -> list[Path]:
    """Return directories under ``source_root`` containing export receipts."""

    root = Path(source_root)
    if (root / "export_receipt.json").is_file():
        return [root]
    return sorted({receipt.parent for receipt in root.rglob("export_receipt.json")})


def collect_exports(
    source_root: str | Path,
    config_dir: str | Path,
    dest_root: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy export dirs into local bundle dirs and build bundle documents."""

    source = Path(source_root)
    configs = Path(config_dir)
    destination = Path(dest_root)
    destination.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for export_dir in discover_export_dirs(source):
        result = _collect_one(export_dir, configs, destination, overwrite=overwrite)
        results.append(result)

    return {
        "source_root": str(source),
        "config_dir": str(configs),
        "dest_root": str(destination),
        "total": len(results),
        "collected": sum(1 for item in results if item["status"] == "collected"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }


def _collect_one(
    export_dir: Path,
    config_dir: Path,
    dest_root: Path,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    try:
        receipt = read_json(export_dir / "export_receipt.json")
        job_id = _required_string(receipt, "job_id")
        params_path = config_dir / job_id / "parameters.json"
        if not params_path.is_file():
            raise ValueError(f"parameters.json missing for {job_id}")

        dest_dir = dest_root / job_id
        if dest_dir.exists():
            if not overwrite:
                raise ValueError(f"destination already exists: {dest_dir}")
            shutil.rmtree(dest_dir)

        shutil.copytree(export_dir, dest_dir)
        shutil.copy2(params_path, dest_dir / "parameters.json")
        bundle_report = build_bundle_documents(dest_dir, dest_dir / "parameters.json")

        return {
            "status": "collected",
            "job_id": job_id,
            "export_dir": str(export_dir),
            "bundle_dir": str(dest_dir),
            "bundle_status": bundle_report["status"],
            "export_id": receipt.get("export_id"),
        }
    except Exception as exc:  # noqa: BLE001 - collector report boundary
        return {
            "status": "failed",
            "export_dir": str(export_dir),
            "reason": str(exc),
        }


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"receipt missing string field: {key}")
    return item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect receipt-backed Rhino exports into local bundle dirs."
    )
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--dest-root", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)

    report = collect_exports(
        args.source_root,
        args.config_dir,
        args.dest_root,
        overwrite=args.overwrite,
    )
    if args.report_json:
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
