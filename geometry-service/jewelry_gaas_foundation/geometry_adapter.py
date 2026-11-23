"""Geometry adapter boundary for future Rhino/Rhino.Compute execution.

This module intentionally contains no Rhino, Rhino.Compute, Grasshopper,
license, network, or CAD runtime imports. Real geometry execution must be
passed into the solver explicitly through this boundary.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .output import GeometryGateStatus, OutputAsset


DEFAULT_RHINO_COMPUTE_ENDPOINT = "/solve"
DEFAULT_RHINO_GRASSHOPPER_ENDPOINT = "/grasshopper"


@dataclass(frozen=True)
class GeometryAdapterResult:
    """Full adapter result returned to the solver boundary."""

    execution_status: str
    assets: tuple[OutputAsset, ...]
    geometry_gates: tuple[GeometryGateStatus, ...]
    reason_code: str | None = None
    debug_trace: dict[str, object] = field(default_factory=dict)


class GeometryAdapter(Protocol):
    """Interface for an explicit future geometry execution adapter."""

    name: str

    def evaluate_geometry(
        self,
        spec: dict[str, Any],
        pending_geometry_checks: tuple[str, ...],
        output_targets: tuple[str, ...],
    ) -> GeometryAdapterResult:
        """Return geometry assets and gate statuses for a validated spec."""


@dataclass(frozen=True)
class NoOpGeometryAdapter:
    """Default adapter used when Rhino execution is not explicitly configured."""

    name: str = "noop_rhino_adapter"

    def evaluate_geometry(
        self,
        spec: dict[str, Any],
        pending_geometry_checks: tuple[str, ...],
        output_targets: tuple[str, ...],
    ) -> GeometryAdapterResult:
        gates = tuple(
            GeometryGateStatus(
                code=code,
                status="not_run",
                reason_code="rhino_adapter_not_configured",
                details={
                    "source": "foundation_solver_boundary",
                    "archetype": str(spec.get("job", {}).get("archetype", "unknown")),
                },
            )
            for code in pending_geometry_checks
        )
        return GeometryAdapterResult(
            execution_status="not_configured",
            assets=_pending_assets(output_targets),
            geometry_gates=gates,
            reason_code="rhino_adapter_not_configured",
            debug_trace={
                "adapter_execution_status": "not_configured",
                "adapter_reason_code": "rhino_adapter_not_configured",
            },
        )

    def evaluate_geometry_gates(
        self,
        spec: dict[str, Any],
        pending_geometry_checks: tuple[str, ...],
    ) -> tuple[GeometryGateStatus, ...]:
        return self.evaluate_geometry(spec, pending_geometry_checks, ()).geometry_gates


@dataclass(frozen=True)
class RhinoComputeHttpAdapter:
    """HTTP-shaped Rhino.Compute adapter tested with injected fake clients."""

    base_url: str
    api_key: str
    definition_id: str
    timeout_seconds: float = 30.0
    http_client: object | None = None
    name: str = "rhino_compute_http_adapter"

    def evaluate_geometry(
        self,
        spec: dict[str, Any],
        pending_geometry_checks: tuple[str, ...],
        output_targets: tuple[str, ...],
    ) -> GeometryAdapterResult:
        payload = {
            "definition_id": self.definition_id,
            "job_id": str(spec["job"]["job_id"]),
            "archetype": str(spec["job"]["archetype"]),
            "spec": spec,
            "pending_geometry_checks": list(pending_geometry_checks),
            "output_targets": list(output_targets),
        }
        base_debug_trace = {
            "adapter_base_url": self.base_url,
            "adapter_definition_id": self.definition_id,
            "adapter_timeout_seconds": self.timeout_seconds,
        }

        try:
            response = self._post(payload)
        except TimeoutError:
            return _failure_result(
                "timeout",
                "rhino_compute_timeout",
                output_targets,
                pending_geometry_checks,
                base_debug_trace,
            )
        except (OSError, urllib.error.URLError, ValueError):
            return _failure_result(
                "failed",
                "rhino_compute_execution_failed",
                output_targets,
                pending_geometry_checks,
                base_debug_trace,
            )

        return _map_rhino_compute_response(
            response,
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    def _post(self, payload: dict[str, object]) -> object:
        client = self.http_client or _StdlibJsonHttpClient()
        url = self.base_url.rstrip("/") + DEFAULT_RHINO_COMPUTE_ENDPOINT
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
        }

        if hasattr(client, "post"):
            return client.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        if hasattr(client, "request"):
            return client.request(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        if callable(client):
            return client(payload)
        raise ValueError("HTTP client must expose post(), request(), or be callable.")


@dataclass(frozen=True)
class RhinoComputeGrasshopperAdapter:
    """Native Rhino.Compute ``/grasshopper`` adapter.

    The promoted Grasshopper definition is expected to accept a string input
    containing the canonical spec JSON and return an adapter-compatible JSON
    string from an output named ``response_json``. This keeps the foundation
    package Rhino-free while allowing the batch runner to talk directly to
    native Rhino.Compute when no VM-side ``/solve`` bridge is present.
    """

    base_url: str
    api_key: str
    definition_path: str | Path
    definition_id: str
    input_name: str = "json_string"
    job_id_input_name: str | None = None
    response_output_name: str = "response_json"
    timeout_seconds: float = 300.0
    http_client: object | None = None
    name: str = "rhino_compute_grasshopper_adapter"

    def evaluate_geometry(
        self,
        spec: dict[str, Any],
        pending_geometry_checks: tuple[str, ...],
        output_targets: tuple[str, ...],
    ) -> GeometryAdapterResult:
        base_debug_trace = {
            "adapter_base_url": self.base_url,
            "adapter_definition_id": self.definition_id,
            "adapter_definition_path": str(self.definition_path),
            "adapter_endpoint": DEFAULT_RHINO_GRASSHOPPER_ENDPOINT,
            "adapter_input_name": self.input_name,
            "adapter_job_id_input_name": self.job_id_input_name,
            "adapter_response_output_name": self.response_output_name,
            "adapter_timeout_seconds": self.timeout_seconds,
        }

        try:
            response = self._post(spec)
        except TimeoutError:
            return _failure_result(
                "timeout",
                "rhino_compute_timeout",
                output_targets,
                pending_geometry_checks,
                base_debug_trace,
            )
        except (OSError, urllib.error.URLError, ValueError):
            return _failure_result(
                "failed",
                "rhino_compute_execution_failed",
                output_targets,
                pending_geometry_checks,
                base_debug_trace,
            )

        adapter_response = _extract_grasshopper_adapter_response(
            response,
            self.response_output_name,
        )
        if adapter_response is None:
            return _failure_result(
                "failed",
                "rhino_compute_response_json_missing",
                output_targets,
                pending_geometry_checks,
                base_debug_trace,
            )

        return _map_rhino_compute_response(
            adapter_response,
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    def _post(self, spec: dict[str, Any]) -> object:
        client = self.http_client or _StdlibJsonHttpClient()
        url = self.base_url.rstrip("/") + DEFAULT_RHINO_GRASSHOPPER_ENDPOINT
        headers = {
            "content-type": "application/json",
            "RhinoComputeKey": self.api_key,
        }
        values = [
            _grasshopper_string_value_tree(
                self.input_name,
                json.dumps(spec, sort_keys=True),
            )
        ]
        if self.job_id_input_name:
            values.append(
                _grasshopper_string_value_tree(
                    self.job_id_input_name,
                    str(spec["job"]["job_id"]),
                )
            )
        payload = {
            "algo": _definition_base64(self.definition_path),
            "pointer": None,
            "values": values,
        }

        if hasattr(client, "post"):
            return client.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        if hasattr(client, "request"):
            return client.request(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        if callable(client):
            return client(payload)
        raise ValueError("HTTP client must expose post(), request(), or be callable.")


class _StdlibJsonHttpClient:
    """Tiny stdlib JSON client used only when an explicit adapter is invoked."""

    def post(
        self,
        url: str,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> object:
        body = json_dumps_bytes(json)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json_loads_bytes(response.read())
        except socket.timeout as exc:
            raise TimeoutError("Rhino.Compute request timed out.") from exc


def json_dumps_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def json_loads_bytes(value: bytes) -> object:
    return json.loads(value.decode("utf-8"))


def _definition_base64(definition_path: str | Path) -> str:
    return b64encode(Path(definition_path).read_bytes()).decode("ascii")


def _grasshopper_string_value_tree(param_name: str, value: str) -> dict[str, object]:
    return {
        "ParamName": param_name,
        "InnerTree": {
            "{0;0}": [
                {
                    "type": "System.String",
                    "data": value,
                }
            ]
        },
    }


def _extract_grasshopper_adapter_response(
    response: object,
    response_output_name: str,
) -> dict[str, object] | None:
    if isinstance(response, dict) and "execution_status" in response:
        return response

    if not isinstance(response, dict):
        return None

    values = response.get("values")
    if not isinstance(values, list):
        return None

    for value_tree in values:
        if not isinstance(value_tree, dict):
            continue
        if not _param_name_matches(value_tree.get("ParamName"), response_output_name):
            continue
        data = _first_value_tree_data(value_tree.get("InnerTree"))
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _param_name_matches(value: object, expected: str) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.rsplit(":", 1)[-1]
    return normalized == expected


def _first_value_tree_data(inner_tree: object) -> object:
    if not isinstance(inner_tree, dict):
        return None
    for branch in inner_tree.values():
        if not isinstance(branch, list) or not branch:
            continue
        first = branch[0]
        if isinstance(first, dict) and "data" in first:
            return first["data"]
    return None


def _map_rhino_compute_response(
    response: object,
    output_targets: tuple[str, ...],
    pending_geometry_checks: tuple[str, ...],
    base_debug_trace: dict[str, object],
) -> GeometryAdapterResult:
    if not isinstance(response, dict):
        return _failure_result(
            "failed",
            "rhino_compute_response_malformed",
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    execution_status = response.get("execution_status")
    if execution_status not in {"completed", "failed", "timeout"}:
        return _failure_result(
            "failed",
            "rhino_compute_response_malformed",
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    if execution_status == "timeout":
        return _failure_result(
            "timeout",
            "rhino_compute_timeout",
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    reason_code = _string_or_none(response.get("reason_code"))
    if execution_status == "failed":
        return _failure_result(
            "failed",
            reason_code or "rhino_compute_execution_failed",
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    response_assets = response.get("assets", {})
    response_gates = response.get("geometry_gates", ())
    if not isinstance(response_assets, (dict, list)) or not isinstance(
        response_gates,
        (dict, list, tuple),
    ):
        return _failure_result(
            "failed",
            "rhino_compute_response_malformed",
            output_targets,
            pending_geometry_checks,
            base_debug_trace,
        )

    assets = _completed_assets(response_assets, output_targets)
    gates = _completed_gates(response_gates, pending_geometry_checks)
    ignored_gate_codes = sorted(
        set(_gate_response_map(response_gates)).difference(pending_geometry_checks)
    )

    return GeometryAdapterResult(
        execution_status="completed",
        assets=assets,
        geometry_gates=gates,
        reason_code=reason_code,
        debug_trace={
            **base_debug_trace,
            "adapter_execution_status": "completed",
            "adapter_reason_code": reason_code,
            "ignored_gate_codes": ignored_gate_codes,
        },
    )


def _failure_result(
    execution_status: str,
    reason_code: str,
    output_targets: tuple[str, ...],
    pending_geometry_checks: tuple[str, ...],
    base_debug_trace: dict[str, object],
) -> GeometryAdapterResult:
    return GeometryAdapterResult(
        execution_status=execution_status,
        assets=tuple(
            OutputAsset(
                target=target,  # type: ignore[arg-type]
                status="failed",
                notes=reason_code,
            )
            for target in output_targets
            if target in {"GLB", "STL"}
        ),
        geometry_gates=tuple(
            GeometryGateStatus(
                code=code,
                status="failed",
                reason_code=reason_code,
                details={"source": "rhino_compute_http_adapter"},
            )
            for code in pending_geometry_checks
        ),
        reason_code=reason_code,
        debug_trace={
            **base_debug_trace,
            "adapter_execution_status": execution_status,
            "adapter_reason_code": reason_code,
        },
    )


def _pending_assets(output_targets: tuple[str, ...]) -> tuple[OutputAsset, ...]:
    return tuple(
        OutputAsset(
            target=target,  # type: ignore[arg-type]
            status="pending_generation",
            notes="Rhino/Grasshopper generation not implemented in foundation scaffold.",
        )
        for target in output_targets
        if target in {"GLB", "STL"}
    )


def _completed_assets(
    response_assets: object,
    output_targets: tuple[str, ...],
) -> tuple[OutputAsset, ...]:
    assets: list[OutputAsset] = []
    for target in output_targets:
        if target not in {"GLB", "STL"}:
            continue
        path = _asset_path(response_assets, target)
        notes = None
        if path is None:
            notes = "Rhino.Compute completed without a generated asset path."
        assets.append(
            OutputAsset(
                target=target,  # type: ignore[arg-type]
                status="generated" if path else "pending_generation",
                path=path,
                notes=notes,
            )
        )
    return tuple(assets)


def _asset_path(response_assets: object, target: str) -> str | None:
    if isinstance(response_assets, dict):
        value = response_assets.get(target)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            path = value.get("path")
            if isinstance(path, str) and path:
                return path
    if isinstance(response_assets, list):
        for item in response_assets:
            if not isinstance(item, dict) or item.get("target") != target:
                continue
            path = item.get("path")
            if isinstance(path, str) and path:
                return path
    return None


def _completed_gates(
    response_gates: object,
    pending_geometry_checks: tuple[str, ...],
) -> tuple[GeometryGateStatus, ...]:
    gate_responses = _gate_response_map(response_gates)
    return tuple(
        _map_gate_status(code, gate_responses.get(code))
        for code in pending_geometry_checks
    )


def _gate_response_map(response_gates: object) -> dict[str, dict[str, object]]:
    if isinstance(response_gates, dict):
        return {
            code: value
            for code, value in response_gates.items()
            if isinstance(code, str) and isinstance(value, dict)
        }
    if isinstance(response_gates, (list, tuple)):
        mapped: dict[str, dict[str, object]] = {}
        for value in response_gates:
            if not isinstance(value, dict):
                continue
            code = value.get("code")
            if isinstance(code, str):
                mapped[code] = value
        return mapped
    return {}


def _map_gate_status(code: str, response: dict[str, object] | None) -> GeometryGateStatus:
    if response is None:
        return GeometryGateStatus(
            code=code,
            status="pending",
            reason_code="check_not_implemented",
            details={"source": "rhino_compute_http_adapter"},
        )

    requested_status = response.get("status")
    reason_code = _string_or_none(response.get("reason_code"))
    raw_details = response.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    executed_check = _string_or_none(response.get("executed_check")) or _string_or_none(
        details.get("executed_check") if isinstance(details, dict) else None
    )
    calibrated = response.get("calibrated")
    if calibrated is None and isinstance(details, dict):
        calibrated = details.get("calibrated")

    if requested_status == "passed":
        if executed_check and calibrated is True:
            return GeometryGateStatus(
                code=code,
                status="passed",
                reason_code=reason_code or "calibrated_check_passed",
                details={**details, "executed_check": executed_check, "calibrated": True},
            )
        return GeometryGateStatus(
            code=code,
            status="pending",
            reason_code="threshold_not_calibrated",
            details={**details, "requested_status": "passed"},
        )

    if requested_status in {"pending", "not_run", "failed"}:
        default_reason_codes = {
            "pending": "check_not_implemented",
            "not_run": "rhino_definition_not_executed",
            "failed": "rhino_geometry_gate_failed",
        }
        return GeometryGateStatus(
            code=code,
            status=requested_status,  # type: ignore[arg-type]
            reason_code=reason_code or default_reason_codes[requested_status],
            details=details,
        )

    return GeometryGateStatus(
        code=code,
        status="failed",
        reason_code="rhino_compute_response_malformed",
        details={"source": "rhino_compute_http_adapter"},
    )


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
