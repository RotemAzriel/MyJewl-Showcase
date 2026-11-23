#!/usr/bin/env python3
"""Versioned, cited, per-shop manufacturing profiles.

Goal 0013 Part B. ``jewelry_gaas_foundation.constraints`` is full of deferred
calibration -- *"a shop-specific structural minimum is required"*, *"obtain
calibrated shop approval"* -- and ``stl_gates`` returns ``needs_calibration``.
Those placeholders were waiting on numbers a shop has and MyJewl does not. This
module is the slot those numbers land in, shaped to match
``docs/casting_shop_survey_2026-08-04.md`` so survey answers transcribe directly.

**The non-negotiable safety property.** A loaded profile supplies *thresholds*, so
a check can run with real numbers instead of a placeholder. It never, by itself,
turns anything into a manufacturing verdict. Per the brand-protecting rule,
pass/fail requires a calibrated profile **and** human jeweler review. Nothing in
this module returns, produces, or influences a gate status.

**A number is usable only if all of these hold**, mirroring the survey's own rule
that a hallway opinion is not a spec:

1. the profile's ``status`` is ``calibrated``;
2. the profile has not expired;
3. the individual value's ``status`` is ``calibrated``;
4. ``committed_in_writing`` is true;
5. the value carries an ``unsupported_span_mm`` qualifier -- a minimum without one
   is not actionable (survey Section 3);
6. its ``alloy_scope`` covers the alloy being validated.

Anything failing those keeps MyJewl's existing uncalibrated baseline, and the
corresponding gate stays ``needs_calibration``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHOP_PROFILE_SCHEMA_PATH = ROOT / "schemas" / "shop_profile_v0_1.schema.json"
UNCALIBRATED_TEMPLATE_PATH = (
    ROOT / "profiles" / "shop_profile_uncalibrated_template_v0_1.json"
)
PROFILE_SCHEMA = "myjewl.shop_profile.v0_1"

#: Thresholds a profile may supply. Each maps to the constraint rules it feeds.
THRESHOLD_RULES: dict[str, tuple[str, ...]] = {
    "min_shank_width_mm": ("BAND-001",),
    "min_shank_thickness_mm": ("BAND-002",),
    "min_knife_edge_band_width_mm": ("BAND-003",),
    "min_comfort_fit_thickness_mm": ("BAND-004",),
    "min_prong_diameter_tip_mm": ("METAL-001", "METAL-002"),
    "min_prong_diameter_base_mm": ("METAL-001", "METAL-002"),
    "min_gallery_wall_thickness_mm": (),
    "min_open_gallery_wire_diameter_mm": (),
    "min_gap_between_features_mm": (),
    "min_castable_wall_thickness_mm": (),
    "min_feature_volume_mm3": (),
}


#: Mesh fields that must all be stated before a shop's export policy counts as
#: declared (Goal 0025). ``preferred_format`` and ``units_assumed`` count as
#: undeclared while they read ``unknown``; the four booleans while they are null.
#: Everything else in the block refines a declared policy rather than forming it:
#: ``max_triangle_count`` is an optional budget, ``max_chord_deviation_mm`` is not
#: recoverable from a mesh so it warns rather than gates, and
#: ``inverted_normals_are_silently_repaired`` never grants permission -- see
#: ``docs/geometry_delivery_2026-08-17.md`` §3.
REQUIRED_MESH_FIELDS: tuple[str, ...] = (
    "preferred_format",
    "units_assumed",
    "single_solid_required",
    "intersecting_solids_accepted",
    "outward_normals_required",
    "watertight_required",
)


class ShopProfileError(ValueError):
    """The profile is structurally or semantically unusable."""


@dataclass(frozen=True)
class MeshPolicy:
    """What a shop has said it can read, or why it has said nothing.

    ``declared`` is the only thing callers should branch on. When it is false every
    threshold field is ``None`` and no geometry may be handed over -- not because
    the geometry is bad, but because nobody has stated what this shop accepts.
    """

    declared: bool
    reason: str
    detail: str
    missing_fields: tuple[str, ...] = ()
    preferred_format: str | None = None
    units_assumed: str | None = None
    single_solid_required: bool | None = None
    intersecting_solids_accepted: bool | None = None
    outward_normals_required: bool | None = None
    inverted_normals_are_silently_repaired: bool | None = None
    watertight_required: bool | None = None
    max_triangle_count: int | None = None
    max_chord_deviation_mm: float | None = None
    profile_id: str | None = None
    profile_version: str | None = None
    expires_on: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "reason": self.reason,
            "detail": self.detail,
            "missing_fields": list(self.missing_fields),
            "preferred_format": self.preferred_format,
            "units_assumed": self.units_assumed,
            "single_solid_required": self.single_solid_required,
            "intersecting_solids_accepted": self.intersecting_solids_accepted,
            "outward_normals_required": self.outward_normals_required,
            "inverted_normals_are_silently_repaired": (
                self.inverted_normals_are_silently_repaired
            ),
            "watertight_required": self.watertight_required,
            "max_triangle_count": self.max_triangle_count,
            "max_chord_deviation_mm": self.max_chord_deviation_mm,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "expires_on": self.expires_on,
            "certifies_manufacturing": False,
        }


class Threshold:
    """A resolved threshold plus why it is or is not usable.

    ``usable`` is the only thing callers should branch on. When it is false,
    ``value`` is ``None`` and the caller must keep its uncalibrated baseline.
    """

    __slots__ = ("key", "value", "units", "usable", "reason", "source", "profile_id")

    def __init__(
        self,
        key: str,
        value: float | None,
        units: str | None,
        usable: bool,
        reason: str,
        source: str | None = None,
        profile_id: str | None = None,
    ) -> None:
        self.key = key
        self.value = value
        self.units = units
        self.usable = usable
        self.reason = reason
        self.source = source
        self.profile_id = profile_id

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"Threshold(key={self.key!r}, value={self.value!r}, "
            f"usable={self.usable!r}, reason={self.reason!r})"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "units": self.units,
            "usable": self.usable,
            "reason": self.reason,
            "source": self.source,
            "profile_id": self.profile_id,
            "certifies_manufacturing": False,
        }


def _validate_with_schema(document: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - jsonschema is a project dep
        raise ShopProfileError("jsonschema is required to load shop profiles") from exc
    schema = json.loads(SHOP_PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document), key=lambda e: e.path
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.path) or "<root>"
        raise ShopProfileError(f"shop profile is invalid at {location}: {first.message}")


class ShopProfile:
    """A loaded, schema-valid shop profile.

    Construction never certifies anything. ``threshold()`` is the only way values
    leave this object, and it refuses anything that is not fully attributable.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        if not isinstance(document, dict):
            raise ShopProfileError("shop profile must be an object")
        _validate_with_schema(document)
        if document["schema"] != PROFILE_SCHEMA:
            raise ShopProfileError("unsupported shop profile schema")
        if document.get("certifies_manufacturing") is not False:
            raise ShopProfileError("a shop profile can never certify manufacturing")
        self._document = json.loads(json.dumps(document))  # defensive deep copy

    # -- identity -----------------------------------------------------------
    @property
    def profile_id(self) -> str:
        return str(self._document["profile_id"])

    @property
    def profile_version(self) -> str:
        return str(self._document["profile_version"])

    @property
    def status(self) -> str:
        return str(self._document["status"])

    @property
    def label(self) -> str:
        """Short human label used in constraint messages."""
        return f"{self.profile_id}@{self.profile_version}"

    @property
    def measurement_basis(self) -> str:
        return str(self._document["measurement_basis"])

    @property
    def document(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._document))

    def expired_on(self, today: date | None = None) -> bool:
        today = today or date.today()
        expires = date.fromisoformat(str(self._document["validity"]["expires_on"]))
        return today > expires

    # -- threshold resolution ----------------------------------------------
    def _select(self, entries: list[dict[str, Any]], alloy: str | None) -> dict | None:
        """Return the most specific entry whose alloy scope covers ``alloy``."""
        wanted = (alloy or "").strip().lower()
        specific = None
        wildcard = None
        for entry in entries:
            scope = [str(item).lower() for item in entry.get("alloy_scope", [])]
            if wanted and wanted in scope:
                specific = specific or entry
            elif "*" in scope:
                wildcard = wildcard or entry
        return specific or wildcard

    def threshold(
        self,
        key: str,
        *,
        alloy: str | None = None,
        today: date | None = None,
    ) -> Threshold:
        """Resolve one threshold, refusing anything not fully attributable."""
        if key not in THRESHOLD_RULES:
            raise ShopProfileError(f"unknown threshold key: {key}")

        def refuse(reason: str) -> Threshold:
            return Threshold(key, None, None, False, reason, profile_id=self.profile_id)

        if self.status != "calibrated":
            return refuse("profile_uncalibrated")
        if self.expired_on(today):
            return refuse("profile_expired")

        entries = self._document.get("minimums", {}).get(key) or []
        entry = self._select(entries, alloy)
        if entry is None:
            return refuse("no_value_for_alloy" if alloy else "no_value_recorded")
        if entry["status"] != "calibrated":
            return refuse("value_uncalibrated")
        if not entry["committed_in_writing"]:
            return refuse("not_committed_in_writing")
        if entry.get("unsupported_span_mm") in (None, 0):
            return refuse("no_unsupported_span_qualifier")

        return Threshold(
            key=key,
            value=float(entry["value"]),
            units=str(entry["units"]),
            usable=True,
            reason="calibrated",
            source=str(entry["provenance"]["source"]),
            profile_id=self.profile_id,
        )

    # -- mesh export policy -------------------------------------------------
    def mesh_policy(self, *, today: date | None = None) -> MeshPolicy:
        """Resolve what this shop accepts as an exported mesh, or refuse.

        Goal 0025. This governs what we may *hand over*, not whether the ring is
        castable -- the survey itself files it under "what we must export". It is
        still a shop-calibration question, and it is refused on exactly the same
        terms as a threshold: an uncalibrated or expired profile states nothing, and
        a half-filled block is not a policy.
        """
        mesh = self._document.get("mesh") or {}

        def refuse(reason: str, detail: str, missing: tuple[str, ...] = ()) -> MeshPolicy:
            return MeshPolicy(
                declared=False,
                reason=reason,
                detail=detail,
                missing_fields=missing,
                profile_id=self.profile_id,
                profile_version=self.profile_version,
                expires_on=str(self._document["validity"]["expires_on"]),
            )

        if self.status != "calibrated":
            return refuse(
                "profile_uncalibrated",
                f"Shop profile {self.label} is not calibrated, so it states no mesh "
                "policy. No export policy was assumed on a shop's behalf.",
                REQUIRED_MESH_FIELDS,
            )
        if self.expired_on(today):
            return refuse(
                "profile_expired",
                f"Shop profile {self.label} expired on "
                f"{self._document['validity']['expires_on']}. A lapsed policy is not "
                "reused; re-confirm it with the shop.",
                REQUIRED_MESH_FIELDS,
            )

        missing = tuple(
            field
            for field in REQUIRED_MESH_FIELDS
            if mesh.get(field) is None or mesh.get(field) == "unknown"
        )
        if missing:
            return refuse(
                "mesh_fields_undeclared",
                f"Shop profile {self.label} leaves {len(missing)} mesh field(s) "
                f"unstated ({', '.join(missing)}), so it has not said what it accepts.",
                missing,
            )

        return MeshPolicy(
            declared=True,
            reason="declared",
            detail=(
                f"Shop profile {self.label} declares its mesh policy: "
                f"{mesh['preferred_format']} in {mesh['units_assumed']}, "
                f"single solid required={mesh['single_solid_required']}, "
                f"intersecting solids accepted={mesh['intersecting_solids_accepted']}, "
                f"outward normals required={mesh['outward_normals_required']}."
            ),
            preferred_format=str(mesh["preferred_format"]),
            units_assumed=str(mesh["units_assumed"]),
            single_solid_required=bool(mesh["single_solid_required"]),
            intersecting_solids_accepted=bool(mesh["intersecting_solids_accepted"]),
            outward_normals_required=bool(mesh["outward_normals_required"]),
            inverted_normals_are_silently_repaired=(
                None
                if mesh.get("inverted_normals_are_silently_repaired") is None
                else bool(mesh["inverted_normals_are_silently_repaired"])
            ),
            watertight_required=bool(mesh["watertight_required"]),
            max_triangle_count=(
                None
                if mesh.get("max_triangle_count") is None
                else int(mesh["max_triangle_count"])
            ),
            max_chord_deviation_mm=(
                None
                if mesh.get("max_chord_deviation_mm") is None
                else float(mesh["max_chord_deviation_mm"])
            ),
            profile_id=self.profile_id,
            profile_version=self.profile_version,
            expires_on=str(self._document["validity"]["expires_on"]),
        )

    def usable_thresholds(self, alloy: str | None = None) -> dict[str, float]:
        """Return only the thresholds that survive every attribution check."""
        resolved = {}
        for key in THRESHOLD_RULES:
            item = self.threshold(key, alloy=alloy)
            if item.usable and item.value is not None:
                resolved[key] = item.value
        return resolved


def load_profile(path: Path | str) -> ShopProfile:
    """Load and validate a shop profile from disk."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShopProfileError(f"cannot read shop profile: {path}") from exc
    return ShopProfile(document)


# ---------------------------------------------------------------------------
# Active-profile registry.
#
# constraints.validate(spec) keeps its signature -- the Goal 0003 lesson that let
# the app consume constraint re-tiering with zero changes. The active profile is
# therefore set out of band rather than threaded through every call site.
# ---------------------------------------------------------------------------

_ACTIVE_PROFILE: ShopProfile | None = None


def set_active_profile(profile: ShopProfile | None) -> None:
    """Install (or clear) the profile constraint rules read thresholds from."""
    global _ACTIVE_PROFILE
    if profile is not None and not isinstance(profile, ShopProfile):
        raise ShopProfileError("active profile must be a ShopProfile or None")
    _ACTIVE_PROFILE = profile


def active_profile() -> ShopProfile | None:
    """Return the installed profile, or ``None`` when nothing is calibrated."""
    return _ACTIVE_PROFILE


def clear_active_profile() -> None:
    set_active_profile(None)


def active_threshold(key: str, *, alloy: str | None = None) -> Threshold:
    """Resolve a threshold against the active profile, or refuse when there is none.

    With no active profile this always refuses, so callers fall back to their
    existing uncalibrated baseline and behavior is unchanged.
    """
    profile = active_profile()
    if profile is None:
        return Threshold(key, None, None, False, "no_active_profile")
    return profile.threshold(key, alloy=alloy)


def active_mesh_policy(*, today: date | None = None) -> MeshPolicy:
    """Resolve the mesh export policy of the active profile, or refuse.

    With no active profile this always refuses, so nothing can be handed to a shop
    that has never said what it accepts.
    """
    profile = active_profile()
    if profile is None:
        return MeshPolicy(
            declared=False,
            reason="no_active_profile",
            detail=(
                "No shop profile is loaded, so no shop has declared what it accepts "
                "as an exported mesh. No export policy was assumed."
            ),
            missing_fields=REQUIRED_MESH_FIELDS,
        )
    return profile.mesh_policy(today=today)


def uncalibrated_template() -> ShopProfile:
    """Load the shipped template. It is explicitly not a real shop."""
    return load_profile(UNCALIBRATED_TEMPLATE_PATH)
