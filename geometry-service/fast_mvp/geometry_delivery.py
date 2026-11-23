"""Hand over the geometry MyJewl can prove, or refuse and say exactly why.

Goal 0025. MyJewl is Geometry-as-a-Service and, until this module, there was no
path by which anyone received the geometry it can prove: ``/api/generate-stl``
returned a procedural draft from ``fast_mvp.mesh``, the promoted assets shipped
only as Three.js browser meshes, and the receipt-backed Rhino STLs sat in a
gitignored directory served to nobody.

**Only one kind of byte may leave here: an exact Rhino export that a receipt
vouches for, verified again at request time.** Rhino/Grasshopper is the sole
geometry authority and the model never hand-authors geometry, so this repository's
own manifold3d union -- watertight, single-bodied, and the basis of every quote --
is measured but never delivered. Measuring a derived mesh answers *how much metal
is in this ring*; delivering one asserts *this is the thing to cast*, which is a
materially stronger claim and not Rhino's to make by proxy. The full argument, and
why "deliver nothing until V6 is promoted" was also refused, is in
``docs/geometry_delivery_2026-08-17.md``.

**A delivery is allowed only if all of these hold**, and each failure is named:

1. the preview resolved ``exact`` -- delivering a neighbouring size's STL is the
   substitution failure Goal 0014 closed, one step worse than quoting one;
2. the geometry signature is in the delivery table verbatim, with the selected
   asset's id **and** version matching;
3. a measured volume exists, and a metal-weight table with it -- a file arriving at
   a casting house without its weight gets guessed at;
4. a **calibrated, unexpired** shop profile declares its whole ``mesh`` block;
5. what we have satisfies what that shop said it accepts;
6. the export bundle is present on this machine;
7. every delivered file hash-matches the receipt, byte for byte, right now.

Checks 4 and 5 run before 6 and 7 on purpose: whether we *may* deliver is a product
decision that must read the same on every machine, while whether we *can* depends on
which laptop the server happens to be running on.

Nothing here certifies manufacturability. A shop profile that authorises delivery
says what that shop can read, not that the ring is castable: gates stay pending /
not_run / needs_calibration, and F3 -- every wire section in the envelope possibly
being too thin -- is untouched by handing over the file.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fast_mvp import quote_volumes
from jewelry_gaas_foundation.shop_profile import MeshPolicy, active_mesh_policy

ROOT = Path(__file__).resolve().parents[1]
TABLE_SCHEMA_PATH = (
    ROOT / "schemas" / "deliverable_geometry_table_v0_1.schema.json"
)
DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parent / "preview_assets" / "deliverable_geometry_v0_1.json"
)

TABLE_SCHEMA = "myjewl.deliverable_geometry_table.v0_1"

#: The one declared source of delivered bytes, pinned by schema ``const`` for the
#: same reason ``quote_volumes`` pins its basis: so the answer cannot be changed by
#: editing data.
DELIVERY_SOURCE = "receipt_backed_rhino_export_bytes"

#: Preview statuses a delivery may be built on. ``estimated`` is excluded: an
#: approximate *picture* is a product decision, an approximate *ring* is a return.
_DELIVERABLE_PREVIEW_STATUS = frozenset({"exact"})

#: The format we can vouch for. Only the STLs are hashed in the receipt.
DELIVERED_FORMAT = "stl"

#: Point this at a calibrated shop profile to let deliveries be authorised. Unset,
#: no profile is loaded and every delivery refuses ``mesh_policy_undeclared``,
#: which is the state the repository ships in.
SHOP_PROFILE_ENV = "MYJEWL_SHOP_PROFILE"

#: Where each receipt role lands inside the archive. ``fused`` is empty for every
#: promoted asset today -- that emptiness is finding F2.
_ARCHIVE_DIRS = {"metal": "geometry", "castable": "fused", "stone": "reference"}

#: Fixed archive timestamp. A delivery is a function of its inputs, so the same
#: configuration under the same policy must produce the same bytes and the same
#: SHA-256 -- a recipient can quote the hash back and we can reproduce it.
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

DELIVERY_SCOPE_NOTE = (
    "Receipt-verified Rhino export geometry and its measured metal weight. This is "
    "not a manufacturing certification, not an approval to cast, and not a "
    "statement that the sections are thick enough to survive casting."
)


class DeliveryError(ValueError):
    """The delivery table is structurally unsafe to deliver from."""


@dataclass(frozen=True)
class DeliverableAsset:
    """One promoted asset's export bundle, as facts about deliverable bytes."""

    asset_id: str
    asset_version: str
    geometry_signature: str
    source_receipt_sha256: str
    receipt_file: str
    definition_path: str
    definition_sha256: str
    definition_kind: str
    template_id: str
    bundle: str
    units: str
    coordinate_system: str
    metal_part_count: int
    reference_part_count: int
    inverted_part_count: int
    inverted_parts: tuple[str, ...]
    not_watertight_part_count: int
    ships_castable_solid: bool
    castable_solid_file: str | None
    triangle_count: int
    vertex_count: int
    files: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]
    metrics_version: str
    measured_on: str

    @property
    def bundle_path(self) -> Path:
        """The export directory, resolved under the repository root.

        The table is a committed artefact, but a path that escapes the repository
        would turn a data edit into arbitrary file disclosure, so it is checked
        rather than trusted.
        """
        resolved = (ROOT / self.bundle).resolve()
        if resolved != ROOT and ROOT not in resolved.parents:
            raise DeliveryError(f"{self.asset_id}: bundle escapes the repository root")
        return resolved

    def identity(self) -> dict[str, Any]:
        """Who made these bytes, from what, and how they can be re-verified."""
        return {
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "geometry_signature": self.geometry_signature,
            "template_id": self.template_id,
            "definition_path": self.definition_path,
            "definition_sha256": self.definition_sha256,
            "definition_kind": self.definition_kind,
            "source_receipt_sha256": self.source_receipt_sha256,
            "receipt_file": self.receipt_file,
            "bundle": self.bundle,
            "units": self.units,
            "coordinate_system": self.coordinate_system,
            "delivery_source": DELIVERY_SOURCE,
            "format": DELIVERED_FORMAT,
            "metrics_version": self.metrics_version,
            "measured_on": self.measured_on,
        }

    def geometry_state(self) -> dict[str, Any]:
        """What this geometry *is*, including the parts of it that are wrong."""
        return {
            "metal_part_count": self.metal_part_count,
            "reference_part_count": self.reference_part_count,
            "inverted_part_count": self.inverted_part_count,
            "inverted_parts": list(self.inverted_parts),
            "not_watertight_part_count": self.not_watertight_part_count,
            "ships_castable_solid": self.ships_castable_solid,
            "castable_solid_file": self.castable_solid_file,
            "triangle_count": self.triangle_count,
            "vertex_count": self.vertex_count,
            "findings": [dict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class DeliveryDecision:
    """Whether geometry may be handed over for one resolved configuration."""

    deliverable: bool
    reason: str
    detail: str
    asset: DeliverableAsset | None = None
    policy: MeshPolicy | None = None
    warnings: tuple[str, ...] = ()
    findings: tuple[dict[str, Any], ...] = ()
    volume: Any | None = None
    bytes_verified: bool = False

    def to_json(self) -> dict[str, Any]:
        """The API shape. Identical whether the answer is yes or no."""
        payload: dict[str, Any] = {
            "deliverable": self.deliverable,
            "reason": self.reason,
            "detail": self.detail,
            "byte_verification": (
                "verified_against_receipt"
                if self.bytes_verified
                else "deferred_to_download"
            ),
            "delivery_source": DELIVERY_SOURCE,
            "format": DELIVERED_FORMAT,
            "scope_note": DELIVERY_SCOPE_NOTE,
            "warnings": list(self.warnings),
            "findings": [dict(item) for item in self.findings],
            "mesh_policy": None if self.policy is None else self.policy.to_json(),
            "asset": None if self.asset is None else self.asset.identity(),
            "geometry_state": (
                None if self.asset is None else self.asset.geometry_state()
            ),
            "authority": "receipt_backed_rhino_export_not_manufacturing",
            "certifies_manufacturing": False,
            "manufacturing_gates": "pending_not_run_needs_calibration",
        }
        if self.deliverable and self.asset is not None:
            payload["files"] = [dict(item) for item in self.asset.files]
            payload["file_count"] = len(self.asset.files)
            payload["byte_size"] = sum(
                int(item["byte_size"]) for item in self.asset.files
            )
        return payload


@lru_cache(maxsize=4)
def _schema(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_table(table: Any) -> dict[str, Any]:
    """Validate the table's shape and the invariants a schema cannot express."""
    if not isinstance(table, dict):
        raise DeliveryError("deliverable geometry table must be an object")
    errors = sorted(
        Draft202012Validator(_schema(str(TABLE_SCHEMA_PATH))).iter_errors(table),
        key=lambda err: list(err.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise DeliveryError(
            f"deliverable geometry table invalid at {location}: {first.message}"
        )
    if table["delivery_source"] != DELIVERY_SOURCE:
        raise DeliveryError(
            f"unsupported delivery source: {table['delivery_source']!r}"
        )

    seen_signatures: set[str] = set()
    seen_assets: set[tuple[str, str]] = set()
    for record in table["records"]:
        signature = record["geometry_signature"]
        if signature in seen_signatures:
            raise DeliveryError(
                f"duplicate geometry signature in delivery table: {signature}"
            )
        seen_signatures.add(signature)
        identity = (record["asset_id"], record["asset_version"])
        if identity in seen_assets:
            raise DeliveryError(f"duplicate asset in delivery table: {identity[0]}")
        seen_assets.add(identity)

        metal = [item for item in record["files"] if item["role"] == "metal"]
        if len(metal) != record["metal_part_count"]:
            raise DeliveryError(
                f"{record['asset_id']}: metal_part_count disagrees with the file list"
            )
        if record["inverted_part_count"] != len(record["inverted_parts"]):
            raise DeliveryError(
                f"{record['asset_id']}: inverted_part_count disagrees with the names"
            )
        inverted_measured = [
            item for item in metal if not item["oriented_outward"]
        ]
        if len(inverted_measured) != record["inverted_part_count"]:
            # The count is what a refusal quotes; the per-file flags are what a
            # recipient checks. If they disagree the table is not describing the
            # bytes it lists.
            raise DeliveryError(
                f"{record['asset_id']}: inverted count disagrees with the measured "
                "per-file orientation flags"
            )
        if len({item["file"] for item in record["files"]}) != len(record["files"]):
            raise DeliveryError(f"{record['asset_id']}: duplicate file in the record")
        castable = [item for item in record["files"] if item["role"] == "castable"]
        if record["ships_castable_solid"]:
            # A union that is open or inside-out is not a union, and must not let a
            # shop requiring one fused solid believe its requirement was met.
            if len(castable) != 1:
                raise DeliveryError(
                    f"{record['asset_id']}: claims a fused solid but lists "
                    f"{len(castable)} file(s) with that role"
                )
            if castable[0]["file"] != record["castable_solid_file"]:
                raise DeliveryError(
                    f"{record['asset_id']}: castable_solid_file names a file that is "
                    "not the one carrying that role"
                )
            if not (castable[0]["watertight"] and castable[0]["oriented_outward"]):
                raise DeliveryError(
                    f"{record['asset_id']}: the fused solid is not a valid closed, "
                    "outward-oriented body, so it is not a union"
                )
        elif record["castable_solid_file"] is not None:
            raise DeliveryError(
                f"{record['asset_id']}: names a fused solid while reporting none"
            )
    return table


def load_table(path: Path | str = DEFAULT_TABLE_PATH) -> dict[str, Any]:
    """Read and validate the table. A missing table delivers nothing, never raises."""
    table_path = Path(path)
    if not table_path.is_file():
        return {
            "schema": TABLE_SCHEMA,
            "delivery_source": DELIVERY_SOURCE,
            "records": [],
            "missing": True,
        }
    return validate_table(json.loads(table_path.read_text(encoding="utf-8")))


def _stamp(path: Path | str) -> tuple[int, int] | None:
    """Identity of the file on disk, so regenerating the table invalidates it.

    A long-running server that cached records by path alone would keep serving a
    retired asset after ``goal0025_measure_deliverables --write`` removed it -- and
    the emptiness check would already be reading the new file, so the two would
    disagree. Keying on (mtime, size) makes a regeneration take effect and keeps
    schema validation off the per-request path.
    """
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _loaded(path: Path | str) -> tuple[dict[str, Any], dict[str, DeliverableAsset]]:
    """The validated table and its records, cached against the file on disk."""
    return _load_cached(str(path), _stamp(path))


@lru_cache(maxsize=8)
def _load_cached(
    path: str, stamp: tuple[int, int] | None
) -> tuple[dict[str, Any], dict[str, DeliverableAsset]]:
    table = load_table(path)
    out: dict[str, DeliverableAsset] = {}
    for record in table.get("records", []):
        out[record["geometry_signature"]] = DeliverableAsset(
            asset_id=record["asset_id"],
            asset_version=record["asset_version"],
            geometry_signature=record["geometry_signature"],
            source_receipt_sha256=record["source_receipt_sha256"],
            receipt_file=record["receipt_file"],
            definition_path=record["definition_path"],
            definition_sha256=record["definition_sha256"],
            definition_kind=record["definition_kind"],
            template_id=record["template_id"],
            bundle=record["bundle"],
            units=record["units"],
            coordinate_system=record["coordinate_system"],
            metal_part_count=record["metal_part_count"],
            reference_part_count=record["reference_part_count"],
            inverted_part_count=record["inverted_part_count"],
            inverted_parts=tuple(record["inverted_parts"]),
            not_watertight_part_count=record["not_watertight_part_count"],
            ships_castable_solid=record["ships_castable_solid"],
            castable_solid_file=record["castable_solid_file"],
            triangle_count=record["triangle_count"],
            vertex_count=record["vertex_count"],
            files=tuple(dict(item) for item in record["files"]),
            findings=tuple(dict(item) for item in record["findings"]),
            metrics_version=table["metrics_version"],
            measured_on=table["measured_on"],
        )
    return table, out


def clear_cache() -> None:
    """Forget the loaded table. Tests that write a table call this."""
    _load_cached.cache_clear()


def record_for_manifest(
    manifest: Any, *, table_path: Path | str = DEFAULT_TABLE_PATH
) -> DeliverableAsset | None:
    """The deliverable record for a resolved preview, or ``None``.

    Deliberately the same join ``quote_volumes.volume_for_manifest`` performs: an
    exact preview, a verbatim signature, and a matching asset id and version. There
    is no nearest size, no interpolation, and no default.
    """
    if not isinstance(manifest, dict):
        return None
    if manifest.get("preview_status") not in _DELIVERABLE_PREVIEW_STATUS:
        return None
    geometry = manifest.get("geometry")
    if not isinstance(geometry, dict):
        return None
    record = _loaded(table_path)[1].get(geometry.get("signature"))
    if record is None:
        return None
    selected = manifest.get("selected_asset")
    if not isinstance(selected, dict):
        return None
    if (
        selected.get("asset_id") != record.asset_id
        or selected.get("asset_version") != record.asset_version
    ):
        # The table has drifted from the registry. Delivering the stale record
        # would hand over geometry that is no longer the one being shown.
        return None
    return record


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_expectations(asset: DeliverableAsset) -> tuple[dict[str, str], list[str]]:
    """Read the bundle's own receipt and return ``{file: sha256}`` plus problems.

    **The receipt is the authority, not this table.** Checking a delivered file
    only against the table would make the table itself sufficient to nominate a
    file -- someone could point a record at ``fast_mvp/runs/<job>/draft_preview.stl``
    with its true hash and the procedural draft would ship as manufacturable
    geometry. Anchoring to the receipt, whose own SHA-256 is pinned in the record,
    means a doctored table nominates nothing: it would have to forge a receipt that
    hashes to the recorded value.
    """
    bundle = asset.bundle_path
    receipt_path = bundle / asset.receipt_file
    if not receipt_path.is_file():
        return {}, [f"{asset.receipt_file}: the bundle's receipt is missing"]
    if _sha256_file(receipt_path) != asset.source_receipt_sha256:
        return {}, [
            f"{asset.receipt_file}: the receipt is not the one this asset was "
            "promoted from"
        ]
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, [f"{asset.receipt_file}: the receipt is unreadable"]
    expectations = {
        str(part["file"]): str(part["sha256"])
        for part in receipt.get("parts", [])
        if isinstance(part, dict) and part.get("file") and part.get("sha256")
    }
    required = {
        str(part["file"])
        for part in receipt.get("parts", [])
        if isinstance(part, dict) and str(part.get("role") or "") == "metal"
    }
    # The converse of "every delivered file is vouched for": every metal part the
    # receipt records must actually be delivered. Without this, a stale record that
    # dropped shank.stl *and* decremented metal_part_count passes the schema, passes
    # every hash check, and hands over a ring with no band. A missing part delivered
    # silently is worse than a refusal.
    missing = sorted(required - {str(item["file"]) for item in asset.files})
    if missing:
        return {}, [
            f"{name}: recorded as metal in the receipt but absent from the "
            "delivery table"
            for name in missing
        ]
    return expectations, []


def verify_bytes(asset: DeliverableAsset) -> list[str]:
    """Re-hash every deliverable file against the receipt. Returns problems.

    Run on every request rather than trusted from the table: the bundles live in
    gitignored ``output/``, outside version control, so a table written in August
    is not evidence about the bytes on disk today.
    """
    bundle = asset.bundle_path
    expectations, problems = receipt_expectations(asset)
    if problems:
        return problems
    for item in asset.files:
        name = str(item["file"])
        expected = expectations.get(name)
        if expected is None:
            problems.append(f"{name}: the receipt does not vouch for this file")
            continue
        if expected != str(item["sha256"]):
            problems.append(f"{name}: the table's hash disagrees with the receipt")
            continue
        path = bundle / name
        if not path.is_file():
            problems.append(f"{name}: missing from the export bundle")
            continue
        if path.stat().st_size != int(item["byte_size"]):
            problems.append(f"{name}: byte size differs from the receipt")
            continue
        if _sha256_file(path) != expected:
            problems.append(f"{name}: bytes differ from the receipt hash")
    return problems


def _policy_refusals(
    asset: DeliverableAsset, policy: MeshPolicy
) -> tuple[str, str] | None:
    """Check what we have against what this shop said it accepts."""
    if policy.units_assumed != asset.units:
        return (
            "units_mismatch",
            f"This shop reads meshes as {policy.units_assumed}; the export is in "
            f"{asset.units}. Delivering it would be read at the wrong scale.",
        )
    if policy.preferred_format != DELIVERED_FORMAT:
        return (
            "preferred_format_unavailable",
            f"This shop wants {policy.preferred_format}, and only the {DELIVERED_FORMAT} "
            "files are hashed in the receipt. Converting here would produce geometry no "
            "receipt vouches for; the fix is to hash that format in the next solve.",
        )
    if policy.watertight_required and asset.not_watertight_part_count:
        return (
            "shop_requires_watertight_parts",
            f"This shop requires closed meshes and {asset.not_watertight_part_count} "
            "delivered part(s) are not watertight.",
        )
    if policy.outward_normals_required and asset.inverted_part_count:
        repaired = policy.inverted_normals_are_silently_repaired
        note = (
            " This shop's pipeline repairs inverted normals silently, which explains "
            "why nobody would notice -- it is not a reason to keep not noticing, so "
            "it does not authorise the delivery."
            if repaired
            else ""
        )
        return (
            "shop_requires_outward_normals",
            f"F1: {asset.inverted_part_count} of {asset.metal_part_count} metal parts "
            "are wound inward, and this shop requires outward normals. The fix belongs "
            f"in the Rhino definition, not in a repair step here.{note}",
        )
    if policy.single_solid_required and not asset.ships_castable_solid:
        return (
            "single_solid_required_but_absent",
            "F2: this shop requires one fused solid and the export ships none -- the "
            f"{asset.metal_part_count} metal parts interpenetrate and nothing unions "
            "them. No union is computed here; Rhino is the geometry authority.",
        )
    if not policy.intersecting_solids_accepted and asset.metal_part_count > 1:
        # Note the second case. A bundle that *does* ship a union still ships the
        # named parts beside it, and the delivered set is the whole receipt-hashed
        # list -- so this shop would still receive interpenetrating solids. Sending
        # the union alone is a coherent future behaviour, but no promoted asset
        # emits a union yet, so there is nothing to build it against or test it on.
        # Refusing is the honest answer until there is.
        return (
            "shop_rejects_intersecting_solids",
            "This shop does not accept interpenetrating solids and the delivered set "
            f"is {asset.metal_part_count} overlapping metal parts"
            + (
                ", with no boolean union at all (F2)."
                if not asset.ships_castable_solid
                else f", alongside the fused {asset.castable_solid_file}. Delivering "
                "only the fused solid is not implemented: no promoted asset emits "
                "one, so it cannot be built or tested against real bytes."
            ),
        )
    if (
        policy.max_triangle_count is not None
        and asset.triangle_count > policy.max_triangle_count
    ):
        return (
            "triangle_budget_exceeded",
            f"This shop accepts at most {policy.max_triangle_count} triangles; the "
            f"export carries {asset.triangle_count}.",
        )
    return None


def _policy_warnings(asset: DeliverableAsset, policy: MeshPolicy) -> list[str]:
    """What a shop asked for that we cannot confirm, or accepted knowingly."""
    warnings: list[str] = []
    if policy.max_chord_deviation_mm is not None:
        warnings.append(
            f"This shop specifies a maximum chord deviation of "
            f"{policy.max_chord_deviation_mm} mm. The tolerance a mesh was "
            "tessellated at cannot be recovered from the mesh, so it is reported as "
            "not verified rather than treated as met."
        )
    if asset.inverted_part_count and not policy.outward_normals_required:
        warnings.append(
            f"F1 travels with this delivery: {asset.inverted_part_count} of "
            f"{asset.metal_part_count} metal parts are wound inward. This shop does "
            "not require outward normals, so it is delivered declared, not repaired."
        )
    if not asset.ships_castable_solid:
        warnings.append(
            "F2 travels with this delivery: the metal parts interpenetrate and the "
            "export ships no boolean union, so whoever casts it must fuse them. The "
            "per-part volume sum over-states metal; use the union volume stated here."
        )
    if asset.reference_part_count:
        warnings.append(
            "The centre stone is included for seat checking only and is labelled "
            "reference_only_not_for_casting. It is not metal and is in no weight."
        )
    return warnings


def decide(
    manifest: Any,
    *,
    quote: Any = None,
    table_path: Path | str = DEFAULT_TABLE_PATH,
    volume_table_path: Path | str = quote_volumes.DEFAULT_TABLE_PATH,
    policy: MeshPolicy | None = None,
    verify: bool = True,
) -> DeliveryDecision:
    """Decide whether this resolved configuration's geometry may be handed over.

    Refusal is the safe answer and, in the state this repository ships in, the
    only one. Every refusal names the condition that failed.

    ``verify=False`` skips re-hashing the bundle, for listing surfaces such as
    ``/review`` that ask the question about many runs at once. It is safe only
    because it cannot weaken a real delivery: :func:`build_archive` re-hashes every
    file as it reads it and refuses the whole archive on any mismatch, so unverified
    bytes have no path out of this module.
    """
    if not isinstance(manifest, dict):
        return DeliveryDecision(
            False,
            "no_preview_manifest",
            "This configuration has no server-authoritative PreviewManifest, so "
            "there is no asset whose geometry could be delivered.",
        )
    if manifest.get("preview_status") not in _DELIVERABLE_PREVIEW_STATUS:
        return DeliveryDecision(
            False,
            "preview_not_exact",
            "Geometry is delivered only for a configuration whose preview resolved "
            f"exact; this one resolved {manifest.get('preview_status')!r}. A "
            "neighbouring size's file is not this ring.",
        )

    table, _cached = _loaded(table_path)
    if table.get("missing") or not table.get("records"):
        return DeliveryDecision(
            False,
            "no_delivery_table",
            "No deliverable-geometry table is loaded, so nothing has been measured "
            "as deliverable. Run scripts/goal0025_measure_deliverables.py.",
        )

    asset = record_for_manifest(manifest, table_path=table_path)
    if asset is None:
        return DeliveryDecision(
            False,
            "no_deliverable_record",
            "No receipt-backed export is recorded for this exact configuration and "
            "asset version. Nothing adjacent is substituted.",
        )

    volume = quote_volumes.volume_for_manifest(manifest, table_path=volume_table_path)
    if volume is None:
        return DeliveryDecision(
            False,
            "no_measured_volume",
            "This asset has no measured ring volume, so the geometry cannot travel "
            "with the weight a casting house needs. Regenerate the quote-volume "
            "table before delivering.",
            asset=asset,
            findings=asset.findings,
        )

    weights = _weight_table(quote)
    if not weights:
        return DeliveryDecision(
            False,
            "no_metal_weight_table",
            "No metal weight could be derived for this configuration, so the file "
            "would arrive at a casting house without the grams that go with it.",
            asset=asset,
            findings=asset.findings,
        )

    policy = active_mesh_policy() if policy is None else policy
    if not policy.declared:
        return DeliveryDecision(
            False,
            "mesh_policy_undeclared",
            _undeclared_detail(asset, policy),
            asset=asset,
            policy=policy,
            findings=asset.findings,
            volume=volume,
        )

    refusal = _policy_refusals(asset, policy)
    if refusal is not None:
        code, detail = refusal
        return DeliveryDecision(
            False,
            code,
            detail,
            asset=asset,
            policy=policy,
            findings=asset.findings,
            volume=volume,
        )

    try:
        bundle = asset.bundle_path
    except DeliveryError as exc:
        # A table whose path escapes the repository is a corrupted artefact, not a
        # customer state. It still gets a named refusal rather than a stack trace:
        # the whole point of this module is that every failure says what it is.
        return DeliveryDecision(
            False,
            "bundle_path_invalid",
            f"The delivery table's export path for {asset.asset_id} is not inside "
            f"this repository, so it is not a bundle anyone promoted: {exc}",
            asset=None,
            policy=policy,
            volume=volume,
        )
    if not bundle.is_dir():
        return DeliveryDecision(
            False,
            "bundle_not_present",
            f"The export bundle {asset.bundle} is not on this machine. Exports live "
            "in gitignored output/ and are not distributed with the repository.",
            asset=asset,
            policy=policy,
            findings=asset.findings,
            volume=volume,
        )

    if verify:
        problems = verify_bytes(asset)
        if problems:
            return DeliveryDecision(
                False,
                "bytes_do_not_match_receipt",
                "The files on disk are not the bytes the receipt vouches for, so "
                "they are not what any review approved: " + "; ".join(problems),
                asset=asset,
                policy=policy,
                findings=asset.findings,
                volume=volume,
            )

    return DeliveryDecision(
        True,
        "deliverable",
        f"{len(asset.files)} receipt-verified Rhino export file(s) for "
        f"{asset.asset_id}, authorised against shop profile "
        f"{policy.profile_id}@{policy.profile_version}. Measurement and geometry "
        "only -- not a manufacturing certification.",
        asset=asset,
        policy=policy,
        warnings=tuple(_policy_warnings(asset, policy)),
        findings=asset.findings,
        volume=volume,
        bytes_verified=verify,
    )


def _undeclared_detail(asset: DeliverableAsset, policy: MeshPolicy) -> str:
    """The refusal this repository ships with. It names the defect and the gap."""
    defects: list[str] = []
    if asset.inverted_part_count:
        defects.append(
            f"{asset.inverted_part_count} of {asset.metal_part_count} metal parts "
            "with inverted normals (F1)"
        )
    if not asset.ships_castable_solid:
        defects.append(
            "no boolean union at all (F2), so the parts interpenetrate and the naive "
            "per-part sum over-states metal"
        )
    sentences = [
        "No calibrated shop profile declares its mesh policy, so nobody has said "
        "whether this geometry is acceptable input."
    ]
    if defects:
        sentences.append(f"This export ships {', and '.join(defects)}.")
    sentences.append(
        "Whether that is deliverable is a property of the receiving shop. "
        f"Reason: {policy.reason}. {policy.detail}"
    )
    return " ".join(sentences)


def _weight_table(quote: Any) -> list[dict[str, Any]]:
    """The Goal 0024 Metal / Grams / DWT / SPG table, or an empty list."""
    if not isinstance(quote, dict):
        return []
    rows = quote.get("metal_weight_by_alloy")
    if not isinstance(rows, list) or not rows:
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def delivery_manifest(
    decision: DeliveryDecision, *, quote: Any = None, job_id: str | None = None
) -> dict[str, Any]:
    """MyJewl's own document that travels inside the archive.

    Explicitly not Rhino output: it is generated here, and it says so. It exists
    because a file arriving at a casting house with no context gets guessed at.
    """
    if not decision.deliverable or decision.asset is None:
        raise DeliveryError("a delivery manifest is only written for a delivery")
    asset = decision.asset
    volume = decision.volume
    quote_json = quote if isinstance(quote, dict) else {}
    return {
        "schema": "myjewl.delivery_manifest.v0_1",
        "generated_by": "fast_mvp/geometry_delivery.py",
        "document_authority": (
            "myjewl_generated_document_not_rhino_geometry -- the STL files in this "
            "archive are Rhino output; this manifest is not."
        ),
        "job_id": job_id,
        "asset": asset.identity(),
        "geometry_state": asset.geometry_state(),
        "files": [dict(item) for item in asset.files],
        "reference_parts_note": (
            "Files with role 'stone' are reference_only_not_for_casting: they exist "
            "so a modelist can check the seat, and are excluded from every weight."
        ),
        "measured_volume": None if volume is None else volume.evidence(),
        "metal_weight_by_alloy": _weight_table(quote_json),
        "metal_weight_note": (
            "One measured volume, every alloy. Metal never changes the geometry, so "
            "switching alloy needs no new solve -- only a different SPG. DWT is troy "
            "pennyweight (1 dwt = 1.55517384 g). SPG figures are nominal trade "
            "values; a shop's own alloy recipe overrides them."
        ),
        "selected_metal": quote_json.get("metal"),
        "mesh_policy": (
            None if decision.policy is None else decision.policy.to_json()
        ),
        "warnings": list(decision.warnings),
        "verification": (
            "Every file above is byte-identical to the SHA-256 recorded in "
            f"{asset.receipt_file}, which is included in this archive. Re-hash them; "
            "do not take our word for it."
        ),
        "scope_note": DELIVERY_SCOPE_NOTE,
        "authority": "receipt_backed_rhino_export_not_manufacturing",
        "certifies_manufacturing": False,
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


def archive_name(decision: DeliveryDecision) -> str:
    if decision.asset is None:  # pragma: no cover - guarded by callers
        raise DeliveryError("no asset to name")
    return f"myjewl_geometry_{decision.asset.asset_id}.zip"


def build_archive(
    decision: DeliveryDecision, *, quote: Any = None, job_id: str | None = None
) -> tuple[bytes, str]:
    """Build the deterministic delivery archive. Returns ``(bytes, sha256)``.

    Every file is re-hashed as it is read: the archive can only ever contain bytes
    the receipt vouches for, so a table edit alone cannot smuggle a file in.
    """
    if not decision.deliverable or decision.asset is None:
        raise DeliveryError(f"refused delivery cannot be built: {decision.reason}")
    asset = decision.asset
    bundle = asset.bundle_path

    # Re-derived from the receipt, not from the decision: the archive is the last
    # place bytes can leave, so it re-establishes the authority rather than
    # inheriting a claim. A decision taken with ``verify=False`` cannot slip a
    # single unverified byte past this loop.
    expectations, problems = receipt_expectations(asset)
    if problems:
        raise DeliveryError("; ".join(problems))

    manifest = delivery_manifest(decision, quote=quote, job_id=job_id)
    entries: list[tuple[str, bytes]] = []
    for item in sorted(asset.files, key=lambda entry: entry["file"]):
        name = str(item["file"])
        expected = expectations.get(name)
        if expected is None:
            raise DeliveryError(f"{name}: the receipt does not vouch for this file")
        payload = (bundle / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise DeliveryError(f"{name}: bytes do not match the receipt hash")
        # The fused solid gets its own directory rather than sitting among the 19
        # named parts: when one exists it is the thing to cast, and a caster should
        # not have to read a manifest to work out which file that is.
        prefix = _ARCHIVE_DIRS[str(item["role"])]
        entries.append((f"{prefix}/{name}", payload))

    receipt = bundle / asset.receipt_file
    if receipt.is_file():
        entries.append((f"provenance/{asset.receipt_file}", receipt.read_bytes()))
    entries.append(
        (
            "delivery_manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ARCHIVE_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
    blob = buffer.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


def delivery_state(*, table_path: Path | str = DEFAULT_TABLE_PATH) -> dict[str, Any]:
    """Whether this deployment can deliver anything, and why not.

    Reported on ``/api/health`` beside ``pricing``, so "can this deployment hand
    over geometry" is one request rather than an inspection.
    """
    policy = active_mesh_policy()
    table, assets = _loaded(table_path)
    records = table.get("records", [])
    present = 0
    for asset in assets.values():
        # Through the same guard ``decide`` uses, so the two surfaces cannot
        # disagree about whether an out-of-repository path counts as present.
        try:
            if asset.bundle_path.is_dir():
                present += 1
        except DeliveryError:
            continue
    return {
        "delivery_source": DELIVERY_SOURCE,
        "format": DELIVERED_FORMAT,
        "deliverable_assets_recorded": len(records),
        "export_bundles_present": present,
        "mesh_policy": policy.to_json(),
        "status": "declared" if policy.declared else policy.reason,
        "detail": (
            policy.detail
            if not policy.declared
            else f"{present} of {len(records)} recorded export bundle(s) are on this "
            "machine and can be verified against their receipts."
        ),
        "shop_profile_env": SHOP_PROFILE_ENV,
        "certifies_manufacturing": False,
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }


def install_profile_from_env(environ: Any = None) -> str | None:
    """Install the shop profile named by the environment, if any.

    Called once at server start. Kept out of ``shop_profile.active_profile`` on
    purpose: that global also feeds ``constraints.py`` thresholds, so which process
    reads the environment is a decision worth being able to see in one place.
    Returns the profile label, or ``None`` when nothing was installed.
    """
    from jewelry_gaas_foundation import shop_profile as _shop_profile

    env = os.environ if environ is None else environ
    path = str(env.get(SHOP_PROFILE_ENV, "") or "").strip()
    if not path:
        return None
    profile = _shop_profile.load_profile(path)
    _shop_profile.set_active_profile(profile)
    return profile.label


__all__ = [
    "DEFAULT_TABLE_PATH",
    "DELIVERED_FORMAT",
    "DELIVERY_SCOPE_NOTE",
    "DELIVERY_SOURCE",
    "SHOP_PROFILE_ENV",
    "TABLE_SCHEMA",
    "DeliverableAsset",
    "DeliveryDecision",
    "DeliveryError",
    "archive_name",
    "build_archive",
    "clear_cache",
    "decide",
    "delivery_manifest",
    "delivery_state",
    "install_profile_from_env",
    "load_table",
    "receipt_expectations",
    "record_for_manifest",
    "validate_table",
    "verify_bytes",
]
