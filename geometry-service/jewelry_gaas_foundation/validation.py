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
    _validate_modular_contract(spec, issues)
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
            quantity = stone.get("quantity", 1)
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
                quantity = 1
            role_counts[role] = role_counts.get(role, 0) + quantity

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


def _validate_modular_contract(
    spec: dict[str, Any], issues: list[ValidationIssue]
) -> None:
    """Validate additive modular references and schedule semantics.

    Legacy v0.1 specs do not carry these fields and remain valid. Once a
    modular field is present, its related fields must be explicit so geometry
    behavior cannot hide inside a display label.
    """
    stones = spec.get("stones")
    setting = spec.get("setting")
    ring = spec.get("ring")
    components = spec.get("components")
    if not isinstance(stones, list) or not isinstance(setting, dict) or not isinstance(ring, dict):
        return

    ids: dict[str, dict[str, Any]] = {}
    for index, stone in enumerate(stones):
        if not isinstance(stone, dict):
            continue
        stone_id = stone.get("stone_id")
        if not isinstance(stone_id, str):
            continue
        if stone_id in ids:
            issues.append(
                ValidationIssue(
                    "modular_stone_id_duplicate",
                    f"stone_id '{stone_id}' must be unique.",
                    f"$.stones[{index}].stone_id",
                )
            )
        ids[stone_id] = stone

        quantity = stone.get("quantity", 1)
        placement = stone.get("placement")
        role = stone.get("role")
        if role == "center" and placement is not None and (
            not isinstance(placement, dict)
            or placement.get("region") != "center"
            or placement.get("pattern") != "single"
            or quantity != 1
        ):
            issues.append(
                ValidationIssue(
                    "modular_center_placement_invalid",
                    "The center schedule must contain one stone at the center with a single placement pattern.",
                    f"$.stones[{index}]",
                )
            )
        if role == "side" and quantity > 1 and (
            not isinstance(placement, dict)
            or placement.get("region") != "head_sides"
            or placement.get("pattern") != "symmetric_pair"
        ):
            issues.append(
                ValidationIssue(
                    "modular_side_schedule_invalid",
                    "An aggregate side-stone schedule must use head_sides/symmetric_pair placement semantics.",
                    f"$.stones[{index}].placement",
                )
            )

    head_fields = ("head_family", "head_geometry", "gallery_structure")
    present_head_fields = [name for name in head_fields if setting.get(name) is not None]
    if present_head_fields and len(present_head_fields) != len(head_fields):
        issues.append(
            ValidationIssue(
                "modular_head_contract_incomplete",
                "head_family, head_geometry, and gallery_structure must be declared together.",
                "$.setting",
            )
        )

    pave = ring.get("pave")
    if isinstance(pave, dict):
        if ring.get("band_profile") is None or ring.get("shoulder_style") is None:
            issues.append(
                ValidationIssue(
                    "modular_band_contract_incomplete",
                    "A pavé schedule requires structured band_profile and shoulder_style fields.",
                    "$.ring",
                )
            )
        for stone_id in pave.get("accent_stone_ids") or []:
            referenced = ids.get(stone_id)
            if not isinstance(referenced, dict) or referenced.get("role") != "accent":
                issues.append(
                    ValidationIssue(
                        "modular_pave_reference_invalid",
                        f"pavé accent_stone_id '{stone_id}' must reference an accent-role stone schedule.",
                        "$.ring.pave.accent_stone_ids",
                    )
                )

    if isinstance(components, list):
        component_ids: set[str] = set()
        component_roles: set[str] = set()
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                continue
            component_id = component.get("component_id")
            role = component.get("role")
            if component_id in component_ids or role in component_roles:
                issues.append(
                    ValidationIssue(
                        "modular_component_duplicate",
                        "Component IDs and roles must be unique within a modular spec.",
                        f"$.components[{index}]",
                    )
                )
            if isinstance(component_id, str):
                component_ids.add(component_id)
            if isinstance(role, str):
                component_roles.add(role)

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
