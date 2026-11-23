"""Archetype contracts for launch rings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchetypeContract:
    """Role-count and setting expectations for an archetype."""

    name: str
    required_role_counts: dict[str, int]
    min_role_counts: dict[str, int]
    required_setting_fields: tuple[str, ...]
    special_rules: tuple[str, ...]


ARCHETYPE_CONTRACTS: dict[str, ArchetypeContract] = {
    "solitaire": ArchetypeContract(
        name="solitaire",
        required_role_counts={"center": 1},
        min_role_counts={},
        required_setting_fields=("center_setting",),
        special_rules=(
            "validate_center_stone_fit",
            "validate_prong_or_bezel_compatibility",
            "validate_stone_metal_collision",
            "validate_minimum_structurals",
        ),
    ),
    "halo": ArchetypeContract(
        name="halo",
        required_role_counts={"center": 1},
        min_role_counts={"halo": 1},
        required_setting_fields=("center_setting",),
        special_rules=(
            "validate_halo_to_center_spacing",
            "validate_halo_layout_consistency",
            "validate_stone_stone_collision",
            "validate_setting_access",
        ),
    ),
    "trilogy": ArchetypeContract(
        name="trilogy",
        required_role_counts={"center": 1, "side": 2},
        min_role_counts={},
        required_setting_fields=("center_setting", "side_stone_layout"),
        special_rules=(
            "validate_side_stone_symmetry",
            "validate_side_stone_fit",
            "validate_center_side_spacing",
            "validate_mountability",
        ),
    ),
}

