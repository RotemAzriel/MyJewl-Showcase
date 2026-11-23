#!/usr/bin/env python3
"""Record the Stage-0 Claude-vision baseline — gate G-A (Goal 0005, plan §4/§6).

G-A has no bar. Its job is to record the number retrieval has to beat: run the
zero-shot vision extractor over a labelled holdout, through the same live gate,
and report per-attribute accuracy plus how often it abstains.

Scored attributes are the mounting axes the extractor is allowed to report —
setting style, prong count (and "when visible"), band-width ratio, band-thickness
ratio. Metal is a typed input (colour + karat/purity): it is passed *in* and never
scored, so there is no metal-colour accuracy number here.

Runs on whichever holdout is supplied — the normalized real mini-holdout from
``curate_real_holdout``, or the synthetic query renders (useful for wiring, and
honest about being synthetic). Costs Anthropic credit only when
``--live`` is passed; the default offline extractor spends nothing and is
labelled as a floor, not a baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fast_mvp.image_extractor import (  # noqa: E402
    DEFAULT_VISION_MODEL,
    extract_mounting,
    load_image,
)


def _mean(values: Iterable[float]) -> float | None:
    collected = [float(value) for value in values]
    return sum(collected) / len(collected) if collected else None


def _cases_from_holdout(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    return [
        {
            "case_id": str(item["item_id"]),
            "image_path": root / str(item["image_path"]),
            "typed": dict(item.get("typed") or {}),
            "labels": dict(item.get("labels") or {}),
            "supported": bool(item.get("supported")),
            "prong_count_visible": bool(item.get("prong_count_visible", True)),
        }
        for item in payload.get("items", [])
    ]


def _cases_from_renders(path: Path, split: str = "query") -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    cases: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        if record.get("split") != split:
            continue
        cases.append(
            {
                "case_id": f"{record['config_id']}#{record['view_index']}",
                "image_path": root / str(record["image_path"]),
                "typed": {
                    "stone_shape": record["stone_shape"],
                    "stone_length_mm": record["stone_length_mm"],
                    "stone_width_mm": record["stone_width_mm"],
                    "stone_depth_mm": record["stone_depth_mm"],
                    "ring_size_us": record["ring_size_us"],
                    # Typed metal input; never a target (see the module docstring).
                    "metal_alloy": record.get("metal_alloy"),
                },
                "labels": {
                    "center_setting": record["center_setting"],
                    "prong_count": record["prong_count"],
                    "band_width_ratio": record["band_width_ratio"],
                    **(
                        {"band_thickness_ratio": record["band_thickness_ratio"]}
                        if record.get("band_thickness_ratio") is not None
                        else {}
                    ),
                },
                "supported": True,
                "prong_count_visible": True,
            }
        )
    return cases


def run_baseline(
    cases: list[dict[str, Any]],
    *,
    live: bool,
    model: str,
    limit: int | None = None,
) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    setting_hits: list[float] = []
    prong_hits: list[float] = []
    prong_visible_hits: list[float] = []
    ratio_errors: list[float] = []
    thickness_errors: list[float] = []
    validity: list[float] = []
    statuses: dict[str, int] = defaultdict(int)
    abstentions: dict[str, int] = defaultdict(int)
    per_case: list[dict[str, Any]] = []
    started = time.monotonic()

    for case in selected:
        try:
            image_bytes, media_type = load_image(case["image_path"])
        except Exception as exc:  # noqa: BLE001 - per-case resilience
            per_case.append({"case_id": case["case_id"], "error": str(exc)})
            continue
        report = extract_mounting(
            dict(case["typed"]),
            image_bytes=image_bytes,
            media_type=media_type,
            model=model,
            allow_llm=live,
        )
        statuses[report["status"]] += 1
        if report.get("abstention_reason"):
            abstentions[report["abstention_reason"]] += 1

        observation = report["observation"]
        labels = case["labels"]
        if case["supported"] and labels:
            if "center_setting" in labels:
                setting_hits.append(
                    1.0 if observation["center_setting"] == labels["center_setting"] else 0.0
                )
            if "prong_count" in labels:
                hit = 1.0 if int(observation["prong_count"]) == int(labels["prong_count"]) else 0.0
                prong_hits.append(hit)
                if case.get("prong_count_visible", True):
                    prong_visible_hits.append(hit)
            if "band_width_ratio" in labels:
                ratio_errors.append(
                    abs(float(observation["band_width_ratio"]) - float(labels["band_width_ratio"]))
                )
            if "band_thickness_ratio" in labels:
                thickness_errors.append(
                    abs(
                        float(observation["band_thickness_ratio"])
                        - float(labels["band_thickness_ratio"])
                    )
                )
        if report["status"] == "accepted":
            gate = report.get("validation") or {}
            validity.append(
                1.0 if gate.get("schema_accepted") and gate.get("constraint_accepted") else 0.0
            )
        per_case.append(
            {
                "case_id": case["case_id"],
                "status": report["status"],
                "provider": report["provider"],
                "observation": observation,
                "abstention_reason": report.get("abstention_reason"),
            }
        )

    providers = {item.get("provider") for item in per_case if item.get("provider")}
    return {
        "gate": "G-A",
        "verdict": "recorded",
        "bar": None,
        "note": (
            "G-A has no bar; it records the zero-shot Claude-vision baseline that "
            "retrieval must beat."
        ),
        "live": live,
        "model": model if live else None,
        "providers_used": sorted(providers),
        "is_baseline": live and providers == {model},
        "caveat": (
            None
            if live and providers == {model}
            else "Offline extractor used: this is a deterministic floor, not the "
            "Claude-vision baseline. Re-run with --live and credentials for G-A."
        ),
        "counts": {"cases": len(selected), "scored": len(per_case)},
        "statuses": dict(statuses),
        "abstentions": dict(abstentions),
        # Mounting axes only. Metal is a typed input, so there is nothing to score.
        "attributes": {
            "setting_top1": _mean(setting_hits),
            "prong_exact": _mean(prong_hits),
            "prong_when_visible": _mean(prong_visible_hits),
            "band_width_ratio_mae": _mean(ratio_errors),
            "band_thickness_ratio_mae": _mean(thickness_errors),
        },
        "validity": {"emitted_configs": len(validity), "gate_pass_rate": _mean(validity)},
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "cases": per_case,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--holdout", type=Path, help="Normalized real mini-holdout.")
    source.add_argument("--render-manifest", type=Path, help="Synthetic render manifest.")
    parser.add_argument("--split", default="query")
    parser.add_argument("--model", default=DEFAULT_VISION_MODEL)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the Anthropic API (spends credit). Default is the offline floor.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report-json", type=Path, default=None)
    args = parser.parse_args(argv)

    cases = (
        _cases_from_holdout(args.holdout)
        if args.holdout
        else _cases_from_renders(args.render_manifest, args.split)
    )
    report = run_baseline(cases, live=args.live, model=args.model, limit=args.limit)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
