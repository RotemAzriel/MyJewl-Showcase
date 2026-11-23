#!/usr/bin/env python3
"""VM-side client for the bounded Goal 0014 parametric master solves.

Unlike ``scripts/goal0011_compute_client.py``, which sent only
``{"template_id": ...}`` and therefore could not have varied geometry even if the
definition had been parametric, this client sends the full typed configuration
request so the definition's derived parameters actually change per config.

The API key is read only from the Azure VM environment. Raw HTTP response bodies
and credentials are never written to repository evidence.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request
from pathlib import Path

from goal0011_compute_client import extract_response, first_data, value_tree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", required=True, type=Path)
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="JSON request carrying template_id and the nested canonical_spec",
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--base-url", default="http://localhost")
    args = parser.parse_args(argv)

    api_key = os.environ.get("RHINO_COMPUTE_API_KEY", "")
    if not api_key:
        raise SystemExit("RHINO_COMPUTE_API_KEY is absent from VM environment")

    request_body = json.loads(args.request_file.read_text(encoding="utf-8"))
    if "canonical_spec" not in request_body:
        raise SystemExit("request must carry canonical_spec")

    payload = {
        "algo": base64.b64encode(args.definition.read_bytes()).decode("ascii"),
        "pointer": None,
        "values": [
            value_tree(
                "RH_IN:json_string", json.dumps(request_body, sort_keys=True)
            ),
            value_tree("RH_IN:job_id", args.job_id),
        ],
    }
    http_request = urllib.request.Request(
        args.base_url.rstrip("/") + "/grasshopper",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "RhinoComputeKey": api_key},
        method="POST",
    )
    with urllib.request.urlopen(http_request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))

    result = extract_response(body)
    if not result:
        diagnostics = []
        for value in body.get("values", []):
            diagnostics.append(
                {
                    "param": str(value.get("ParamName", "")),
                    "data": str(first_data(value.get("InnerTree")))[:2400],
                }
            )
        print(
            json.dumps(
                {
                    "execution_status": "failed",
                    "reason_code": "stable_response_absent",
                    "safe_output_diagnostics": diagnostics,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("execution_status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
