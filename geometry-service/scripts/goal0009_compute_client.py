#!/usr/bin/env python3
"""VM-side client for one bounded Goal 0009 Azure Rhino.Compute solve.

The API key is read from the VM environment only. Raw HTTP bodies are not
written to disk; the script prints only the stable RH_OUT response.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request
from pathlib import Path


def value_tree(name: str, value: str) -> dict:
    return {
        "ParamName": name,
        "InnerTree": {"{0;0}": [{"type": "System.String", "data": value}]},
    }


def first_data(tree):
    if not isinstance(tree, dict):
        return None
    for branch in tree.values():
        if isinstance(branch, list) and branch and isinstance(branch[0], dict):
            return branch[0].get("data")
    return None


def extract_response(body: dict) -> dict | None:
    for value in body.get("values", []):
        name = str(value.get("ParamName", ""))
        if name.rsplit(":", 1)[-1].split(" (", 1)[0] != "response_json":
            continue
        data = first_data(value.get("InnerTree"))
        for _ in range(12):
            if not isinstance(data, str):
                break
            data = json.loads(data)
        return data if isinstance(data, dict) else None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--definition", required=True, type=Path)
    parser.add_argument("--job-id", default="goal0009-reference-v2")
    parser.add_argument("--base-url", default="http://localhost")
    args = parser.parse_args()
    api_key = os.environ.get("RHINO_COMPUTE_API_KEY", "")
    if not api_key:
        raise SystemExit("RHINO_COMPUTE_API_KEY is absent from VM environment")
    payload = {
        "algo": base64.b64encode(args.definition.read_bytes()).decode("ascii"),
        "pointer": None,
        "values": [
            value_tree("RH_IN:json_string", json.dumps({
                "template_id": "goal0009-split-ring-classic-basket-v2",
            }, sort_keys=True)),
            value_tree("RH_IN:job_id", args.job_id),
        ],
    }
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/grasshopper",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "RhinoComputeKey": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))
    result = extract_response(body)
    if not result:
        diagnostics = []
        for value in body.get("values", []):
            diagnostics.append({
                "param": str(value.get("ParamName", "")),
                "data": str(first_data(value.get("InnerTree")))[:2400],
            })
        print(json.dumps({
            "execution_status": "failed",
            "reason_code": "stable_response_absent",
            "safe_output_diagnostics": diagnostics,
        }, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("execution_status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
