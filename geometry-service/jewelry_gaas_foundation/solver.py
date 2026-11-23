"""Minimal solver-path scaffolding.

The actual Rhino/Grasshopper geometry solve is intentionally not implemented
here. This module validates the canonical job and returns the output package
shape expected from the future compute pipeline while keeping real geometry
execution behind an explicit adapter boundary.
"""

from __future__ import annotations

from typing import Any

from .archetypes import ARCHETYPE_CONTRACTS
from .geometry_adapter import (
    GeometryAdapter,
    GeometryAdapterResult,
    NoOpGeometryAdapter,
)
from .output import OutputAsset, OutputPackageMetadata
from .validation import validate_job_spec


SOLVER_NAME = "launch_archetype_v0_rhino_boundary"


def solve_job(
    spec: dict[str, Any],
    geometry_adapter: GeometryAdapter | None = None,
) -> OutputPackageMetadata:
    """Validate and prepare a minimal launch-archetype output package."""

    validation = validate_job_spec(spec)
    job = spec.get("job", {})
    job_id = str(job.get("job_id", "unknown"))
    archetype = str(job.get("archetype", "unknown"))
    spec_version = str(spec.get("spec_version", "unknown"))
    validation_issue_codes = tuple(issue.code for issue in validation.issues)
    adapter = geometry_adapter or NoOpGeometryAdapter()
    explicit_adapter_invoked = geometry_adapter is not None
    debug_trace = {
        "solver": SOLVER_NAME,
        "archetype": archetype,
        "validation_issue_codes": list(validation_issue_codes),
        "adapter_name": adapter.name,
        "adapter_invoked": False,
    }

    if not validation.accepted:
        return OutputPackageMetadata(
            job_id=job_id,
            archetype=archetype,
            spec_version=spec_version,
            validation_status="rejected",
            assets=(),
            technical_summary={
                "solver": SOLVER_NAME,
                "validation_issue_codes": list(validation_issue_codes),
            },
            debug_trace=debug_trace,
            rejection_reasons=tuple(
                issue.code for issue in validation.issues if issue.severity == "error"
            ),
        )

    if archetype not in ARCHETYPE_CONTRACTS:
        return OutputPackageMetadata(
            job_id=job_id,
            archetype=archetype,
            spec_version=spec_version,
            validation_status="rejected",
            assets=(),
            technical_summary={
                "solver": SOLVER_NAME,
                "message": "This solver path only accepts configured launch archetypes.",
            },
            debug_trace=debug_trace,
            rejection_reasons=("unsupported_archetype_for_solver",),
        )

    output_targets = _output_targets(job.get("output_targets", ()))
    adapter_result = _evaluate_adapter(
        adapter,
        spec,
        validation.pending_geometry_checks,
        output_targets,
    )
    debug_trace = {
        **debug_trace,
        **adapter_result.debug_trace,
        "adapter_invoked": explicit_adapter_invoked,
    }

    return OutputPackageMetadata(
        job_id=job_id,
        archetype=archetype,
        spec_version=spec_version,
        validation_status="accepted",
        assets=adapter_result.assets,
        technical_summary={
            "solver": SOLVER_NAME,
            "ring_size_standard": spec["ring"]["size_standard"],
            "ring_size": spec["ring"]["size"],
            "metal_type": spec["metal"]["type"],
            "center_stone_shape": _center_stone(spec)["shape"],
            "center_stone_dimensions_mm": _center_stone(spec)["dimensions_mm"],
            "pending_geometry_checks": list(validation.pending_geometry_checks),
            "adapter_execution_status": adapter_result.execution_status,
        },
        geometry_gates=adapter_result.geometry_gates,
        debug_trace=debug_trace,
    )


def solve_solitaire(spec: dict[str, Any]) -> OutputPackageMetadata:
    """Validate and prepare a minimal Solitaire output package."""

    job = spec.get("job", {})
    archetype = str(job.get("archetype", "unknown"))
    if archetype != "solitaire":
        return OutputPackageMetadata(
            job_id=str(job.get("job_id", "unknown")),
            archetype=archetype,
            spec_version=str(spec.get("spec_version", "unknown")),
            validation_status="rejected",
            assets=(),
            technical_summary={
                "solver": "solitaire_v0_compatibility_wrapper",
                "message": "This solver path only accepts solitaire jobs.",
            },
            debug_trace={
                "solver": "solitaire_v0_compatibility_wrapper",
                "archetype": archetype,
                "validation_issue_codes": [],
                "adapter_name": None,
                "adapter_invoked": False,
            },
            rejection_reasons=("unsupported_archetype_for_solver",),
        )

    return solve_job(spec)


def _evaluate_adapter(
    adapter: GeometryAdapter,
    spec: dict[str, Any],
    pending_geometry_checks: tuple[str, ...],
    output_targets: tuple[str, ...],
) -> GeometryAdapterResult:
    if hasattr(adapter, "evaluate_geometry"):
        return adapter.evaluate_geometry(spec, pending_geometry_checks, output_targets)

    geometry_gates = adapter.evaluate_geometry_gates(  # type: ignore[attr-defined]
        spec,
        pending_geometry_checks,
    )
    return GeometryAdapterResult(
        execution_status="completed",
        assets=_pending_assets(output_targets),
        geometry_gates=geometry_gates,
        debug_trace={
            "adapter_execution_status": "completed",
            "adapter_reason_code": None,
        },
    )


def _output_targets(targets: Any) -> tuple[str, ...]:
    if not isinstance(targets, (list, tuple)):
        return ()

    return tuple(str(target) for target in targets)


def _pending_assets(targets: tuple[str, ...]) -> tuple[OutputAsset, ...]:
    return tuple(
        OutputAsset(
            target=target,  # type: ignore[arg-type]
            status="pending_generation",
            notes="Rhino/Grasshopper generation not implemented in foundation scaffold.",
        )
        for target in targets
        if target in {"GLB", "STL"}
    )


def _center_stone(spec: dict[str, Any]) -> dict[str, Any]:
    return next(stone for stone in spec["stones"] if stone["role"] == "center")
