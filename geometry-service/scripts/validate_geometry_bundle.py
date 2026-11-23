#!/usr/bin/env python3
"""Validate one receipt-backed Rhino geometry bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.geometry_bundle import validate_bundle, write_json_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--triangle-tolerance-mm", type=float, default=1e-5)
    args = parser.parse_args(argv)

    report = validate_bundle(
        args.bundle_dir,
        triangle_tolerance_mm=args.triangle_tolerance_mm,
    )
    if args.report_json:
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

