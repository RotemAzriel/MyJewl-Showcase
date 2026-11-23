#!/usr/bin/env python3
"""Measure Goal 0011 fixed-view highlight clipping against its baseline.

The report is presentation evidence only. It cannot replace the required
human visual decision and does not assign a geometry or manufacturing verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


REQUIRED_VIEWS = {"front_three_quarter", "side_profile", "top_stone_seating"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metrics(path: Path) -> dict[str, Any]:
    pixels = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    luminance = 0.2126 * pixels[:, :, 0] + 0.7152 * pixels[:, :, 1] + 0.0722 * pixels[:, :, 2]
    return {
        "file": str(path),
        "sha256": sha256(path),
        "size_px": [int(pixels.shape[1]), int(pixels.shape[0])],
        "pixel_count": int(luminance.size),
        "rgb_all_ge_250_fraction": float(np.mean(np.all(pixels >= 250.0 / 255.0, axis=2))),
        "luminance_ge_0_98_fraction": float(np.mean(luminance >= 0.98)),
        "luminance_p99": float(np.quantile(luminance, 0.99)),
        "luminance_p999": float(np.quantile(luminance, 0.999)),
    }


def build_report(baseline: Path, render_evidence: Path) -> dict[str, Any]:
    evidence = json.loads(render_evidence.read_text(encoding="utf-8"))
    views = evidence.get("views")
    if not isinstance(views, dict) or set(views) != REQUIRED_VIEWS:
        raise ValueError("render evidence must contain exactly the three Goal 0011 fixed views")
    baseline_metrics = image_metrics(baseline)
    view_metrics = {}
    hashes_ready = True
    for name, item in sorted(views.items()):
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("malformed view metadata: {}".format(name))
        path = (render_evidence.parent / item["file"]).resolve()
        metrics = image_metrics(path)
        metrics["declared_sha256"] = item.get("sha256")
        metrics["hash_match"] = metrics["sha256"] == item.get("sha256")
        hashes_ready = hashes_ready and metrics["hash_match"]
        view_metrics[name] = metrics
    max_goal_clipping = max(item["luminance_ge_0_98_fraction"] for item in view_metrics.values())
    baseline_clipping = baseline_metrics["luminance_ge_0_98_fraction"]
    reduction = baseline_clipping - max_goal_clipping
    ratio = baseline_clipping / max_goal_clipping if max_goal_clipping else None
    controlled = hashes_ready and max_goal_clipping < 0.01 and reduction > 0.0
    return {
        "schema": "myjewl.goal0011.render_quality.v1",
        "authority": "presentation_metrics_only_not_visual_or_manufacturing_approval",
        "render_evidence": str(render_evidence),
        "render_evidence_sha256": sha256(render_evidence),
        "source_receipt_sha256": evidence.get("source_receipt_sha256"),
        "baseline": baseline_metrics,
        "views": view_metrics,
        "hashes_ready": hashes_ready,
        "max_goal0011_luminance_clipping_fraction": max_goal_clipping,
        "baseline_luminance_clipping_fraction": baseline_clipping,
        "absolute_clipping_reduction": reduction,
        "clipping_reduction_ratio": ratio,
        "controlled_highlights_presentation_check": controlled,
        "human_visual_review": "not_run",
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("render_evidence", type=Path)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    report = build_report(args.baseline, args.render_evidence)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
