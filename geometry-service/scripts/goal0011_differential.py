#!/usr/bin/env python3
"""Prove the receipt-backed V2-to-V4 Goal 0011 head differential.

This validator is deliberately bounded to the approved 6.5 mm round / US 6.5
master. It requires the intended shoulder and basket-petal parts to change and
every unrelated receipt-listed part to remain byte-identical. The result is
visual-reference evidence only and never certifies manufacturing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


V2_TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v2"
V4_TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"
EXPECTED_CHANGED_PARTS = {
    "shoulder_front_L",
    "shoulder_rear_L",
    "shoulder_front_R",
    "shoulder_rear_R",
    "basket_petal_01",
    "basket_petal_02",
    "basket_petal_03",
    "basket_petal_04",
}
EXPECTED_UNCHANGED_PARTS = {
    "shank",
    "basket_lower_rail",
    "stone_seat_rail",
    "center_stone_round_brilliant",
    "prong_01",
    "prong_02",
    "prong_03",
    "prong_04",
    "prong_tip_01",
    "prong_tip_02",
    "prong_tip_03",
    "prong_tip_04",
}
EXPECTED_PARTS = EXPECTED_CHANGED_PARTS | EXPECTED_UNCHANGED_PARTS
REVIEW_CHECKS = {
    "no_unconnected_vertical_rods",
    "stone_visibly_seated_and_retained",
    "shoulders_head_and_gallery_read_as_one_object",
    "gallery_negative_space_is_plausible",
    "front_side_top_silhouettes_are_finished",
    "highlights_preserve_form",
    "contact_shadow_and_component_grounding_are_believable",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parts(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = receipt.get("parts")
    if not isinstance(raw, list):
        raise ValueError("receipt.parts must be a list")
    parts: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("every receipt part must be a named object")
        name = item["name"]
        if name in parts:
            raise ValueError(f"duplicate receipt part: {name}")
        parts[name] = item
    return parts


def compare(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_path: Path,
    after_path: Path,
    visual_review_path: Path,
) -> dict[str, Any]:
    before_parts = _parts(before)
    after_parts = _parts(after)
    review = _load(visual_review_path)
    before_names = set(before_parts)
    after_names = set(after_parts)
    shared_contract = before_names == after_names == EXPECTED_PARTS

    changed = sorted(
        name
        for name in before_names & after_names
        if before_parts[name].get("sha256") != after_parts[name].get("sha256")
    )
    unchanged = sorted((before_names & after_names) - set(changed))
    closed_and_role_stable = shared_contract and all(
        before_parts[name].get("closed") is True
        and after_parts[name].get("closed") is True
        and before_parts[name].get("role") == after_parts[name].get("role")
        for name in EXPECTED_PARTS
    )
    hashes_present = shared_contract and all(
        isinstance(before_parts[name].get("sha256"), str)
        and len(before_parts[name]["sha256"]) == 64
        and isinstance(after_parts[name].get("sha256"), str)
        and len(after_parts[name]["sha256"]) == 64
        for name in EXPECTED_PARTS
    )
    template_pair_ready = (
        (before.get("template") or {}).get("template_id") == V2_TEMPLATE_ID
        and (after.get("template") or {}).get("template_id") == V4_TEMPLATE_ID
    )
    after_sha = _sha256(after_path)
    checks = review.get("checks")
    visual_approval_ready = (
        review.get("decision") == "approved"
        and review.get("source_receipt_sha256") == after_sha
        and isinstance(checks, dict)
        and set(checks) == REVIEW_CHECKS
        and all(checks.values())
    )
    differential_ready = (
        shared_contract
        and closed_and_role_stable
        and hashes_present
        and template_pair_ready
        and set(changed) == EXPECTED_CHANGED_PARTS
        and set(unchanged) == EXPECTED_UNCHANGED_PARTS
    )

    return {
        "schema": "myjewl.goal0011.receipt_differential.v1",
        "authority": "receipt_backed_visual_reference_evidence_not_manufacturing",
        "status": "ready" if differential_ready and visual_approval_ready else "not_ready",
        "bounded_reference": {
            "stone_shape": "round",
            "stone_dimensions_mm": [6.5, 6.5, 3.9],
            "ring_size_us": 6.5,
        },
        "before": {
            "template_id": (before.get("template") or {}).get("template_id"),
            "receipt": str(before_path),
            "receipt_sha256": _sha256(before_path),
            "visual_decision": "rejected_as_too_similar_to_v1",
        },
        "after": {
            "template_id": (after.get("template") or {}).get("template_id"),
            "receipt": str(after_path),
            "receipt_sha256": after_sha,
            "visual_review": str(visual_review_path),
            "visual_review_sha256": _sha256(visual_review_path),
            "visual_approval_ready": visual_approval_ready,
        },
        "part_contract_ready": shared_contract,
        "closed_and_role_stable": closed_and_role_stable,
        "hashes_present": hashes_present,
        "template_pair_ready": template_pair_ready,
        "changed_parts": changed,
        "expected_changed_parts": sorted(EXPECTED_CHANGED_PARTS),
        "unchanged_parts": unchanged,
        "expected_unchanged_parts": sorted(EXPECTED_UNCHANGED_PARTS),
        "differential_ready": differential_ready,
        "ready_for_visual_reference_promotion": (
            differential_ready and visual_approval_ready
        ),
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before_receipt", type=Path)
    parser.add_argument("after_receipt", type=Path)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)
    report = compare(
        _load(args.before_receipt),
        _load(args.after_receipt),
        before_path=args.before_receipt,
        after_path=args.after_receipt,
        visual_review_path=args.visual_review,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
