#!/usr/bin/env python3
"""Run a bounded native Rhino.Compute Phase C.1 differential proof.

The native GHX returns receipt-backed STL bytes in ``response_json``.  This
runner stores those bytes locally, which keeps the proof independent of a
temporary Windows file-transfer service while still exercising native
``POST /grasshopper`` and the real Rhino/GhPython solve.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jewelry_gaas_foundation.config_adapter import flat_to_canonical


PENDING_CHECKS = (
    "minimum_structural_floors",
    "stone_stone_collision",
    "stone_metal_collision",
    "mountability_and_setting_access",
    "mesh_closedness_and_manifold_suitability",
)


def _value_tree(name: str, value: str) -> dict[str, Any]:
    return {
        "ParamName": name,
        "InnerTree": {"{0;0}": [{"type": "System.String", "data": value}]},
    }


def _find_value(values: object, name: str) -> object:
    if not isinstance(values, list):
        return None
    for value in values:
        if not isinstance(value, dict):
            continue
        param_name = value.get("ParamName")
        if not isinstance(param_name, str):
            continue
        # A component grouped under RH_OUT is returned by Compute as
        # ``RH_OUT:name (component_output_name)``.  Accept that stable
        # grouped form as well as the direct ``name`` form.
        normalized_name = param_name.split(" (", 1)[0].rsplit(":", 1)[-1]
        target_name = name.rsplit(":", 1)[-1]
        if normalized_name != target_name and param_name != name:
            continue
        inner_tree = value.get("InnerTree")
        if not isinstance(inner_tree, dict):
            continue
        for branch in inner_tree.values():
            if isinstance(branch, list) and branch and isinstance(branch[0], dict):
                return branch[0].get("data")
    return None


def _post(
    base_url: str,
    api_key: str,
    definition: Path,
    spec: dict[str, Any],
    input_name: str,
    job_id_input_name: str,
    response_output_name: str,
    timeout: float,
) -> tuple[int, object, str]:
    payload = {
        "algo": base64.b64encode(definition.read_bytes()).decode("ascii"),
        "pointer": None,
        "values": [
            _value_tree(input_name, json.dumps(spec, sort_keys=True)),
            _value_tree(job_id_input_name, str(spec["job"]["job_id"])),
            _value_tree("pending_geometry_checks", json.dumps(list(PENDING_CHECKS))),
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
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")), f"{time.monotonic() - started:.2f}s"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body, f"{time.monotonic() - started:.2f}s"


def _materialize(
    response: dict[str, Any],
    spec: dict[str, Any],
    definition: Path,
    output_root: Path,
    response_output_name: str,
) -> Path:
    response_json = _find_value(response.get("values"), response_output_name)
    # Rhino 8's Python 3 output marshalling may wrap a string output in one
    # additional JSON string layer.  Unwrap the bounded number of layers used
    # by Compute until the native response object is reached.
    for _ in range(3):
        if not isinstance(response_json, str):
            break
        response_json = json.loads(response_json)
    if not isinstance(response_json, dict):
        raise ValueError("native response_json output is missing")
    if response_json.get("execution_status") != "completed":
        raise ValueError(f"native definition failed: {response_json.get('reason_code')}")
    debug = response_json.get("debug")
    if not isinstance(debug, dict):
        raise ValueError("native response has no debug receipt payload")
    encoded = debug.get("files_base64")
    if not isinstance(encoded, dict):
        raise ValueError("native response has no files_base64 payload")
    job_id = str(spec["job"]["job_id"])
    export_id = str(debug.get("export_id") or uuid.uuid4())
    export_dir = output_root / job_id / export_id
    export_dir.mkdir(parents=True, exist_ok=True)
    facts = debug.get("files")
    if not isinstance(facts, dict):
        raise ValueError("native response has no file facts")
    for filename, encoded_data in encoded.items():
        if not isinstance(filename, str) or not isinstance(encoded_data, str):
            raise ValueError("native response contains invalid file data")
        (export_dir / filename).write_bytes(base64.b64decode(encoded_data))
    receipt = {
        "schema_version": 1,
        "export_id": export_id,
        "job_id": job_id,
        "gh_definition_path": str(definition),
        "gh_definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "gh_definition_version": "phase_c1_native_rh_in_v1",
        "exported_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "files": facts,
    }
    (export_dir / "export_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return export_dir


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_paths = sorted(Path(args.config_dir).glob("*/parameters.json"))[: args.max_configs]
    if len(config_paths) != args.max_configs:
        raise ValueError(f"expected {args.max_configs} configs, found {len(config_paths)}")
    output_root = Path(args.output_root)
    results: list[dict[str, Any]] = []
    for params_path in config_paths:
        flat = json.loads(params_path.read_text(encoding="utf-8"))
        spec = flat_to_canonical(flat)
        status, response, elapsed = _post(
            args.base_url,
            args.api_key,
            Path(args.definition),
            spec,
            args.input_name,
            args.job_id_input_name,
            args.response_output_name,
            args.timeout,
        )
        item: dict[str, Any] = {
            "job_id": spec["job"]["job_id"],
            "http_status": status,
            "elapsed": elapsed,
        }
        if status == 200 and isinstance(response, dict):
            export_dir = _materialize(
                response,
                spec,
                Path(args.definition),
                output_root,
                args.response_output_name,
            )
            item.update({"status": "completed", "export_dir": str(export_dir)})
        else:
            item.update({"status": "failed", "response": response})
        results.append(item)
    return {"status": "pass" if all(r["status"] == "completed" for r in results) else "fail", "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--definition", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--max-configs", type=int, default=3)
    parser.add_argument("--input-name", default="RH_IN:json_string")
    parser.add_argument("--job-id-input-name", default="RH_IN:job_id")
    parser.add_argument("--response-output-name", default="RH_OUT:response_json")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
