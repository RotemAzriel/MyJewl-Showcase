#!/usr/bin/env python3
"""CLI wrapper for the MyJewl mounting-only extractor."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

from fast_mvp.pipeline import analyze_photo_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract mounting parameters while preserving a typed stone spec."
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--stone-shape", required=True)
    parser.add_argument("--stone-length-mm", required=True, type=float)
    parser.add_argument("--stone-width-mm", required=True, type=float)
    parser.add_argument("--stone-depth-mm", required=True, type=float)
    parser.add_argument("--ring-size-us", required=True, type=float)
    parser.add_argument("--stone-carat-eq", type=float)
    parser.add_argument("--setting", choices=("prong", "bezel", "basket"))
    parser.add_argument("--prong-count", type=int)
    # Typed inputs, not mounting overrides: metal never bypasses inference.
    parser.add_argument("--metal-family", choices=("yellow", "white", "rose"))
    parser.add_argument("--metal-karat", choices=("14k", "18k", "pt950"))
    parser.add_argument("--band-width-mm", type=float)
    parser.add_argument("--band-thickness-mm", type=float)
    parser.add_argument("--output", type=Path, default=Path("parameters.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        image_bytes = args.image.read_bytes()
        mime = mimetypes.guess_type(args.image.name)[0] or "image/jpeg"
        payload: dict[str, Any] = {
            "image_data_url": (
                f"data:{mime};base64,"
                + base64.b64encode(image_bytes).decode("ascii")
            ),
            "filename": args.image.name,
            "stone_shape": args.stone_shape,
            "stone_length_mm": args.stone_length_mm,
            "stone_width_mm": args.stone_width_mm,
            "stone_depth_mm": args.stone_depth_mm,
            "ring_size_us": args.ring_size_us,
            "stone_carat_eq": args.stone_carat_eq,
            "metal_color_family": args.metal_family,
            "metal_karat": args.metal_karat,
            "mounting_overrides": {
                "center_setting": args.setting,
                "prong_count": args.prong_count,
                "band_width_mm": args.band_width_mm,
                "band_thickness_mm": args.band_thickness_mm,
            },
        }
        result = analyze_photo_request(payload)
        if not result["validation"]["schema_accepted"]:
            raise ValueError("The generated flat parameters failed schema conversion.")
        if not result["validation"]["constraint_accepted"]:
            violations = result["validation"]["hard_violations"]
            summary = "; ".join(item["rule_id"] for item in violations) or "unknown"
            raise ValueError(f"The generated spec has hard constraint violations: {summary}.")
        args.output.write_text(
            json.dumps(result["flat_parameters"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"extract_params: {exc}", file=sys.stderr)
        return 2

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
