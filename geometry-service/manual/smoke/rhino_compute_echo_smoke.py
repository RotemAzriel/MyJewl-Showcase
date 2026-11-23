#!/usr/bin/env python3
"""Manual Rhino.Compute echo smoke helper.

This script is intentionally outside the unit test suite. It exercises only the
explicit RhinoComputeHttpAdapter boundary against a locally configured
Rhino.Compute-compatible endpoint.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = ROOT / "examples" / "solitaire_valid.json"
REQUIRED_ENV_VARS = (
    "RHINO_COMPUTE_BASE_URL",
    "RHINO_COMPUTE_API_KEY",
    "RHINO_COMPUTE_DEFINITION_ID",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jewelry_gaas_foundation import (  # noqa: E402
    RhinoComputeHttpAdapter,
    solve_job,
    validate_job_spec,
)


def main() -> int:
    missing_env_vars = tuple(name for name in REQUIRED_ENV_VARS if not os.getenv(name))
    if missing_env_vars:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "reason_code": "missing_required_environment",
                    "missing_environment": list(missing_env_vars),
                    "network_call_attempted": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    spec_path = Path(os.getenv("JOB_SPEC_PATH", str(DEFAULT_SPEC_PATH))).expanduser()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print_error("spec_read_failed", str(exc))
        return 2
    except json.JSONDecodeError as exc:
        print_error("spec_json_invalid", str(exc))
        return 2

    validation = validate_job_spec(spec)
    if not validation.accepted:
        print(
            json.dumps(
                {
                    "status": "validation_rejected",
                    "network_call_attempted": False,
                    "issues": [asdict(issue) for issue in validation.issues],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    timeout_seconds = parse_timeout(os.getenv("RHINO_COMPUTE_TIMEOUT_SECONDS"))
    if timeout_seconds is None:
        print_error("timeout_invalid", "RHINO_COMPUTE_TIMEOUT_SECONDS must be numeric.")
        return 2

    adapter = RhinoComputeHttpAdapter(
        base_url=os.environ["RHINO_COMPUTE_BASE_URL"],
        api_key=os.environ["RHINO_COMPUTE_API_KEY"],
        definition_id=os.environ["RHINO_COMPUTE_DEFINITION_ID"],
        timeout_seconds=timeout_seconds,
    )
    package = solve_job(spec, geometry_adapter=adapter)
    package_dict = asdict(package)
    print(json.dumps(sanitized_output(package_dict), indent=2, sort_keys=True))
    return smoke_exit_code(package_dict)


def parse_timeout(raw_value: str | None) -> float | None:
    if raw_value is None:
        return 30.0
    try:
        return float(raw_value)
    except ValueError:
        return None


def sanitized_output(package: dict[str, Any]) -> dict[str, Any]:
    debug_trace = package.get("debug_trace")
    if isinstance(debug_trace, dict):
        debug_trace.pop("adapter_base_url", None)
    return package


def smoke_exit_code(package: dict[str, Any]) -> int:
    if package.get("validation_status") != "accepted":
        return 1
    technical_summary = package.get("technical_summary")
    if not isinstance(technical_summary, dict):
        return 1
    return 0 if technical_summary.get("adapter_execution_status") == "completed" else 1


def print_error(reason_code: str, message: str) -> None:
    print(
        json.dumps(
            {
                "status": "configuration_error",
                "reason_code": reason_code,
                "message": message,
                "network_call_attempted": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
