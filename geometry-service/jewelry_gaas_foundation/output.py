"""Output metadata contracts for generated job packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


OutputTarget = Literal["GLB", "STL"]
AssetStatus = Literal["pending_generation", "generated", "failed"]
GeometryGateState = Literal["not_run", "pending", "passed", "failed"]


@dataclass(frozen=True)
class OutputAsset:
    """An output asset entry in the handoff package."""

    target: OutputTarget
    status: AssetStatus
    path: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class GeometryGateStatus:
    """A geometry/manufacturing gate status reported by the solver boundary."""

    code: str
    status: GeometryGateState
    reason_code: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputPackageMetadata:
    """Metadata returned by a solver pipeline."""

    job_id: str
    archetype: str
    spec_version: str
    validation_status: Literal["accepted", "rejected"]
    assets: tuple[OutputAsset, ...]
    technical_summary: dict[str, object]
    geometry_gates: tuple[GeometryGateStatus, ...] = ()
    debug_trace: dict[str, object] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    rejection_reasons: tuple[str, ...] = ()
