#!/usr/bin/env python3
"""Validate the bounded M4 Phase C.3 evidence bundle.

This is a local, non-networking gate. It checks that the authoritative native
contract proof, receipt-backed geometry bundles, geometry differential, and
Blender 5.2 mask reports agree with one another. It deliberately treats the
uncalibrated collision broad phase as non-certifying evidence rather than a
manufacturing pass/fail decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.phase_c2_authoritative_proof import DEFAULT_CORE_SHA256, REQUIRED_FILES


EXPECTED_CONTRACT = {
    "json_input": "RH_IN:json_string",
    "job_id_input": "RH_IN:job_id",
    "response_output": "RH_OUT:response_json",
}
EXPECTED_MASK_VALUES = {0, 1, 2, 3}
REQUIRED_VISIBLE_MASK_VALUES = {1, 2, 3}
EXPECTED_SOURCE_CORE_SHA256 = "33e0392357ec56c631ceafc6e5632f724ebe5e9ee9104d769481299314adc4b1"
FORBIDDEN_REPORT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "rhino_token",
    "secret",
    "token",
}
FORBIDDEN_FILE_SUFFIXES = {".env", ".pem", ".key", ".pfx", ".p12", ".rdp"}
FORBIDDEN_FILE_TERMS = ("credential", "password", "secret")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _record_failure(failures: list[str], check: str, detail: str) -> None:
    failures.append(f"{check}: {detail}")


def _walk_report_keys(value: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_REPORT_KEYS:
                found.append(f"{prefix}{key}")
            found.extend(_walk_report_keys(child, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_report_keys(child, f"{prefix}[{index}]."))
    return found


def _artifact_path(value: object, repo_root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("render artifact path is missing")
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _extract_patch_response(report: dict[str, Any]) -> dict[str, Any] | None:
    """Unwrap the nested Rhino.Compute response saved by the patcher run."""
    body = report.get("body")
    if not isinstance(body, dict):
        return None
    for value in body.get("values", []):
        if not isinstance(value, dict) or "response_json" not in str(value.get("ParamName", "")):
            continue
        inner_tree = value.get("InnerTree")
        if not isinstance(inner_tree, dict):
            continue
        for branch in inner_tree.values():
            if not isinstance(branch, list) or not branch:
                continue
            data = branch[0].get("data") if isinstance(branch[0], dict) else None
            for _ in range(3):
                if not isinstance(data, str):
                    break
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return None
            return data if isinstance(data, dict) else None
    return None


def validate_evidence(
    evidence_root: str | Path,
    *,
    repo_root: str | Path | None = None,
    contract_report_name: str = "contract_proof_report.json",
    differential_report_name: str = "differential_report_c3.json",
    patch_report_name: str = "patch_report.json",
    bundle_report_name: str = "bundle_smoke_c3.json",
    blender_report_name: str = "blender_smoke_5_2_report.json",
    expected_core_sha256: str = DEFAULT_CORE_SHA256,
) -> dict[str, Any]:
    root = Path(evidence_root)
    workspace = Path(repo_root) if repo_root is not None else Path.cwd()
    failures: list[str] = []
    checks: dict[str, Any] = {}

    contract_path = root / contract_report_name
    differential_path = root / differential_report_name
    try:
        contract = _read_json(contract_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _record_failure(failures, "authoritative_contract", str(exc))
        contract = {}

    checks["authoritative_contract"] = {
        "path": str(contract_path),
        "status": contract.get("status"),
        "expected_core_sha256": expected_core_sha256,
    }
    if contract.get("status") != "pass":
        _record_failure(failures, "authoritative_contract", "proof report is not pass")
    if contract.get("expected_core_sha256") != expected_core_sha256:
        _record_failure(failures, "authoritative_contract", "definition hash pin changed")
    if contract.get("contract") != EXPECTED_CONTRACT:
        _record_failure(failures, "authoritative_contract", "stable RH_IN/RH_OUT contract changed")

    results = contract.get("results")
    if not isinstance(results, list) or len(results) != 3:
        _record_failure(failures, "authoritative_contract", "proof must contain exactly three results")
        results = []
    job_ids: list[str] = []
    receipt_facts: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            _record_failure(failures, "authoritative_contract", "result is not an object")
            continue
        job_id = result.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            _record_failure(failures, "authoritative_contract", "result is missing job_id")
            continue
        job_ids.append(job_id)
        if result.get("status") != "completed" or result.get("http_status") != 200:
            _record_failure(failures, f"authoritative_contract.{job_id}", "HTTP/solve status is not completed")
        if result.get("response_execution_status") != "completed":
            _record_failure(failures, f"authoritative_contract.{job_id}", "response_execution_status is not completed")
        if result.get("definition_sha256") != expected_core_sha256:
            _record_failure(failures, f"authoritative_contract.{job_id}", "receipt definition hash is not pinned")
        if result.get("legacy_external_panel_injection") != "not_used":
            _record_failure(failures, f"authoritative_contract.{job_id}", "legacy panel injection was not explicitly absent")
        files = result.get("files")
        if not isinstance(files, dict) or set(files) != set(REQUIRED_FILES):
            _record_failure(failures, f"authoritative_contract.{job_id}", "receipt does not contain all four required files")
        else:
            receipt_facts[job_id] = files

    if len(set(job_ids)) != 3:
        _record_failure(failures, "authoritative_contract", "job IDs are not unique")

    patch_path = root / patch_report_name
    try:
        patch_report = _read_json(patch_path)
        patch_response = _extract_patch_response(patch_report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _record_failure(failures, "authoritative_model", str(exc))
        patch_report = {}
        patch_response = None
    checks["authoritative_model"] = {
        "path": str(patch_path),
        "http_status": patch_report.get("http_status"),
        "source_core_sha256": (
            patch_response.get("source_core", {}).get("sha256")
            if patch_response else None
        ),
        "existing_core_geometry_nodes_reused": (
            patch_response.get("authoritative_model_proof", {}).get("existing_core_geometry_nodes_reused")
            if patch_response else None
        ),
    }
    proof = patch_response.get("authoritative_model_proof", {}) if patch_response else {}
    if patch_report.get("http_status") != 200 or patch_response is None:
        _record_failure(failures, "authoritative_model", "patch proof response is missing")
    if patch_response and patch_response.get("execution_status") != "patched":
        _record_failure(failures, "authoritative_model", "patch proof did not complete")
    if patch_response and patch_response.get("contract") != EXPECTED_CONTRACT:
        _record_failure(failures, "authoritative_model", "patch proof contract changed")
    if proof.get("existing_core_geometry_nodes_reused") is not True:
        _record_failure(failures, "authoritative_model", "existing authoritative geometry nodes were not proven reused")
    if proof.get("source_sha256_pinned") is not True:
        _record_failure(failures, "authoritative_model", "source core was not SHA-pinned")
    if proof.get("synthetic_external_panel_injection") is not False:
        _record_failure(failures, "authoritative_model", "synthetic external panel injection was not ruled out")
    if patch_response and patch_response.get("source_core", {}).get("sha256") != EXPECTED_SOURCE_CORE_SHA256:
        _record_failure(failures, "authoritative_model", "authoritative source core hash changed")

    try:
        differential = _read_json(differential_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _record_failure(failures, "geometry_differential", str(exc))
        differential = {}
    checks["geometry_differential"] = {
        "path": str(differential_path),
        "status": differential.get("status"),
        "config_count": differential.get("config_count"),
    }
    if differential.get("status") != "pass" or differential.get("config_count") != 3:
        _record_failure(failures, "geometry_differential", "three-config differential is not pass")
    for pair in differential.get("pairs", []):
        changed = set(pair.get("changed_files", [])) if isinstance(pair, dict) else set()
        if changed != set(REQUIRED_FILES):
            _record_failure(failures, "geometry_differential", "a pair did not change all four STL files")

    bundle_root = root / "bundles"
    bundle_checks: list[dict[str, Any]] = []
    for job_id in job_ids:
        bundle_dir = bundle_root / job_id
        bundle_path = bundle_dir / bundle_report_name
        blender_path = bundle_dir / blender_report_name
        try:
            bundle = _read_json(bundle_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _record_failure(failures, f"bundle.{job_id}", str(exc))
            bundle = {}
        try:
            blender = _read_json(blender_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _record_failure(failures, f"blender.{job_id}", str(exc))
            blender = {}

        if bundle.get("status") != "pass":
            _record_failure(failures, f"bundle.{job_id}", "bundle smoke is not pass")
        if bundle.get("contract", {}).get("status") != "valid":
            _record_failure(failures, f"bundle.{job_id}", "bundle contract is not valid")
        if bundle.get("gate_1_closedness", {}).get("status") != "pass":
            _record_failure(failures, f"bundle.{job_id}", "Gate 1 closedness is not pass")
        if bundle.get("official_geometry_gate_state_modified") is not False:
            _record_failure(failures, f"bundle.{job_id}", "official geometry gate ledger was modified")
        for mesh in bundle.get("gate_1_closedness", {}).get("meshes", []):
            if mesh.get("status") != "pass":
                _record_failure(failures, f"bundle.{job_id}", f"mesh Gate 1 failed: {mesh.get('path')}")

        bundle_files = bundle.get("contract", {}).get("files", {})
        expected_files = receipt_facts.get(job_id, {})
        for filename in REQUIRED_FILES:
            if bundle_files.get(filename, {}).get("sha256") != expected_files.get(filename, {}).get("sha256"):
                _record_failure(failures, f"bundle.{job_id}", f"{filename} disagrees with receipt proof")

        if blender.get("status") != "pass":
            _record_failure(failures, f"blender.{job_id}", "Blender smoke is not pass")
        version = blender.get("blender_version")
        if not isinstance(version, str) or not version.startswith("5.2."):
            _record_failure(failures, f"blender.{job_id}", "runtime is not Blender 5.2")
        mask = blender.get("mask", {})
        if set(mask.get("allowed_values", [])) != EXPECTED_MASK_VALUES:
            _record_failure(failures, f"blender.{job_id}", "mask allowed values changed")
        if not REQUIRED_VISIBLE_MASK_VALUES.issubset(set(mask.get("observed_values", []))):
            _record_failure(failures, f"blender.{job_id}", "mask is missing a required visible class")
        if mask.get("missing_visible_classes"):
            _record_failure(failures, f"blender.{job_id}", "mask report has missing visible classes")
        for artifact in blender.get("artifacts", {}).values():
            try:
                artifact_path = _artifact_path(artifact.get("path"), workspace)
                if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
                    _record_failure(failures, f"blender.{job_id}", f"missing render artifact: {artifact_path}")
            except (OSError, ValueError) as exc:
                _record_failure(failures, f"blender.{job_id}", str(exc))

        bundle_checks.append({
            "job_id": job_id,
            "bundle_status": bundle.get("status"),
            "gate_1_status": bundle.get("gate_1_closedness", {}).get("status"),
            "blender_status": blender.get("status"),
            "blender_version": blender.get("blender_version"),
            "mask_observed_values": mask.get("observed_values"),
        })
    checks["bundles"] = bundle_checks

    secret_findings: list[str] = []
    report_paths = [contract_path, differential_path]
    report_paths.extend(bundle_root.glob(f"*/{bundle_report_name}"))
    report_paths.extend(bundle_root.glob(f"*/{blender_report_name}"))
    for report_path in report_paths:
        try:
            findings = _walk_report_keys(_read_json(report_path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        secret_findings.extend(f"{report_path}: {finding}" for finding in findings)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        lowered_name = path.name.lower()
        if path.suffix.lower() in FORBIDDEN_FILE_SUFFIXES or any(term in lowered_name for term in FORBIDDEN_FILE_TERMS):
            secret_findings.append(str(path))
    checks["credential_hygiene"] = {
        "status": "pass" if not secret_findings else "fail",
        "findings": secret_findings,
        "api_key_source": "environment_only",
    }
    if secret_findings:
        _record_failure(failures, "credential_hygiene", "credential-like report fields or files found")

    return {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "evidence_root": str(root),
        "checks": checks,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--expected-core-sha256", default=DEFAULT_CORE_SHA256)
    args = parser.parse_args(argv)
    report = validate_evidence(
        args.evidence_root,
        repo_root=args.repo_root,
        expected_core_sha256=args.expected_core_sha256,
    )
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
