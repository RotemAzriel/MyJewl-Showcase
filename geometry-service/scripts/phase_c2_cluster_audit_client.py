#!/usr/bin/env python3
"""POST a diagnostic GHX to Rhino.Compute without exposing the API key."""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", required=True, type=Path)
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--report-json", required=True, type=Path)
    args = parser.parse_args()

    key = os.environ.get("RHINO_COMPUTE_API_KEY", "")
    if not key:
        raise SystemExit("RHINO_COMPUTE_API_KEY is required on the Azure host")
    payload = {
        "algo": base64.b64encode(args.definition.read_bytes()).decode("ascii"),
        "pointer": None,
        "values": [
            {
                "ParamName": "RH_IN:json_string",
                "InnerTree": {"{0;0}": [{"type": "System.String", "data": "{}"}]},
            },
            {
                "ParamName": "RH_IN:job_id",
                "InnerTree": {"{0;0}": [{"type": "System.String", "data": "cluster_audit"}]},
            },
        ],
    }
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/grasshopper",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "RhinoComputeKey": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            report = {"http_status": response.status, "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        report = {"http_status": exc.code, "body": exc.read().decode("utf-8", "replace")}
    args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["http_status"] == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
