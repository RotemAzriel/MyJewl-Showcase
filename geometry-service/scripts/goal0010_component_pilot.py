#!/usr/bin/env python3
"""Audit readiness of a bounded Goal 0009 solitaire component master.

This script consumes derived Rhino receipt evidence. It never edits geometry,
assigns manufacturing verdicts, or promotes the input into product authority.
Its report is a handoff/checklist for the Goal 0010 professional-authoring
pilot.
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


def _load_receipt(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("receipt must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_artifact_files(parts: list[Any], master_dir: Path | None) -> dict[str, Any]:
    if master_dir is None:
        return {"checked": False, "ready": False, "reason": "master_dir_not_supplied", "files": []}

    checks: list[dict[str, Any]] = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        relative_file = item.get("file")
        if not isinstance(name, str) or not isinstance(relative_file, str):
            checks.append({"name": name or "unnamed", "status": "malformed_metadata"})
            continue
        candidate = (master_dir / relative_file).resolve()
        try:
            candidate.relative_to(master_dir.resolve())
        except ValueError:
            checks.append({"name": name, "file": relative_file, "status": "path_escape"})
            continue
        expected = item.get("sha256")
        if not candidate.is_file():
            checks.append({"name": name, "file": relative_file, "status": "missing"})
            continue
        actual = _sha256(candidate)
        checks.append({
            "name": name,
            "file": relative_file,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "status": "match" if isinstance(expected, str) and actual == expected else "hash_mismatch",
        })
    return {
        "checked": True,
        "ready": bool(checks) and all(item["status"] == "match" for item in checks),
        "reason": None,
        "files": checks,
    }


def _audit_render_evidence(receipt_path: Path, render_evidence: Path | None) -> dict[str, Any]:
    if render_evidence is None:
        return {"checked": False, "ready": False, "reason": "render_evidence_not_supplied"}
    evidence = _load_receipt(render_evidence)
    receipt_sha = _sha256(receipt_path)
    declared = evidence.get("source_receipt_sha256")
    return {
        "checked": True,
        "ready": declared == receipt_sha,
        "reason": None if declared == receipt_sha else "receipt_hash_mismatch",
        "render_evidence": str(render_evidence),
        "receipt_sha256": receipt_sha,
        "declared_source_receipt_sha256": declared,
        "authority": evidence.get("authority"),
        "schema": evidence.get("schema"),
    }


def audit_receipt(
    receipt: dict[str, Any],
    *,
    receipt_path: Path,
    master_dir: Path | None = None,
    render_evidence: Path | None = None,
) -> dict[str, Any]:
    parts = receipt.get("parts")
    if not isinstance(parts, list):
        raise ValueError("receipt.parts must be a list")

    names: list[str] = []
    roles: set[str] = set()
    closed_count = 0
    malformed: list[str] = []
    for item in parts:
        if not isinstance(item, dict):
            malformed.append("non-object part entry")
            continue
        name = item.get("name")
        role = item.get("role")
        if not isinstance(name, str) or not isinstance(role, str):
            malformed.append(str(name or "unnamed"))
            continue
        names.append(name)
        roles.add(role)
        if item.get("closed") is True:
            closed_count += 1

    actual = set(names)
    duplicate_names = sorted(name for name in actual if names.count(name) > 1)
    missing = sorted(EXPECTED_PARTS - actual)
    extra = sorted(actual - EXPECTED_PARTS)
    receipt_authority = receipt.get("authority")
    manufacturing_gates = receipt.get("manufacturing_gates")

    structural_ready = not malformed and not duplicate_names and not missing and not extra
    structural_ready = structural_ready and roles == REQUIRED_ROLES and closed_count == len(parts)
    artifact_files = _audit_artifact_files(parts, master_dir)
    render_provenance = _audit_render_evidence(receipt_path, render_evidence)

    return {
        "schema": "myjewl.goal0010.component_pilot.v2",
        "authority": "derived_review_evidence_only",
        "input_receipt": str(receipt_path),
        "source_authority": receipt_authority,
        "generated_by": receipt.get("generated_by"),
        "job_id": receipt.get("job_id"),
        "template_id": receipt.get("template_id"),
        "part_count": len(parts),
        "expected_part_count": len(EXPECTED_PARTS),
        "parts": sorted(actual),
        "roles": sorted(roles),
        "closed_parts": closed_count,
        "malformed_parts": malformed,
        "duplicate_parts": duplicate_names,
        "missing_parts": missing,
        "extra_parts": extra,
        "structural_identity_ready": structural_ready,
        "artifact_files": artifact_files,
        "render_provenance": render_provenance,
        "baseline_comparison_ready": structural_ready and artifact_files["ready"] and render_provenance["ready"],
        "external_authoring_source": "missing",
        "external_authoring_status": "required_before_visual_approval",
        "visual_gate": "not_run",
        "manufacturing_gates": manufacturing_gates or "pending_not_run_needs_calibration",
        "next_action": "provide_approved_professional_component_master",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="Goal 0009 receipt JSON")
    parser.add_argument("--master-dir", type=Path, help="Directory containing receipt-listed part files")
    parser.add_argument("--render-evidence", type=Path, help="Cycles evidence JSON for the same receipt")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args(argv)

    report = audit_receipt(
        _load_receipt(args.receipt),
        receipt_path=args.receipt,
        master_dir=args.master_dir,
        render_evidence=args.render_evidence,
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
