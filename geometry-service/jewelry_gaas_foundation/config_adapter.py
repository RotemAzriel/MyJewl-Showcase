"""Flat dataset config -> canonical spec adapter (G-6).

The pre-baked dataset stores a *flat* ``parameters.json`` label schema
(``data_pipeline_architecture.md`` §4.2), but ``constraints.validate`` and
``validation.validate_job_spec`` expect the *nested* canonical spec v0.1
(``schemas/canonical_spec_v0_1.schema.json``). Running ``validate`` on a raw flat
config silently passes everything — guards see empty nested dicts (R-10).

``flat_to_canonical`` converts the flat config into the nested canonical shape so
the constraint filter actually evaluates the config. Missing required fields raise
``ValueError`` rather than emitting a partial canonical that would re-introduce the
silent no-op.
"""

from __future__ import annotations

import copy
from typing import Any

# Flat fields required to build a canonical spec the constraint engine can
# meaningfully evaluate. (carat, prong_count, metal_alloy, config_id are optional
# pass-through / derived below.)
_REQUIRED_FLAT_FIELDS = (
    "archetype",
    "stone_shape",
    "stone_length_mm",
    "stone_width_mm",
    "stone_depth_mm",
    "center_setting",
    "metal_type",
    "band_width_mm",
    "band_thickness_mm",
    "ring_size_us",
)


def flat_to_canonical(flat: dict[str, Any]) -> dict[str, Any]:
    """Convert a flat ``parameters.json`` dict into a nested canonical spec dict.

    Raises ``ValueError`` if ``flat`` is not a dict or is missing a required field.
    """
    if not isinstance(flat, dict):
        raise ValueError("flat_to_canonical: expected a dict config.")

    missing = [k for k in _REQUIRED_FLAT_FIELDS if flat.get(k) is None]
    if missing:
        raise ValueError(
            "flat_to_canonical: missing required field(s): " + ", ".join(missing)
        )

    job_id = str(flat.get("config_id") or "").strip() or "adapted_config"

    dimensions: dict[str, Any] = {
        "length": flat["stone_length_mm"],
        "width": flat["stone_width_mm"],
        "depth": flat["stone_depth_mm"],
    }

    center_stone: dict[str, Any] = {
        "stone_id": "center",
        "role": "center",
        "shape": flat["stone_shape"],
        "dimensions_mm": dimensions,
        "quantity": 1,
        "placement": {
            "region": "center",
            "pattern": "single",
            "orientation": "upright",
        },
    }
    if flat.get("stone_carat_eq") is not None:
        center_stone["carat"] = flat["stone_carat_eq"]

    setting: dict[str, Any] = {"center_setting": flat["center_setting"]}
    if flat.get("prong_count") is not None:
        setting["prong_count"] = flat["prong_count"]
    for flat_key, canonical_key in (
        ("head_family", "head_family"),
        ("head_geometry", "head_geometry"),
        ("gallery_structure", "gallery_structure"),
        ("side_stone_layout", "side_stone_layout"),
        ("side_setting", "side_setting"),
    ):
        if flat.get(flat_key) is not None:
            setting[canonical_key] = flat[flat_key]

    metal: dict[str, Any] = {"type": flat["metal_type"]}
    if flat.get("metal_alloy") is not None:
        # Keep platinum detectable by constraints._is_platinum (startswith "pt" /
        # contains "platinum"), e.g. "pt950" / "platinum_950".
        metal["alloy"] = str(flat["metal_alloy"]).replace("_", " ").strip()
    if flat.get("metal_purity") is not None:
        metal["purity"] = flat["metal_purity"]
    if flat.get("metal_finish") is not None:
        metal["finish"] = flat["metal_finish"]

    ring: dict[str, Any] = {
        "size_standard": "US",
        "size": flat["ring_size_us"],
        "band_width_mm": flat["band_width_mm"],
        "band_thickness_mm": flat["band_thickness_mm"],
    }
    if flat.get("band_profile") is not None:
        ring["profile"] = str(flat["band_profile"]).strip()
    if flat.get("band_profile_code") is not None:
        ring["band_profile"] = flat["band_profile_code"]
    if flat.get("shoulder_style") is not None:
        ring["shoulder_style"] = flat["shoulder_style"]
    if isinstance(flat.get("pave"), dict):
        ring["pave"] = copy.deepcopy(flat["pave"])

    stones = [center_stone]
    schedules = flat.get("stone_schedules")
    if schedules is not None:
        if not isinstance(schedules, list) or not all(
            isinstance(schedule, dict) for schedule in schedules
        ):
            raise ValueError("flat_to_canonical: stone_schedules must be a list of objects.")
        stones.extend(copy.deepcopy(schedules))

    canonical = {
        "spec_version": "0.1",
        "job": {
            "job_id": job_id,
            "archetype": flat["archetype"],
            "output_targets": ["STL"],
        },
        "ring": ring,
        "metal": metal,
        "stones": stones,
        "setting": setting,
    }
    components = flat.get("components")
    if components is not None:
        if not isinstance(components, list) or not all(
            isinstance(component, dict) for component in components
        ):
            raise ValueError("flat_to_canonical: components must be a list of objects.")
        canonical["components"] = copy.deepcopy(components)
    return canonical
