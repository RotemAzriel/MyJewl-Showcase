"""Canonical-spec validation for Jewelry GaaS v0.1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from .archetypes import ARCHETYPE_CONTRACTS


Severity = Literal["error", "warning", "info"]

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "canonical_spec_v0_1.schema.json"
)


@dataclass(frozen=True)
class ValidationIssue:
    """A validation issue with a stable machine-readable code."""

    code: str
    message: str
    path: str
    severity: Severity = "error"


@dataclass(frozen=True)
class ValidationResult:
    """Validation outcome for a canonical job spec."""

    accepted: bool
    issues: tuple[ValidationIssue, ...]
    pending_geometry_checks: tuple[str, ...]


def validate_job_spec(spec: dict[str, Any]) -> ValidationResult:
    """Validate schema requirements and declare pending geometry gates.

    JSON Schema is the baseline single source of truth for canonical structure,
    types, enum-like fields, numeric positivity, and unsupported properties.
    Python validation only adds archetype-level role checks and foundation
    readiness notes that are awkward to express cleanly in the schema.
    """

    issues: list[ValidationIssue] = []
    issues.extend(_validate_schema(spec))
    _validate_archetype_contracts(spec, issues)
    _validate_manufacturing_profile_readiness(spec.get("manufacturing_profile"), issues)

    pending_geometry_checks = (
        "minimum_structural_floors",
        "stone_stone_collision",
        "stone_metal_collision",
        "mountability_and_setting_access",
        "mesh_closedness_and_manifold_suitability",
    )

    return ValidationResult(
        accepted=not any(issue.severity == "error" for issue in issues),
        issues=tuple(issues),
        pending_geometry_checks=pending_geometry_checks,
    )


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_schema(spec: dict[str, Any]) -> tuple[ValidationIssue, ...]:
    validator = _schema_validator()
    errors = sorted(
        validator.iter_errors(spec),
        key=lambda error: (list(error.absolute_path), error.validator, error.message),
    )
    return tuple(
        ValidationIssue(
            "schema_validation_failed",
            error.message,
            _json_path(error.absolute_path),
        )
        for error in errors
    )


def _json_path(path_parts: Any) -> str:
    path = "$"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _validate_archetype_contracts(
    spec: dict[str, Any], issues: list[ValidationIssue]
) -> None:
    job = spec.get("job")
    setting = spec.get("setting")
    stones = spec.get("stones")

    if not isinstance(job, dict) or not isinstance(setting, dict) or not isinstance(stones, list):
        return

    archetype = job.get("archetype")
    if archetype not in ARCHETYPE_CONTRACTS:
        return

    role_counts: dict[str, int] = {}
    for stone in stones:
        if not isinstance(stone, dict):
            continue
        role = stone.get("role")
        if isinstance(role, str):
            role_counts[role] = role_counts.get(role, 0) + 1

    contract = ARCHETYPE_CONTRACTS[archetype]
    for role, expected_count in contract.required_role_counts.items():
        actual_count = role_counts.get(role, 0)
        if actual_count != expected_count:
            issues.append(
                ValidationIssue(
                    "archetype_role_count_invalid",
                    f"{archetype} requires exactly {expected_count} {role} stone(s); found {actual_count}.",
                    "$.stones",
                )
            )

    for role, minimum_count in contract.min_role_counts.items():
        actual_count = role_counts.get(role, 0)
        if actual_count < minimum_count:
            issues.append(
                ValidationIssue(
                    "archetype_role_count_too_low",
                    f"{archetype} requires at least {minimum_count} {role} stone(s); found {actual_count}.",
                    "$.stones",
                )
            )

    for field_name in contract.required_setting_fields:
        if setting.get(field_name) in (None, "", "none"):
            issues.append(
                ValidationIssue(
                    "archetype_setting_field_missing",
                    f"{archetype} requires setting.{field_name}.",
                    f"$.setting.{field_name}",
                )
            )


def _validate_manufacturing_profile_readiness(
    value: Any, issues: list[ValidationIssue]
) -> None:
    if value is None:
        issues.append(
            ValidationIssue(
                "manufacturing_profile_unverified",
                "Manufacturing compensation is deferred until calibrated shop profiles are available.",
                "$.manufacturing_profile",
                "info",
            )
        )
