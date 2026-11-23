#!/usr/bin/env python3
"""Echo-only /solve bridge for the Rhino.Compute smoke milestone.

This manual helper intentionally avoids Rhino, Rhino.Compute, Grasshopper, and
foundation-package imports. It exists only to prove the adapter-compatible HTTP
contract before real CAD generation is wired in.
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_DEFINITION_ID = "echo_smoke_v0"
REQUIRED_PAYLOAD_FIELDS = {
    "definition_id",
    "job_id",
    "archetype",
    "spec",
    "pending_geometry_checks",
    "output_targets",
}


def build_echo_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the adapter-compatible harmless echo response."""

    pending_geometry_checks = payload.get("pending_geometry_checks", [])
    if not isinstance(pending_geometry_checks, list):
        pending_geometry_checks = []

    gate_codes = [code for code in pending_geometry_checks if isinstance(code, str)]
    executed_check = str(payload.get("definition_id", DEFAULT_DEFINITION_ID))
    return {
        "execution_status": "completed",
        "reason_code": "rhino_compute_echo_definition_executed",
        "assets": {},
        "geometry_gates": [
            {
                "code": code,
                "status": "pending",
                "reason_code": "check_not_implemented",
                "details": {
                    "executed_check": executed_check,
                    "calibrated": False,
                },
            }
            for code in gate_codes
        ],
        "debug": {
            "job_id": payload.get("job_id"),
            "archetype": payload.get("archetype"),
        },
    }


def validate_payload(
    payload: object,
    expected_definition_id: str,
) -> tuple[bool, str | None]:
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"

    missing_fields = sorted(REQUIRED_PAYLOAD_FIELDS.difference(payload))
    if missing_fields:
        return False, "missing_required_fields"

    if payload.get("definition_id") != expected_definition_id:
        return False, "rhino_definition_not_executed"

    if not isinstance(payload.get("pending_geometry_checks"), list):
        return False, "pending_geometry_checks_must_be_array"

    if not isinstance(payload.get("output_targets"), list):
        return False, "output_targets_must_be_array"

    if not isinstance(payload.get("spec"), dict):
        return False, "spec_must_be_object"

    return True, None


def handle_solve_request(
    path: str,
    headers: dict[str, str],
    body: bytes,
    api_key: str,
    definition_id: str = DEFAULT_DEFINITION_ID,
) -> tuple[int, dict[str, Any]]:
    if path != "/solve":
        return 404, {
            "execution_status": "failed",
            "reason_code": "endpoint_not_found",
        }

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    if normalized_headers.get("x-api-key") != api_key:
        return 401, {
            "execution_status": "failed",
            "reason_code": "unauthorized",
        }

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    valid, reason_code = validate_payload(payload, definition_id)
    if not valid:
        return 400, {
            "execution_status": "failed",
            "reason_code": reason_code,
        }

    return 200, build_echo_response(payload)


def make_handler(api_key: str, definition_id: str = DEFAULT_DEFINITION_ID):
    class SolveBridgeHandler(BaseHTTPRequestHandler):
        server_version = "RhinoComputeEchoSolveBridge/0.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
            status_code, response = handle_solve_request(
                path=self.path,
                headers=dict(self.headers.items()),
                body=self._read_body(),
                api_key=api_key,
                definition_id=definition_id,
            )
            self._send_json(status_code, response)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_body(self) -> bytes:
            raw_length = self.headers.get("content-length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                return b""

            return self.rfile.read(length)

        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SolveBridgeHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an echo-only adapter-compatible /solve bridge.",
    )
    parser.add_argument("--host", default=os.getenv("SOLVE_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SOLVE_BRIDGE_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--definition-id",
        default=os.getenv("RHINO_COMPUTE_DEFINITION_ID", DEFAULT_DEFINITION_ID),
    )
    return parser.parse_args()


def main() -> int:
    api_key = os.getenv("RHINO_COMPUTE_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "reason_code": "missing_required_environment",
                    "missing_environment": ["RHINO_COMPUTE_API_KEY"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    args = parse_args()
    handler = make_handler(api_key=api_key, definition_id=args.definition_id)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        json.dumps(
            {
                "status": "listening",
                "host": args.host,
                "port": args.port,
                "definition_id": args.definition_id,
                "endpoint": "/solve",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
