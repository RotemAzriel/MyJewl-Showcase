"""Deterministic internal modular ring configurator (Goals 0006–0007).

The catalog records below are application authority and map deterministically
into canonical spec v0.1. Display names never stand in for geometry: head,
gallery, side/accent schedules, band profile, shoulders, pavé, and component
materials are explicit fields. Rhino/Grasshopper remains the geometry authority
and every geometry gate remains non-certifying until calibrated review.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fast_mvp.pipeline import StoneInputs, _parse_stone_inputs
from fast_mvp.preview_resolution import attach_preview_manifest
from fast_mvp.quote_volumes import attach_quote
from fast_mvp.typed_inputs import (
    METAL_COLOR_FAMILIES,
    METAL_FINISHES,
    METAL_PURITIES,
    MetalSpec,
    StoneCertificate,
    apply_certificate,
    parse_metal_spec,
    preview_metal,
    quote_basis,
    stone_spec_source,
)
from jewelry_gaas_foundation import constraints, master_parameters
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.validation import validate_job_spec


CATALOG_REVISION = "modular-rings-v5"
SUPPORTED_SHAPES = (
    "round",
    "oval",
    "cushion",
    "pear",
    "emerald",
    "princess",
    "radiant",
)

GEOMETRY_GATES = {
    "minimum_structural_floors": "pending",
    "stone_metal_collision": "pending",
    "stone_stone_collision": "pending",
    "mountability_and_setting_access": "pending",
    "mesh_closedness_and_manifold_suitability": "pending",
}


class ManualConfiguratorError(ValueError):
    """A manual selection is incomplete or outside the internal catalog."""


@dataclass(frozen=True)
class StoneSchedule:
    schedule_id: str
    role: str
    shape: str
    quantity: int
    length_mm: float
    width_mm: float
    depth_mm: float
    region: str
    pattern: str
    orientation: str

    def to_json(self) -> dict[str, Any]:
        return {
            "stone_id": self.schedule_id,
            "role": self.role,
            "shape": self.shape,
            "quantity": self.quantity,
            "dimensions_mm": {
                "length": self.length_mm,
                "width": self.width_mm,
                "depth": self.depth_mm,
            },
            "placement": {
                "region": self.region,
                "pattern": self.pattern,
                "orientation": self.orientation,
            },
        }


@dataclass(frozen=True)
class PaveSchedule:
    coverage: str
    setting_style: str
    accent_stone_schedule: StoneSchedule | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "setting_style": self.setting_style,
            "accent_stone_ids": (
                [self.accent_stone_schedule.schedule_id]
                if self.accent_stone_schedule
                else []
            ),
            "accent_stone_schedule": (
                self.accent_stone_schedule.to_json()
                if self.accent_stone_schedule
                else None
            ),
        }

    def canonical_json(self) -> dict[str, Any]:
        value = self.to_json()
        value.pop("accent_stone_schedule")
        return value


@dataclass(frozen=True)
class SolvedConfiguration:
    """One typed configuration a receipt-backed master was actually solved for.

    Goal 0015 listed these by hand. Goal 0016 derives them from the production
    preview registry instead (see :func:`solved_configurations`), so the catalog
    offer and the registry describe the same finite set *by construction* rather
    than by a test that checks two hand-maintained lists agree.
    """

    configuration_id: str
    stone_dimensions_mm: tuple[float, float, float]
    ring_size_us: float
    receipt_sha256: str

    def matches(self, stone: StoneInputs) -> bool:
        requested = (stone.length_mm, stone.width_mm, stone.depth_mm)
        return abs(stone.ring_size_us - self.ring_size_us) <= 0.001 and all(
            abs(actual - expected) <= 0.01
            for actual, expected in zip(requested, self.stone_dimensions_mm)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "stone_dimensions_mm": list(self.stone_dimensions_mm),
            "ring_size_us": self.ring_size_us,
            "receipt_sha256": self.receipt_sha256,
        }


@dataclass(frozen=True)
class ParametricMaster:
    """A receipt-backed Rhino master that derives geometry from the typed request.

    Goal 0016. Before this, the V5 pair was offered only at the three solved
    configurations, so a customer inside the master's envelope but outside the
    solved set could not even *describe* what they wanted — the catalog refused
    the selection and the intent was lost. The boundary now sits where the master
    itself puts it: :func:`master_parameters.derive_parameters` is the single gate,
    and anything it accepts can be expressed, priced, previewed honestly (as
    ``unavailable`` until it is solved), and queued for an operator to fulfill.

    This widens what can be *asked for*. It widens no claim: an unsolved
    configuration still resolves to no preview asset at all, and the app still
    never triggers paid compute.
    """

    template_id: str
    definition_path: str
    differential_id: str

    def typed_request(
        self,
        stone: StoneInputs,
        band_width_mm: float,
        band_thickness_mm: float,
    ) -> dict[str, Any]:
        """Return the typed request this master would receive for one selection."""
        return master_parameters.configuration_request(
            stone.ring_size_us,
            stone.length_mm,
            stone.depth_mm,
            band_width_mm,
            band_thickness_mm,
            template_id=self.template_id,
            stone_width_mm=stone.width_mm,
        )

    def refusal(
        self,
        stone: StoneInputs,
        band_width_mm: float,
        band_thickness_mm: float,
    ) -> str | None:
        """Return the master's own refusal message, or ``None`` if it would build."""
        try:
            master_parameters.derive_parameters(
                self.typed_request(stone, band_width_mm, band_thickness_mm)
            )
        except master_parameters.MasterParameterError as exc:
            return str(exc)
        return None

    def derived_parameters(
        self,
        stone: StoneInputs,
        band_width_mm: float,
        band_thickness_mm: float,
    ) -> dict[str, Any]:
        return master_parameters.derive_parameters(
            self.typed_request(stone, band_width_mm, band_thickness_mm)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "definition_path": self.definition_path,
            "differential_id": self.differential_id,
            "envelope": master_parameters.envelope(),
            "geometry_authority": "rhino_grasshopper",
            "manufacturing_authority": False,
        }


@dataclass(frozen=True)
class HeadStyle:
    style_id: str
    display_name: str
    description: str
    architecture: str
    supported_shapes: tuple[str, ...]
    center_setting: str
    prong_count: int
    head_family: str
    head_geometry: str
    gallery_structure: str
    side_setting: str
    side_stone_schedules: tuple[StoneSchedule, ...]
    min_stone_dimension_mm: float
    max_stone_dimension_mm: float
    reference_stone_dimensions_mm: tuple[float, float, float] | None = None
    reference_ring_size_us: float | None = None
    reference_template_id: str | None = None
    reference_receipt_sha256: str | None = None
    reference_differential_id: str | None = None
    parametric_master: ParametricMaster | None = None

    def refusal(self, stone: StoneInputs) -> str | None:
        """Return why this head cannot take the stone, or ``None`` if it can."""
        maximum = max(stone.length_mm, stone.width_mm)
        if stone.shape not in self.supported_shapes:
            return f"head style '{self.style_id}' does not support {stone.shape} stones."
        if not self.min_stone_dimension_mm <= maximum <= self.max_stone_dimension_mm:
            return (
                f"head style '{self.style_id}' supports {self.min_stone_dimension_mm}"
                f"–{self.max_stone_dimension_mm} mm stones, not {maximum} mm."
            )
        if self.parametric_master is not None:
            # The master is the authority on what it can build, so quote its own
            # refusal rather than maintaining a second copy of its envelope. The
            # reference band values keep this a question about the stone and the
            # ring size; each band re-checks with its own section below.
            return self.parametric_master.refusal(
                stone,
                master_parameters.REF_BAND_WIDTH,
                master_parameters.REF_BAND_THICKNESS,
            )
        if self.reference_stone_dimensions_mm is not None:
            requested = (stone.length_mm, stone.width_mm, stone.depth_mm)
            if any(
                abs(actual - expected) > 0.01
                for actual, expected in zip(requested, self.reference_stone_dimensions_mm)
            ):
                return (
                    f"head style '{self.style_id}' is bounded to its single approved "
                    "stone measurement."
                )
        if (
            self.reference_ring_size_us is not None
            and abs(stone.ring_size_us - self.reference_ring_size_us) > 0.001
        ):
            return (
                f"head style '{self.style_id}' is bounded to US "
                f"{self.reference_ring_size_us}."
            )
        return None

    def compatible(self, stone: StoneInputs) -> bool:
        return self.refusal(stone) is None

    def to_json(self) -> dict[str, Any]:
        result = {
            "head_style_id": self.style_id,
            "display_name": self.display_name,
            "description": self.description,
            "architecture": self.architecture,
            "supported_shapes": list(self.supported_shapes),
            "supported_stone_dimension_mm": [
                self.min_stone_dimension_mm,
                self.max_stone_dimension_mm,
            ],
            "head_family": self.head_family,
            "head_geometry": self.head_geometry,
            "gallery_structure": self.gallery_structure,
            "side_setting": self.side_setting,
            "side_stone_schedules": [
                schedule.to_json() for schedule in self.side_stone_schedules
            ],
            "canonical_mapping": {
                "job.archetype": self.architecture,
                "setting.center_setting": self.center_setting,
                "setting.prong_count": self.prong_count,
                "setting.head_family": self.head_family,
                "setting.head_geometry": self.head_geometry,
                "setting.gallery_structure": self.gallery_structure,
                "setting.side_stone_layout": (
                    "left_right_pair" if self.architecture == "trilogy" else "none"
                ),
                "setting.side_setting": self.side_setting,
            },
        }
        if self.reference_template_id:
            result["visual_reference"] = {
                "status": "approved",
                "authority": "receipt_backed_rhino_visual_reference",
                "template_id": self.reference_template_id,
                "receipt_sha256": self.reference_receipt_sha256,
                "differential_id": self.reference_differential_id,
                "bounded_stone_dimensions_mm": list(
                    self.reference_stone_dimensions_mm or ()
                ),
                "bounded_ring_size_us": self.reference_ring_size_us,
                "manufacturing_status": "pending_not_run_needs_calibration",
            }
        if self.parametric_master is not None:
            result["parametric_master"] = self.parametric_master.to_json()
        return result


@dataclass(frozen=True)
class BandStyle:
    style_id: str
    display_name: str
    description: str
    supported_shapes: tuple[str, ...]
    compatible_head_ids: tuple[str, ...]
    supported_architectures: tuple[str, ...]
    band_width_mm: float
    band_thickness_mm: float
    band_profile: str
    legacy_profile: str
    shoulder_style: str
    pave: PaveSchedule
    min_ring_size_us: float = 3.0
    max_ring_size_us: float = 13.0

    def refusal(self, stone: StoneInputs, head: HeadStyle) -> str | None:
        if not (
            stone.shape in self.supported_shapes
            and head.style_id in self.compatible_head_ids
            and head.architecture in self.supported_architectures
        ):
            return (
                f"band style '{self.style_id}' is not compatible with head style "
                f"'{head.style_id}'."
            )
        if not self.min_ring_size_us <= stone.ring_size_us <= self.max_ring_size_us:
            return (
                f"band style '{self.style_id}' supports US {self.min_ring_size_us}"
                f"–{self.max_ring_size_us}, not US {stone.ring_size_us}."
            )
        if head.parametric_master is not None:
            # Re-check with this band's own section: band width and thickness are
            # typed inputs to the master too, so a band the master would refuse
            # must not be offered with it.
            return head.parametric_master.refusal(
                stone, self.band_width_mm, self.band_thickness_mm
            )
        return None

    def compatible(self, stone: StoneInputs, head: HeadStyle) -> bool:
        return self.refusal(stone, head) is None

    def to_json(self) -> dict[str, Any]:
        result = {
            "band_style_id": self.style_id,
            "display_name": self.display_name,
            "description": self.description,
            "supported_shapes": list(self.supported_shapes),
            "compatible_head_style_ids": list(self.compatible_head_ids),
            "supported_architectures": list(self.supported_architectures),
            "supported_ring_size_us": [self.min_ring_size_us, self.max_ring_size_us],
            "band_profile": self.band_profile,
            "shoulder_style": self.shoulder_style,
            "pave": self.pave.to_json(),
            "canonical_mapping": {
                "ring.band_width_mm": self.band_width_mm,
                "ring.band_thickness_mm": self.band_thickness_mm,
                "ring.profile": self.legacy_profile,
                "ring.band_profile": self.band_profile,
                "ring.shoulder_style": self.shoulder_style,
                "ring.pave": self.pave.canonical_json(),
            },
        }
        return result


_PEAR_SIDE_PAIR = StoneSchedule(
    "side_pear_pair",
    "side",
    "pear",
    2,
    3.8,
    2.4,
    1.5,
    "head_sides",
    "symmetric_pair",
    "points_outward",
)

_ROUND_PAVE_12 = StoneSchedule(
    "band_round_accents_12",
    "accent",
    "round",
    12,
    1.1,
    1.1,
    0.7,
    "band",
    "mirrored_row",
    "radial",
)

_ROUND_PAVE_14 = StoneSchedule(
    "band_round_accents_14",
    "accent",
    "round",
    14,
    1.15,
    1.15,
    0.72,
    "band",
    "mirrored_row",
    "radial",
)

_NO_PAVE = PaveSchedule("none", "none")
_V4_HEAD_ID = "head_v4_mid_petal_4_prong"
_V4_BAND_ID = "band_v4_split_solitaire"
_V4_TEMPLATE_ID = "goal0011-realism-first-round-solitaire-master-v4"
_V4_RECEIPT_SHA256 = "1cb1c04a8994f7710ca17d567f32e7fc14f5169352a4ffa06bedac3031ea43a5"
_V4_DIFFERENTIAL_ID = "goal0011-v2-to-v4-receipt-differential"

_V5_HEAD_ID = "head_v5_parametric_petal_4_prong"
_V5_BAND_ID = "band_v5_parametric_split_solitaire"
_V5_TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"
_V5_DIFFERENTIAL_ID = "goal0014-v5-config-differential"
_V5_DEFINITION_PATH = "manual/rhino_compute_bridge/goal0014_parametric_master_v5.ghx"
_V5_ASSET_ID_PREFIX = "goal0014-v5-"

_V5_MASTER = ParametricMaster(
    _V5_TEMPLATE_ID,
    _V5_DEFINITION_PATH,
    _V5_DIFFERENTIAL_ID,
)

#: Cache key -> solved set, so the catalog does not re-read and re-hash the whole
#: production registry on every option request. Keyed on the registry file's
#: identity and mtime, so a promotion is picked up without a restart.
_SOLVED_CACHE: dict[Any, tuple[SolvedConfiguration, ...]] = {}


def _solved_from_asset(
    asset: dict[str, Any],
    template_id: str | None,
) -> SolvedConfiguration | None:
    """Read one solved configuration out of a promoted registry asset."""
    projection = asset.get("match", {}).get("geometry_projection")
    provenance = asset.get("provenance", {})
    if (
        not isinstance(projection, dict)
        or asset.get("classification") != "exact"
        or (template_id is not None and provenance.get("template_id") != template_id)
    ):
        return None
    stones = projection.get("stones")
    if not isinstance(stones, list) or len(stones) != 1:
        return None
    dimensions = stones[0].get("dimensions_mm") or {}
    try:
        measurements = (
            float(dimensions["length"]),
            float(dimensions["width"]),
            float(dimensions["depth"]),
        )
        ring_size = float(projection["ring"]["size"])
    except (KeyError, TypeError, ValueError):
        return None
    return SolvedConfiguration(
        str(asset["asset_id"]).removeprefix(_V5_ASSET_ID_PREFIX),
        measurements,
        ring_size,
        str(provenance.get("source_receipt_sha256") or ""),
    )


def solved_configurations(
    template_id: str | None = None,
    registry_path: Any = None,
) -> tuple[SolvedConfiguration, ...]:
    """Return the configurations that carry receipt-backed geometry today.

    Derived from the production preview registry rather than declared here, so
    promoting a solved configuration is what makes the catalog offer it as
    available — there is no second list to forget to update. A registry that
    cannot be loaded yields an empty set, which reads as "nothing is solved yet":
    every configuration is then offered made-to-order and nothing is claimed.
    """
    from fast_mvp.preview_resolution import (
        DEFAULT_REGISTRY_PATH,
        PreviewRegistryError,
        load_registry,
    )

    path = Path(registry_path) if registry_path is not None else DEFAULT_REGISTRY_PATH
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, template_id)
    except OSError:
        return ()
    if key in _SOLVED_CACHE:
        return _SOLVED_CACHE[key]
    try:
        registry = load_registry(path)
    except (PreviewRegistryError, OSError, ValueError):
        return ()
    found = tuple(
        sorted(
            (
                solved
                for solved in (
                    _solved_from_asset(asset, template_id)
                    for asset in registry["assets"]
                    if asset.get("enabled")
                    and asset.get("approval", {}).get("status") == "approved"
                )
                if solved is not None
            ),
            key=lambda item: item.configuration_id,
        )
    )
    _SOLVED_CACHE[key] = found
    return found


HEAD_STYLES = (
    HeadStyle(
        _V5_HEAD_ID,
        "Parametric V5 petal solitaire",
        "Receipt-backed four-prong petal head derived from the typed ring size and stone. "
        "Configurations already solved on Azure preview instantly; the rest are built to "
        "order from the same master.",
        "solitaire",
        ("round",),
        "prong",
        4,
        "petal",
        "petal_prong",
        "petal_open",
        "none",
        (),
        master_parameters.STONE_DIAMETER_MIN_MM,
        master_parameters.STONE_DIAMETER_MAX_MM,
        None,
        None,
        _V5_TEMPLATE_ID,
        None,
        _V5_DIFFERENTIAL_ID,
        _V5_MASTER,
    ),
    HeadStyle(
        _V4_HEAD_ID,
        "Approved V4 mid-petal solitaire",
        "Receipt-backed four-prong head with split shoulders joined into the mid-height basket petals.",
        "solitaire",
        ("round",),
        "prong",
        4,
        "petal",
        "petal_prong",
        "petal_open",
        "none",
        (),
        6.5,
        6.5,
        (6.5, 6.5, 3.9),
        6.5,
        _V4_TEMPLATE_ID,
        _V4_RECEIPT_SHA256,
        _V4_DIFFERENTIAL_ID,
    ),
    HeadStyle(
        "head_classic_4_prong",
        "Classic four-prong solitaire",
        "Open rail head with four evenly spaced center prongs.",
        "solitaire",
        SUPPORTED_SHAPES,
        "prong",
        4,
        "classic",
        "open_prong",
        "open_rail",
        "none",
        (),
        4.0,
        10.5,
    ),
    HeadStyle(
        "head_crown_6_prong",
        "Crown six-prong solitaire",
        "Six-prong crown head with a double gallery rail.",
        "solitaire",
        SUPPORTED_SHAPES,
        "prong",
        6,
        "crown",
        "crown_prong",
        "double_rail",
        "none",
        (),
        4.0,
        10.5,
    ),
    HeadStyle(
        "head_low_bezel",
        "Low bezel solitaire",
        "Continuous bezel wall with a continuous lower gallery.",
        "solitaire",
        SUPPORTED_SHAPES,
        "bezel",
        0,
        "bezel",
        "continuous_bezel",
        "continuous_wall",
        "none",
        (),
        4.0,
        9.5,
    ),
    HeadStyle(
        "head_petal_trilogy_4_prong",
        "Petal gallery trilogy",
        "Four-prong petal head with an open gallery and a symmetric pear side pair.",
        "trilogy",
        ("round",),
        "prong",
        4,
        "petal",
        "petal_prong",
        "petal_open",
        "prong",
        (_PEAR_SIDE_PAIR,),
        5.5,
        8.0,
    ),
)

_SOLITAIRE_PRONG_HEAD_IDS = tuple(
    head.style_id
    for head in HEAD_STYLES
    if head.architecture == "solitaire"
    and head.center_setting == "prong"
    and head.reference_template_id is None
)
_SOLITAIRE_HEAD_IDS = tuple(
    head.style_id
    for head in HEAD_STYLES
    if head.architecture == "solitaire" and head.reference_template_id is None
)
_PETAL_TRILOGY_HEAD_ID = "head_petal_trilogy_4_prong"

BAND_STYLES = (
    BandStyle(
        _V5_BAND_ID,
        "V5 parametric split solitaire band",
        "2.05 mm split shank whose four shoulders join the parametric V5 basket, "
        "derived from the typed ring size.",
        ("round",),
        (_V5_HEAD_ID,),
        ("solitaire",),
        2.05,
        1.65,
        "comfort_fit",
        "split-ring solitaire",
        "tapered_cathedral",
        _NO_PAVE,
        master_parameters.US_SIZE_MIN,
        master_parameters.US_SIZE_MAX,
    ),
    BandStyle(
        _V4_BAND_ID,
        "V4 split solitaire band",
        "Bounded 2.05 mm split shank whose four shoulders join the approved V4 mid-petal basket.",
        ("round",),
        (_V4_HEAD_ID,),
        ("solitaire",),
        2.05,
        1.65,
        "comfort_fit",
        "split-ring solitaire",
        "tapered_cathedral",
        _NO_PAVE,
        6.5,
        6.5,
    ),
    BandStyle(
        "band_classic_solitaire",
        "Classic solitaire band",
        "Polished comfort-fit shank with straight shoulders and no pavé.",
        SUPPORTED_SHAPES,
        _SOLITAIRE_HEAD_IDS,
        ("solitaire",),
        2.0,
        1.6,
        "comfort_fit",
        "comfort-fit solitaire",
        "straight",
        _NO_PAVE,
    ),
    BandStyle(
        "band_knife_edge",
        "Knife-edge solitaire band",
        "Structured knife-edge shank with tapered shoulders and no pavé.",
        SUPPORTED_SHAPES,
        _SOLITAIRE_PRONG_HEAD_IDS,
        ("solitaire",),
        2.2,
        1.7,
        "knife_edge",
        "knife-edge solitaire",
        "tapered",
        _NO_PAVE,
    ),
    BandStyle(
        "band_micro_pave",
        "Half-pavé comfort band",
        "Comfort-fit shank with tapered shoulders and a defined round accent schedule.",
        SUPPORTED_SHAPES,
        _SOLITAIRE_PRONG_HEAD_IDS,
        ("solitaire",),
        2.3,
        1.7,
        "comfort_fit",
        "micro-pave comfort-fit",
        "tapered",
        PaveSchedule("half", "bead_set", _ROUND_PAVE_12),
    ),
    BandStyle(
        "band_knife_edge_half_pave",
        "Knife-edge half-pavé band",
        "Knife-edge shank with tapered cathedral shoulders and a round half-pavé schedule.",
        ("round",),
        (_PETAL_TRILOGY_HEAD_ID,),
        ("trilogy",),
        2.4,
        1.8,
        "knife_edge",
        "knife-edge half-pave",
        "tapered_cathedral",
        PaveSchedule("half", "bead_set", _ROUND_PAVE_14),
    ),
)

REFERENCE_CONFIGURATIONS = (
    {
        "reference_configuration_id": "reference_goal0014_v5_parametric_solitaire",
        "display_name": "Goal 0014 V5 parametric petal solitaire",
        "authority": "myjewl_receipt_backed_rhino_visual_reference",
        "center_shape": "round",
        "head_style_id": _V5_HEAD_ID,
        "band_style_id": _V5_BAND_ID,
        "template_id": _V5_TEMPLATE_ID,
        "differential_id": _V5_DIFFERENTIAL_ID,
        "visual_review": "approved",
        "manufacturing_status": "pending_not_run_needs_calibration",
        "reference_scope": (
            "One parametric round-solitaire master. Configurations already solved on "
            "Azure carry receipt-backed geometry and preview exactly; every other "
            "configuration inside the master's envelope is offered made-to-order with "
            "no preview rather than approximated from a neighbouring solve. Not a "
            "manufacturing certification."
        ),
    },
    {
        "reference_configuration_id": "reference_petal_trilogy_knife_pave_v1",
        "display_name": "Petal trilogy with knife-edge pavé",
        "authority": "myjewl_internal",
        "center_shape": "round",
        "head_style_id": _PETAL_TRILOGY_HEAD_ID,
        "band_style_id": "band_knife_edge_half_pave",
        "reference_scope": (
            "Bounded internal visual/product reference; not retailer data and not a "
            "manufacturing certification."
        ),
    },
    {
        "reference_configuration_id": "reference_goal0011_v4_mid_petal_solitaire",
        "display_name": "Goal 0011 V4 approved mid-petal solitaire",
        "authority": "myjewl_receipt_backed_rhino_visual_reference",
        "center_shape": "round",
        "head_style_id": _V4_HEAD_ID,
        "band_style_id": _V4_BAND_ID,
        "template_id": _V4_TEMPLATE_ID,
        "receipt_sha256": _V4_RECEIPT_SHA256,
        "differential_id": _V4_DIFFERENTIAL_ID,
        "visual_review": "approved",
        "manufacturing_status": "pending_not_run_needs_calibration",
        "reference_scope": (
            "Bounded 6.5 x 6.5 x 3.9 mm round / US 6.5 visual reference; "
            "not a manufacturing certification."
        ),
    },
)

_HEAD_BY_ID = {head.style_id: head for head in HEAD_STYLES}
_BAND_BY_ID = {band.style_id: band for band in BAND_STYLES}

#: A configuration the master can build but nobody has solved yet. It is offered,
#: described, quoted, and queued — it is simply not previewed, because no
#: receipt-backed geometry exists for it and a neighbouring solve is not a
#: substitute for a typed input.
MADE_TO_ORDER = "made_to_order"
#: A configuration with its own receipt-backed asset in the production registry.
PREVIEW_AVAILABLE = "preview_available"
#: A style with no receipt-backed master behind it at all. Selecting it is valid
#: and gate-checked, but it yields neither a preview nor a derived work order —
#: an operator would have to author geometry for it first.
UNPROVEN = "unproven"


def preview_availability(head: HeadStyle, stone: StoneInputs) -> dict[str, Any]:
    """Say, before anything is configured, whether this selection can be previewed.

    This is a statement about *evidence*, not about taste: ``made_to_order`` means
    a proven master would build it and an operator can fulfill it, but no solved
    geometry exists yet, so no preview will be shown and no neighbour substituted.
    """
    match = (
        next(
            (
                item
                for item in solved_configurations(head.reference_template_id)
                if item.matches(stone)
            ),
            None,
        )
        if head.reference_template_id
        else None
    )
    if match is None and head.parametric_master is None:
        return {
            "status": UNPROVEN,
            "configuration_id": None,
            "fulfillable_by_master": None,
            "detail": (
                "No receipt-backed Rhino master covers this style yet, so this "
                "selection has no preview and no derivable geometry work order."
            ),
        }
    if match is not None:
        return {
            "status": PREVIEW_AVAILABLE,
            "configuration_id": match.configuration_id,
            "fulfillable_by_master": (
                head.parametric_master.template_id
                if head.parametric_master is not None
                else None
            ),
            "receipt_sha256": match.receipt_sha256,
            "detail": (
                "This configuration was solved on Azure and has its own "
                "receipt-backed preview asset."
            ),
        }
    return {
        "status": MADE_TO_ORDER,
        "configuration_id": None,
        "fulfillable_by_master": head.parametric_master.template_id,
        "detail": (
            "The parametric master can build this configuration, but it has not been "
            "solved yet, so there is no preview to show. Accepting the design queues "
            "an explicit geometry request an operator can fulfill; MyJewl will never "
            "show you a neighbouring size in its place."
        ),
    }


def _reviewed_stone(
    payload: dict[str, Any],
) -> tuple[StoneInputs, StoneCertificate | None]:
    raw_certificate = payload.get("certificate") or payload.get("stone_certificate")
    if isinstance(raw_certificate, dict):
        reviewed = (
            raw_certificate.get("reviewed") is True
            or payload.get("certificate_reviewed") is True
        )
        if not reviewed:
            raise ManualConfiguratorError(
                "certificate measurements must be reviewed before configuring a ring."
            )
    filled, certificate = apply_certificate(payload)
    stone = _parse_stone_inputs(filled)
    if stone.shape not in SUPPORTED_SHAPES:
        raise ManualConfiguratorError(
            "the bounded modular catalog supports round, oval, cushion, pear, "
            "emerald, princess, and radiant center stones."
        )
    return stone, certificate


def _has_explicit_metal(payload: dict[str, Any]) -> bool:
    nested = payload.get("metal") if isinstance(payload.get("metal"), dict) else {}
    alloy = nested.get("alloy") or payload.get("metal_alloy")
    if alloy not in (None, ""):
        return True
    family = (
        nested.get("color_family")
        or nested.get("color")
        or payload.get("metal_color_family")
        or payload.get("metal_color")
    )
    purity = (
        nested.get("purity")
        or nested.get("karat")
        or payload.get("metal_karat")
        or payload.get("metal_purity")
        or payload.get("metal_karat_or_purity")
    )
    return family not in (None, "") and purity not in (None, "")


def compatible_options(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only modular choices compatible with the typed stone and size."""
    stone, certificate = _reviewed_stone(payload)
    heads = [head for head in HEAD_STYLES if head.compatible(stone)]
    availability = {
        head.style_id: preview_availability(head, stone) for head in heads
    }
    bands_by_head = {
        head.style_id: [
            band.to_json() for band in BAND_STYLES if band.compatible(stone, head)
        ]
        for head in heads
    }
    available_head_ids = {head.style_id for head in heads}
    references = [
        dict(reference)
        for reference in REFERENCE_CONFIGURATIONS
        if reference["center_shape"] == stone.shape
        and reference["head_style_id"] in available_head_ids
        and any(
            band["band_style_id"] == reference["band_style_id"]
            for band in bands_by_head.get(reference["head_style_id"], [])
        )
    ]
    return {
        "catalog_revision": CATALOG_REVISION,
        "typed_stone": _typed_stone_json(stone, certificate),
        "head_styles": [
            {**head.to_json(), "preview_availability": availability[head.style_id]}
            for head in heads
        ],
        "band_styles_by_head": bands_by_head,
        "reference_configurations": references,
        "metal_choices": {
            "color_families": list(METAL_COLOR_FAMILIES),
            "purities": list(METAL_PURITIES),
            "finishes": list(METAL_FINISHES),
            "invalid_combination_policy": "pt950 is available only in white",
            "geometry_policy": (
                "Material and finish are explicit component assignments but are "
                "excluded from geometry identity."
            ),
        },
    }


def configure(payload: dict[str, Any]) -> dict[str, Any]:
    """Build and live-gate one explicit internal modular selection."""
    if not isinstance(payload, dict):
        raise ManualConfiguratorError("manual configurator payload must be a JSON object.")
    stone, certificate = _reviewed_stone(payload)
    if not _has_explicit_metal(payload):
        raise ManualConfiguratorError("metal colour and karat/purity are required.")
    metal = parse_metal_spec(payload)

    head_id = str(payload.get("head_style_id") or "").strip()
    band_id = str(payload.get("band_style_id") or "").strip()
    head = _HEAD_BY_ID.get(head_id)
    band = _BAND_BY_ID.get(band_id)
    if head is None:
        raise ManualConfiguratorError(
            f"unknown head_style_id: {head_id or '(missing)'}."
        )
    if band is None:
        raise ManualConfiguratorError(
            f"unknown band_style_id: {band_id or '(missing)'}."
        )
    head_refusal = head.refusal(stone)
    if head_refusal is not None:
        # Quote the real reason. When a parametric master refuses, this is its own
        # envelope message, so the customer learns what would work instead of being
        # told an unqualified "not compatible".
        raise ManualConfiguratorError(
            f"head style '{head_id}' is not compatible with this typed stone: "
            f"{head_refusal}"
        )
    band_refusal = band.refusal(stone, head)
    if band_refusal is not None:
        raise ManualConfiguratorError(
            f"band style '{band_id}' is not compatible with head style '{head_id}': "
            f"{band_refusal}"
        )

    geometry_identity = _geometry_identity(stone, head, band)
    config_id = (
        f"manual_{geometry_identity.removeprefix('geo_v2_')}_"
        f"{metal.alloy}_{metal.finish}"
    )
    flat = _assemble_flat(
        stone,
        metal,
        head,
        band,
        config_id=config_id,
        geometry_identity=geometry_identity,
    )
    canonical = flat_to_canonical(flat)
    schema_result = validate_job_spec(canonical)
    constraint_result = constraints.validate(canonical)
    validation = _validation_report(schema_result, constraint_result)
    if not schema_result.accepted or not constraint_result.accepted:
        return {
            "status": "rejected",
            "lane": "manual",
            "provider": "internal_catalog",
            "catalog_revision": CATALOG_REVISION,
            "validation": validation,
            "flat_parameters": None,
            "canonical_spec": None,
        }

    typed_inputs = _typed_stone_json(stone, certificate)
    typed_inputs["metal"] = metal.to_json()
    side_stones = [schedule.to_json() for schedule in head.side_stone_schedules]
    accent_stones = (
        [band.pave.accent_stone_schedule.to_json()]
        if band.pave.accent_stone_schedule
        else []
    )
    preview = {
        "architecture": head.architecture,
        "stone_shape": stone.shape,
        "stone_dimensions_mm": typed_inputs["stone_dimensions_mm"],
        "carat": stone.carat_eq,
        "ring_size_us": stone.ring_size_us,
        "center_setting": head.center_setting,
        "prong_count": head.prong_count,
        "head_family": head.head_family,
        "head_geometry": head.head_geometry,
        "gallery_structure": head.gallery_structure,
        "side_setting": head.side_setting,
        "side_stones": side_stones,
        "band_width_mm": band.band_width_mm,
        "band_thickness_mm": band.band_thickness_mm,
        "band_profile": band.band_profile,
        "shoulder_style": band.shoulder_style,
        "pave": band.pave.canonical_json(),
        "accent_stones": accent_stones,
        "metal": preview_metal(metal),
        "metal_family": metal.color_family,
        "metal_purity": metal.purity,
        "metal_finish": metal.finish,
        "head_style_id": head.style_id,
        "band_style_id": band.style_id,
    }
    if head.reference_template_id:
        preview["reference_geometry_id"] = head.reference_template_id
        preview["visual_reference"] = head.to_json()["visual_reference"]
    reference_id = _reference_id(head, band, stone.shape)
    selection = {
        "head_style_id": head.style_id,
        "band_style_id": band.style_id,
    }
    if reference_id:
        selection["reference_configuration_id"] = reference_id
    modular_contract = _modular_contract(canonical)
    report = attach_preview_manifest({
        "status": "accepted",
        "lane": "manual",
        "provider": "internal_catalog",
        "catalog_revision": CATALOG_REVISION,
        "geometry_identity": geometry_identity,
        "selection": selection,
        "typed_inputs": typed_inputs,
        "preview": preview,
        "modular_contract": modular_contract,
        "quote": quote_basis(metal),
        "rationale": "Explicit customer selection from the compatible internal modular catalog.",
        "flat_parameters": flat,
        "canonical_spec": canonical,
        "validation": validation,
        "rejected_candidates": [],
    })
    # The manifest decides whether a receipt-backed volume exists for this exact
    # configuration, so the real quote can only be built once it has resolved.
    attach_quote(report, metal)
    report["fulfillment"] = _fulfillment(report, stone, head, band)
    return report


def _fulfillment(
    report: dict[str, Any],
    stone: StoneInputs,
    head: HeadStyle,
    band: BandStyle,
) -> dict[str, Any]:
    """Describe how this accepted design could actually be produced.

    Goal 0016. The preview manifest is authority for *whether a preview exists*;
    this block says what happens next when one does not. For a parametric master
    that means naming the master, the typed request it would receive, and the
    parameters it derives — enough that a queued request is a fulfillable work
    order rather than a lost intent.

    Deriving the parameters here also means the customer cannot accept a design
    the master would refuse: the derivation already ran during compatibility, and
    it runs again against the real band section.
    """
    manifest = report.get("preview_manifest") or {}
    preview_status = manifest.get("preview_status", "unavailable")
    fulfillment: dict[str, Any] = {
        "preview_status": preview_status,
        "preview_reason_code": manifest.get("reason_code"),
        "substitution_policy": (
            "MyJewl never shows a neighbouring solved configuration in place of the "
            "one you typed."
        ),
        "manufacturing_authority": False,
        "manufacturing_status": "pending_not_run_needs_calibration",
    }
    if head.parametric_master is None:
        fulfillment["availability"] = (
            PREVIEW_AVAILABLE if preview_status == "exact" else UNPROVEN
        )
        fulfillment["geometry_request"] = None
        fulfillment["detail"] = (
            "This configuration has its own receipt-backed preview asset."
            if preview_status == "exact"
            else (
                "No receipt-backed Rhino master covers this style yet, so an operator "
                "has no derived work order to fulfill from this selection alone."
            )
        )
        return fulfillment

    master = head.parametric_master
    fulfillment["availability"] = (
        PREVIEW_AVAILABLE if preview_status == "exact" else MADE_TO_ORDER
    )
    fulfillment["geometry_request"] = {
        "master": master.to_json(),
        "typed_request": master.typed_request(
            stone, band.band_width_mm, band.band_thickness_mm
        ),
        "derived_parameters": master.derived_parameters(
            stone, band.band_width_mm, band.band_thickness_mm
        ),
        "solve_status": "solved" if preview_status == "exact" else "not_solved",
        "operator_path": list(_OPERATOR_FULFILLMENT_STEPS),
    }
    fulfillment["detail"] = (
        "This configuration already has receipt-backed geometry and previews exactly."
        if preview_status == "exact"
        else (
            "The V5 master derives every value of this configuration, but it has not "
            "been solved yet. Requesting geometry queues the derived work order for an "
            "operator; the app never starts paid compute."
        )
    )
    return fulfillment


#: The chain an operator runs to turn a queued made-to-order request into an
#: exact preview. Recorded in the request itself so the next session does not
#: have to reconstruct it, and pinned by a test against the real script names.
_OPERATOR_FULFILLMENT_STEPS = (
    "python3 -m scripts.operator_fulfill_geometry <config-dir> --operator-approved",
    "python3 -m scripts.geometry_differential <bundle-1> <bundle-2> <bundle-3>",
    "python3 -m scripts.build_goal0014_browser_assets <received-root> "
    "--output-dir fast_mvp/static/assets/goal0014_v5",
    "python3 -m scripts.goal0015_promote_v5 --write",
    "python3 -m scripts.promotion_gate "
    "manual/rhino_compute_bridge/goal0014_parametric_master_v5.ghx "
    "--registry fast_mvp/preview_assets/registry_v0_1.json --asset-id <asset-id>",
)


def _typed_stone_json(
    stone: StoneInputs,
    certificate: StoneCertificate | None,
) -> dict[str, Any]:
    return {
        "stone_shape": stone.shape,
        "stone_dimensions_mm": {
            "length": stone.length_mm,
            "width": stone.width_mm,
            "depth": stone.depth_mm,
        },
        "stone_carat_eq": stone.carat_eq,
        "ring_size_us": stone.ring_size_us,
        "stone_spec_source": stone_spec_source(certificate),
        "certificate": certificate.to_json() if certificate else None,
    }


def _geometry_identity(stone: StoneInputs, head: HeadStyle, band: BandStyle) -> str:
    identity = {
        "catalog_revision": CATALOG_REVISION,
        "stone_shape": stone.shape,
        "stone_dimensions_mm": [stone.length_mm, stone.width_mm, stone.depth_mm],
        "ring_size_us": stone.ring_size_us,
        "head": head.to_json(),
        "band": band.to_json(),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"geo_v2_{digest}"


def _component_metal(metal: MetalSpec) -> dict[str, Any]:
    return {
        "type": metal.metal_type,
        "alloy": metal.alloy.replace("_", " "),
        "purity": metal.purity,
        "finish": metal.finish,
    }


def _components(metal: MetalSpec, head: HeadStyle, band: BandStyle) -> list[dict[str, Any]]:
    roles = ["band", "head"]
    if head.prong_count or head.side_setting == "prong":
        roles.append("prongs")
    if band.pave.coverage != "none":
        roles.append("pave")
    return [
        {
            "component_id": f"component_{role}",
            "role": role,
            "metal": _component_metal(metal),
        }
        for role in roles
    ]


def _assemble_flat(
    stone: StoneInputs,
    metal: MetalSpec,
    head: HeadStyle,
    band: BandStyle,
    *,
    config_id: str,
    geometry_identity: str,
) -> dict[str, Any]:
    stone_schedules = [
        schedule.to_json() for schedule in head.side_stone_schedules
    ]
    if band.pave.accent_stone_schedule:
        stone_schedules.append(band.pave.accent_stone_schedule.to_json())
    flat: dict[str, Any] = {
        "config_id": config_id,
        "geometry_identity": geometry_identity,
        "catalog_revision": CATALOG_REVISION,
        "head_style_id": head.style_id,
        "band_style_id": band.style_id,
        "archetype": head.architecture,
        "stone_shape": stone.shape,
        "stone_length_mm": stone.length_mm,
        "stone_width_mm": stone.width_mm,
        "stone_depth_mm": stone.depth_mm,
        "stone_color": "diamond_colorless",
        "center_setting": head.center_setting,
        "prong_count": head.prong_count,
        "head_family": head.head_family,
        "head_geometry": head.head_geometry,
        "gallery_structure": head.gallery_structure,
        "side_stone_layout": (
            "left_right_pair" if head.architecture == "trilogy" else "none"
        ),
        "side_setting": head.side_setting,
        "stone_schedules": stone_schedules,
        "metal_type": metal.metal_type,
        "metal_alloy": metal.alloy,
        "metal_purity": metal.purity,
        "metal_finish": metal.finish,
        "components": _components(metal, head, band),
        "band_width_mm": band.band_width_mm,
        "band_thickness_mm": band.band_thickness_mm,
        "band_profile": band.legacy_profile,
        "band_profile_code": band.band_profile,
        "shoulder_style": band.shoulder_style,
        "pave": band.pave.canonical_json(),
        "ring_size_us": stone.ring_size_us,
        "geometry_gates": dict(GEOMETRY_GATES),
    }
    if stone.carat_eq is not None:
        flat["stone_carat_eq"] = round(stone.carat_eq, 2)
    return flat


def _reference_id(head: HeadStyle, band: BandStyle, center_shape: str) -> str | None:
    for reference in REFERENCE_CONFIGURATIONS:
        if (
            reference["head_style_id"] == head.style_id
            and reference["band_style_id"] == band.style_id
            and reference["center_shape"] == center_shape
        ):
            return str(reference["reference_configuration_id"])
    return None


def _modular_contract(canonical: dict[str, Any]) -> dict[str, Any]:
    ring = canonical["ring"]
    setting = canonical["setting"]
    return {
        "architecture": canonical["job"]["archetype"],
        "head_family": setting.get("head_family"),
        "head_geometry": setting.get("head_geometry"),
        "gallery_structure": setting.get("gallery_structure"),
        "side_setting": setting.get("side_setting"),
        "band_profile": ring.get("band_profile"),
        "shoulder_style": ring.get("shoulder_style"),
        "pave": ring.get("pave"),
        "stone_schedules": canonical["stones"],
        "components": canonical.get("components", []),
        "geometry_authority": "canonical_spec_v0_1_then_rhino_grasshopper",
        "manufacturing_status": "non_certifying",
    }


def _validation_report(
    schema_result: Any,
    constraint_result: constraints.ConstraintResult,
) -> dict[str, Any]:
    return {
        "schema_accepted": schema_result.accepted,
        "schema_issues": [
            {"code": issue.code, "path": issue.path, "message": issue.message}
            for issue in schema_result.issues
            if issue.severity == "error"
        ],
        "constraint_accepted": constraint_result.accepted,
        "hard_violations": [
            {"rule_id": issue.rule_id, "message": issue.message}
            for issue in constraint_result.hard_violations
        ],
        "warnings": [
            {"rule_id": issue.rule_id, "tier": issue.tier, "message": issue.message}
            for issue in constraint_result.soft_warnings
            + constraint_result.advisory_notes
        ][:12],
        "schema_gaps": constraint_result.schema_gaps[:8],
    }


__all__ = [
    "BAND_STYLES",
    "CATALOG_REVISION",
    "HEAD_STYLES",
    "MADE_TO_ORDER",
    "PREVIEW_AVAILABLE",
    "UNPROVEN",
    "ManualConfiguratorError",
    "ParametricMaster",
    "REFERENCE_CONFIGURATIONS",
    "SUPPORTED_SHAPES",
    "SolvedConfiguration",
    "compatible_options",
    "configure",
    "preview_availability",
    "solved_configurations",
]
