"""Foundation contracts for Jewelry Geometry-as-a-Service v0.1."""

from .archetypes import ARCHETYPE_CONTRACTS, ArchetypeContract
from .constraints import ConstraintResult, ConstraintViolation, check_constraints, validate
from .geometry_adapter import (
    GeometryAdapter,
    GeometryAdapterResult,
    NoOpGeometryAdapter,
    RhinoComputeGrasshopperAdapter,
    RhinoComputeHttpAdapter,
)
from .output import GeometryGateStatus
from .solver import solve_job, solve_solitaire
from .validation import ValidationIssue, ValidationResult, validate_job_spec

__all__ = [
    "ARCHETYPE_CONTRACTS",
    "ArchetypeContract",
    "ConstraintResult",
    "ConstraintViolation",
    "GeometryAdapter",
    "GeometryAdapterResult",
    "GeometryGateStatus",
    "NoOpGeometryAdapter",
    "RhinoComputeGrasshopperAdapter",
    "RhinoComputeHttpAdapter",
    "ValidationIssue",
    "ValidationResult",
    "check_constraints",
    "solve_job",
    "solve_solitaire",
    "validate",
    "validate_job_spec",
]
