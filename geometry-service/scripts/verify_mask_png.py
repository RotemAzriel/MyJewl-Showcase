#!/usr/bin/env python3
"""Verify that a semantic mask PNG contains only exact 8-bit class IDs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def verify_mask(
    path: Path,
    allowed: set[int],
    required_visible: set[int] | None = None,
) -> dict[str, object]:
    with Image.open(path) as opened:
        image = opened.convert("L")
        values = set(image.getdata())
    unexpected = values - allowed
    if unexpected:
        raise ValueError(
            f"Mask contains intermediate/unexpected values: {sorted(unexpected)}"
        )
    required = required_visible or set()
    missing_required = required - values
    if missing_required:
        raise ValueError(
            f"Mask is missing required visible class values: {sorted(missing_required)}"
        )
    return {
        "path": str(path),
        "mode": "L",
        "observed_values": sorted(values),
        "allowed_values": sorted(allowed),
        "required_visible_values": sorted(required),
        "missing_visible_classes": sorted(allowed - values - {0}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mask", type=Path)
    parser.add_argument("--allowed", default="0,1,2,3")
    parser.add_argument("--require-visible", default="")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    allowed = {int(value) for value in args.allowed.split(",") if value.strip()}
    required = {int(value) for value in args.require_visible.split(",") if value.strip()}
    report = verify_mask(args.mask, allowed, required_visible=required)
    if args.report_json:
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
