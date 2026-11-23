#!/usr/bin/env python3
"""Audit the bounded Goal 0011 Rhino master and visual-review evidence.

This validator proves provenance, named-part closure metadata, file hashes,
fixed-view render linkage, and an explicit human visual decision.  It never
assigns a manufacturing pass/fail verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_PARTS = {
    "shank",
    "shoulder_front_L",
    "shoulder_rear_L",
    "shoulder_front_R",
    "shoulder_rear_R",
    "basket_lower_rail",
    "stone_seat_rail",
    "basket_petal_01",
    "basket_petal_02",
    "basket_petal_03",
    "basket_petal_04",
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
REQUIRED_ROLES = {"metal", "stone"}
REQUIRED_VIEWS = {"front_three_quarter", "side_profile", "top_stone_seating"}
REVIEW_CHECKS = {
    "no_unconnected_vertical_rods",
    "stone_visibly_seated_and_retained",
    "shoulders_head_and_gallery_read_as_one_object",
    "gallery_negative_space_is_plausible",
    "front_side_top_silhouettes_are_finished",
    "highlights_preserve_form",
    "contact_shadow_and_component_grounding_are_believable",
}
TEMPLATE_IDS = {
    "v1": "goal0011-realism-first-round-solitaire-master-v1",
    "v2": "goal0011-realism-first-round-solitaire-master-v2",
    "v4": "goal0011-realism-first-round-solitaire-master-v4",
}
TEMPLATE_ID = TEMPLATE_IDS["v1"]
SOURCE_OWNERSHIP = "myjewl_original_rhinocommon_source"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("JSON input must contain an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_hashes(parts: list[Any], master_dir: Path | None) -> dict[str, Any]:
    if master_dir is None:
        return {"checked": False, "ready": False, "reason": "master_dir_not_supplied", "files": []}
    root = master_dir.resolve()
    checks = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        relative = item.get("file")
        if not isinstance(name, str) or not isinstance(relative, str):
            checks.append({"name": name or "unnamed", "status": "malformed_metadata"})
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            checks.append({"name": name, "file": relative, "status": "path_escape"})
            continue
        if not candidate.is_file():
            checks.append({"name": name, "file": relative, "status": "missing"})
            continue
        expected = item.get("sha256")
        actual = _sha256(candidate)
        checks.append({
            "name": name,
            "file": relative,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "status": "match" if isinstance(expected, str) and expected == actual else "hash_mismatch",
        })
    return {
        "checked": True,
        "ready": len(checks) == len(parts) and all(item["status"] == "match" for item in checks),
        "reason": None,
        "files": checks,
    }


def _render_provenance(receipt_path: Path, render_path: Path | None) -> dict[str, Any]:
    if render_path is None:
        return {"checked": False, "ready": False, "reason": "render_evidence_not_supplied"}
    evidence = _load(render_path)
    views = evidence.get("views")
    view_names = set(views) if isinstance(views, dict) else set()
    checks = []
    if isinstance(views, dict):
        for name, item in sorted(views.items()):
            if not isinstance(item, dict) or not isinstance(item.get("file"), str):
                checks.append({"view": name, "status": "malformed_metadata"})
                continue
            candidate = (render_path.parent / item["file"]).resolve()
            try:
                candidate.relative_to(render_path.parent.resolve())
            except ValueError:
                checks.append({"view": name, "status": "path_escape"})
                continue
            if not candidate.is_file():
                checks.append({"view": name, "status": "missing"})
                continue
            actual = _sha256(candidate)
            checks.append({
                "view": name,
                "file": item["file"],
                "status": "match" if actual == item.get("sha256") else "hash_mismatch",
            })
    receipt_sha = _sha256(receipt_path)
    ready = (
        evidence.get("source_receipt_sha256") == receipt_sha
        and view_names == REQUIRED_VIEWS
        and len(checks) == len(REQUIRED_VIEWS)
        and all(item["status"] == "match" for item in checks)
        and evidence.get("resolution") == [2048, 2048]
        and evidence.get("authority") == "comparison_only_not_manufacturing"
    )
    return {
        "checked": True,
        "ready": ready,
        "reason": None if ready else "fixed_view_or_receipt_provenance_incomplete",
        "render_evidence": str(render_path),
        "render_evidence_sha256": _sha256(render_path),
        "receipt_sha256": receipt_sha,
        "declared_source_receipt_sha256": evidence.get("source_receipt_sha256"),
        "views": sorted(view_names),
        "file_checks": checks,
        "resolution": evidence.get("resolution"),
    }


def _visual_review(
    receipt_path: Path,
    render_path: Path | None,
    review_path: Path | None,
    render_ready: bool,
) -> dict[str, Any]:
    if review_path is None:
        return {"checked": False, "ready": False, "decision": "not_run", "reason": "visual_review_not_supplied"}
    review = _load(review_path)
    checks = review.get("checks")
    check_names = set(checks) if isinstance(checks, dict) else set()
    all_checks = (
        check_names == REVIEW_CHECKS
        and all(checks.get(name) is True for name in REVIEW_CHECKS)
    ) if isinstance(checks, dict) else False
    render_sha = _sha256(render_path) if render_path and render_path.is_file() else None
    approved = (
        render_ready
        and review.get("decision") == "approved"
        and review.get("source_receipt_sha256") == _sha256(receipt_path)
        and review.get("render_evidence_sha256") == render_sha
        and all_checks
        and isinstance(review.get("reviewer"), str)
        and bool(review.get("reviewer").strip())
    )
    return {
        "checked": True,
        "ready": approved,
        "decision": "approved" if approved else str(review.get("decision", "invalid")),
        "reason": None if approved else "human_visual_approval_incomplete",
        "review": str(review_path),
        "reviewer": review.get("reviewer"),
        "checks": checks,
    }


def _differential_proof(
    receipt_path: Path,
    evidence_path: Path | None,
    visual_ready: bool,
) -> dict[str, Any]:
    if evidence_path is None:
        return {
            "checked": False,
            "ready": False,
            "status": "not_run_until_visual_approval",
            "reason": "differential_evidence_not_supplied",
        }
    evidence = _load(evidence_path)
    after = evidence.get("after") if isinstance(evidence.get("after"), dict) else {}
    ready = (
        visual_ready
        and evidence.get("schema") == "myjewl.goal0011.receipt_differential.v1"
        and evidence.get("status") == "ready"
        and evidence.get("differential_ready") is True
        and evidence.get("ready_for_visual_reference_promotion") is True
        and after.get("receipt_sha256") == _sha256(receipt_path)
        and after.get("visual_approval_ready") is True
    )
    return {
        "checked": True,
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "reason": None if ready else "receipt_differential_incomplete",
        "evidence": str(evidence_path),
        "evidence_sha256": _sha256(evidence_path),
        "changed_parts": evidence.get("changed_parts"),
        "unchanged_parts": evidence.get("unchanged_parts"),
    }


def _browser_promotion(
    receipt_path: Path,
    differential: dict[str, Any],
    evidence_path: Path | None,
) -> dict[str, Any]:
    if evidence_path is None:
        return {
            "checked": False,
            "ready": False,
            "status": "not_run_until_visual_and_differential_approval",
            "reason": "browser_promotion_evidence_not_supplied",
        }
    evidence = _load(evidence_path)
    ready = (
        differential.get("ready") is True
        and evidence.get("schema") == "myjewl.goal0011.ui_promotion.v2"
        and evidence.get("status") == "ready"
        and evidence.get("target_receipt_sha256") == _sha256(receipt_path)
        and evidence.get("differential_evidence_sha256")
        == differential.get("evidence_sha256")
        and evidence.get("geometry_authority")
        == "receipt_backed_rhino_derived_browser_preview"
        and evidence.get("receipt_backed_browser_asset_ready") is True
        and evidence.get("manufacturing_gates")
        == "pending_not_run_needs_calibration"
    )
    return {
        "checked": True,
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "reason": None if ready else "browser_visual_reference_incomplete",
        "evidence": str(evidence_path),
        "evidence_sha256": _sha256(evidence_path),
        "catalog_revision": evidence.get("catalog_revision"),
        "geometry_authority": evidence.get("geometry_authority"),
    }


def audit_master(
    receipt: dict[str, Any],
    *,
    receipt_path: Path,
    master_dir: Path | None = None,
    render_evidence: Path | None = None,
    visual_review: Path | None = None,
    differential_evidence: Path | None = None,
    browser_promotion_evidence: Path | None = None,
    expected_template_id: str = TEMPLATE_ID,
) -> dict[str, Any]:
    parts = receipt.get("parts")
    if not isinstance(parts, list):
        raise ValueError("receipt.parts must be a list")
    names = [item.get("name") for item in parts if isinstance(item, dict) and isinstance(item.get("name"), str)]
    roles = {item.get("role") for item in parts if isinstance(item, dict) and isinstance(item.get("role"), str)}
    actual = set(names)
    malformed = len(names) != len(parts)
    duplicate = sorted(name for name in actual if names.count(name) > 1)
    template = receipt.get("template") if isinstance(receipt.get("template"), dict) else {}
    structural_ready = (
        not malformed
        and not duplicate
        and actual == EXPECTED_PARTS
        and roles == REQUIRED_ROLES
        and all(item.get("closed") is True for item in parts if isinstance(item, dict))
        and template.get("template_id") == expected_template_id
    )
    artifact_files = _file_hashes(parts, master_dir)
    render = _render_provenance(receipt_path, render_evidence)
    review = _visual_review(receipt_path, render_evidence, visual_review, render["ready"])
    differential = _differential_proof(
        receipt_path,
        differential_evidence,
        review["ready"],
    )
    promotion = _browser_promotion(
        receipt_path,
        differential,
        browser_promotion_evidence,
    )
    ownership_ready = template.get("source_ownership") == SOURCE_OWNERSHIP
    return {
        "schema": "myjewl.goal0011.master_audit.v1",
        "authority": "derived_review_evidence_only",
        "input_receipt": str(receipt_path),
        "expected_template_id": expected_template_id,
        "template_id": template.get("template_id"),
        "source_ownership": template.get("source_ownership"),
        "source_ownership_ready": ownership_ready,
        "part_count": len(parts),
        "expected_part_count": len(EXPECTED_PARTS),
        "parts": sorted(actual),
        "roles": sorted(roles),
        "closed_parts": sum(item.get("closed") is True for item in parts if isinstance(item, dict)),
        "missing_parts": sorted(EXPECTED_PARTS - actual),
        "extra_parts": sorted(actual - EXPECTED_PARTS),
        "duplicate_parts": duplicate,
        "structural_identity_ready": structural_ready,
        "artifact_files": artifact_files,
        "render_provenance": render,
        "visual_review": review,
        "master_visual_ready": structural_ready and artifact_files["ready"] and ownership_ready and review["ready"],
        "differential_geometry_proof": differential,
        "browser_promotion": promotion,
        "manufacturing_gates": receipt.get("manufacturing_gates") or "pending_not_run_needs_calibration",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--master-dir", type=Path)
    parser.add_argument("--render-evidence", type=Path)
    parser.add_argument("--visual-review", type=Path)
    parser.add_argument("--differential-evidence", type=Path)
    parser.add_argument("--browser-promotion-evidence", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--revision", choices=tuple(TEMPLATE_IDS), default="v1")
    args = parser.parse_args(argv)
    report = audit_master(
        _load(args.receipt),
        receipt_path=args.receipt,
        master_dir=args.master_dir,
        render_evidence=args.render_evidence,
        visual_review=args.visual_review,
        differential_evidence=args.differential_evidence,
        browser_promotion_evidence=args.browser_promotion_evidence,
        expected_template_id=TEMPLATE_IDS[args.revision],
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
