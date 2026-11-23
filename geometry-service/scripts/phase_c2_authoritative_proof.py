#!/usr/bin/env python3
"""Run the bounded Phase C.2 proof on the Azure Rhino.Compute host.

Run this on the VM (or another host that can read the VM's output directory):
the API key is taken from the environment and is never written to a report.
The definition must be the generated, provenance-patched authoritative-core
GHX.  The script posts only the stable RH_IN inputs and validates receipt
backed files emitted by the real core MeshExporter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jewelry_gaas_foundation.config_adapter import flat_to_canonical


REQUIRED_FILES = ("model.stl", "band.stl", "stone.stl", "prongs.stl")
DEFAULT_CORE_SHA256 = "a7053521c6e0bb4286583368a50dd19674d4553ce7938799f5f0edc9b13bf4b0"


def _value_tree(name: str, value: str) -> dict[str, Any]:
    return {
        "ParamName": name,
        "InnerTree": {"{0;0}": [{"type": "System.String", "data": value}]},
    }


def _post(base_url: str, api_key: str, definition: Path, spec: dict[str, Any], timeout: float) -> tuple[int, object, float]:
    payload = {
        "algo": __import__("base64").b64encode(definition.read_bytes()).decode("ascii"),
        "pointer": None,
        "values": [
            _value_tree("RH_IN:json_string", json.dumps(spec, sort_keys=True)),
            _value_tree("RH_IN:job_id", str(spec["job"]["job_id"])),
        ],
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/grasshopper",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "RhinoComputeKey": api_key},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8")), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            body_value: object = json.loads(body)
        except json.JSONDecodeError:
            body_value = body
        return exc.code, body_value, time.monotonic() - started


def _first_branch_data(inner_tree: object) -> object:
    if not isinstance(inner_tree, dict):
        return None
    for branch in inner_tree.values():
        if isinstance(branch, list) and branch and isinstance(branch[0], dict):
            return branch[0].get("data")
    return None


def _extract_response(body: object) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    for value in body.get("values", []):
        if not isinstance(value, dict):
            continue
        name = str(value.get("ParamName", ""))
        if name.rsplit(":", 1)[-1].split(" (", 1)[0] != "response_json":
            continue
        data = _first_branch_data(value.get("InnerTree"))
        for _ in range(3):
            if not isinstance(data, str):
                break
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, dict) else None
    return None


def _latest_receipt(output_root: Path, job_id: str, expected_core_sha256: str) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for receipt_path in (output_root / job_id).rglob("export_receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("job_id") != job_id:
            continue
        if expected_core_sha256 and receipt.get("gh_definition_sha256") != expected_core_sha256:
            continue
        if not all(name in receipt.get("files", {}) for name in REQUIRED_FILES):
            continue
        candidates.append((receipt_path.stat().st_mtime, receipt_path, receipt))
    if not candidates:
        raise FileNotFoundError(f"no receipt-backed authoritative export for {job_id}")
    _mtime, path, receipt = max(candidates, key=lambda item: item[0])
    return path.parent, receipt


def _copy_and_verify(export_dir: Path, receipt: dict[str, Any], destination: Path) -> dict[str, Any]:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(export_dir, destination)
    files: dict[str, Any] = {}
    for filename in REQUIRED_FILES:
        path = destination / filename
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected = receipt["files"][filename]
        if digest != expected.get("sha256") or len(raw) != expected.get("byte_size"):
            raise ValueError(f"receipt mismatch for {filename}")
        files[filename] = expected
    return files


def _load_contract_spec(path: Path) -> dict[str, Any]:
    """Load either a canonical spec or the repository's flat config format."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("job"), dict):
        return raw
    return flat_to_canonical(raw)


def run(args: argparse.Namespace) -> dict[str, Any]:
    # Keep the credential out of argv and therefore out of shell history and
    # process listings. Azure-only environment injection is the supported path.
    api_key = os.environ.get("RHINO_COMPUTE_API_KEY", "")
    if not args.collect_only and not api_key:
        raise ValueError("RHINO_COMPUTE_API_KEY is required on the Azure host")
    paths = [Path(item) for item in args.config]
    if not paths:
        paths = sorted(Path(args.config_dir).glob("*/parameters.json"))
    paths = paths[: args.max_configs]
    if len(paths) != args.max_configs:
        raise ValueError(f"expected {args.max_configs} configs, found {len(paths)}")
    definition = Path(args.definition)
    artifact_root = Path(args.artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    expected_sha = args.expected_core_sha256
    for path in paths:
        spec = _load_contract_spec(path)
        job_id = str(spec["job"]["job_id"])
        if args.collect_only:
            status, response, elapsed = 200, {"execution_status": "completed", "reason_code": "collect_only"}, 0.0
        else:
            status, body, elapsed = _post(args.base_url, api_key, definition, spec, args.timeout)
            response = _extract_response(body)
        item: dict[str, Any] = {"job_id": job_id, "http_status": status, "elapsed_seconds": round(elapsed, 3)}
        if response is not None:
            item["response_execution_status"] = response.get("execution_status")
            item["response_reason_code"] = response.get("reason_code")
        if status != 200:
            item.update({
                "status": "failed",
                "reason": "unexpected_http_status",
            })
            results.append(item)
            continue
        if response is None or response.get("execution_status") != "completed":
            item.update({
                "status": "failed",
                "reason": "stable_response_json_not_completed",
            })
            results.append(item)
            continue
        try:
            export_dir, receipt = _latest_receipt(Path(args.remote_output_root), job_id, expected_sha)
            files = _copy_and_verify(export_dir, receipt, artifact_root / job_id)
            item.update({
                "status": "completed",
                "export_id": receipt.get("export_id"),
                "definition_sha256": receipt.get("gh_definition_sha256"),
                "definition_version": receipt.get("gh_definition_version"),
                "export_dir": str(export_dir),
                "files": files,
                "legacy_external_panel_injection": "not_used",
            })
        except (OSError, ValueError, KeyError) as exc:
            item.update({"status": "failed", "reason": str(exc)})
        results.append(item)
    return {
        "schema_version": 1,
        "status": "pass" if all(item["status"] == "completed" for item in results) else "fail",
        "started_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "definition": str(definition),
        "expected_core_sha256": expected_sha,
        "contract": {"json_input": "RH_IN:json_string", "job_id_input": "RH_IN:job_id", "response_output": "RH_OUT:response_json"},
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--definition", required=True)
    parser.add_argument("--config-dir", default=".")
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--remote-output-root", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--expected-core-sha256", default=DEFAULT_CORE_SHA256)
    parser.add_argument("--max-configs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--collect-only", action="store_true", help="Reuse existing receipt-backed exports without solving.")
    args = parser.parse_args()
    report = run(args)
    Path(args.report_json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
