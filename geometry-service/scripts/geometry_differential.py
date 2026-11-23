#!/usr/bin/env python3
"""Compare receipt-backed STL hashes, bounds, and part counts across configs."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from scripts.geometry_bundle import inspect_mesh, read_json, write_json_atomic


def compare(bundle_dirs: list[Path]) -> dict[str, Any]:
    configs: list[dict[str, Any]] = []
    for bundle_dir in bundle_dirs:
        manifest = read_json(bundle_dir / "geometry_manifest.json")
        files: dict[str, Any] = {}
        paths = [str(manifest["combined"]["path"])] + [
            str(part["path"]) for part in manifest["parts"]
        ]
        for path_name in paths:
            facts = inspect_mesh(bundle_dir / path_name)
            files[path_name] = {
                "sha256": facts.sha256,
                "triangle_count": facts.face_count,
                "bounds_mm": [list(row) for row in facts.bounds_mm],
            }
        configs.append({"job_id": manifest["job_id"], "files": files})

    pairs: list[dict[str, Any]] = []
    for left, right in combinations(configs, 2):
        changed: dict[str, bool] = {}
        for filename in left["files"]:
            changed[filename] = left["files"][filename] != right["files"].get(filename)
        pairs.append({
            "left": left["job_id"],
            "right": right["job_id"],
            "changed_files": [name for name, changed_value in changed.items() if changed_value],
            "all_files_changed": all(changed.values()),
        })
    return {
        "status": "pass" if all(pair["changed_files"] for pair in pairs) else "fail",
        "config_count": len(configs),
        "configs": configs,
        "pairs": pairs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dirs", nargs="+", type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    report = compare(args.bundle_dirs)
    if args.report_json:
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
