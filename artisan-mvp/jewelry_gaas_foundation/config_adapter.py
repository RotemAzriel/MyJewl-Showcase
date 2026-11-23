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
    }
    if flat.get("stone_carat_eq") is not None:
        center_stone["carat"] = flat["stone_carat_eq"]

    setting: dict[str, Any] = {"center_setting": flat["center_setting"]}
    if flat.get("prong_count") is not None:
        setting["prong_count"] = flat["prong_count"]

    metal: dict[str, Any] = {"type": flat["metal_type"]}
    if flat.get("metal_alloy") is not None:
        # Keep platinum detectable by constraints._is_platinum (startswith "pt" /
        # contains "platinum"), e.g. "pt950" / "platinum_950".
        metal["alloy"] = str(flat["metal_alloy"]).replace("_", " ").strip()

    return {
        "spec_version": "0.1",
        "job": {
            "job_id": job_id,
            "archetype": flat["archetype"],
            "output_targets": ["STL"],
        },
        "ring": {
            "size_standard": "US",
            "size": flat["ring_size_us"],
            "band_width_mm": flat["band_width_mm"],
            "band_thickness_mm": flat["band_thickness_mm"],
            **({"shank_style": flat["shank_style"]} if flat.get("shank_style") else {}),
        },
        "metal": metal,
        "stones": [center_stone],
        "setting": setting,
    }
