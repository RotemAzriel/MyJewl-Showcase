"""Resolve a promoted configuration to its measured ring volume, or to nothing.

Goal 0023. A quote is only as trustworthy as the volume under it, so this module
is deliberately narrow: it joins a resolved :class:`PreviewManifest` to a table of
volumes measured from receipt-backed export bytes, and it refuses in every case
where the join is not exact.

The refusals are the point:

* the preview must have resolved ``exact`` -- an ``estimated`` preview is an
  approximation of a *picture*, and approximating a *price* is a different and
  worse promise;
* the geometry signature must be present in the table verbatim -- there is no
  nearest-neighbour, no interpolation between solved sizes, and no default;
* the selected asset's id **and** version must match the record, so a table that
  has fallen behind the registry produces ``pending_geometry`` rather than the
  volume of a retired asset.

The basis is the measured mesh union. ``parts_sum_mm3`` travels with the record as
evidence and is structurally unreachable as a basis: the table declares one basis,
the schema pins it with ``const``, and :data:`_BASIS_FIELDS` maps only that one
value to a field. Rationale and the engine decision:
``docs/quote_basis_2026-08-17.md``.

Nothing here certifies manufacturability. A price is a commercial estimate from
measured geometry; geometry gates stay pending / not_run / needs_calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fast_mvp.typed_inputs import MetalSpec, quote_basis

ROOT = Path(__file__).resolve().parents[1]
TABLE_SCHEMA_PATH = ROOT / "schemas" / "measured_volume_table_v0_1.schema.json"
DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parent / "preview_assets" / "measured_volumes_v0_1.json"
)

TABLE_SCHEMA = "myjewl.measured_volume_table.v0_1"

#: The one declared basis, and the only field it may read. Adding a key here is a
#: deliberate product decision, not a configuration change.
QUOTE_VOLUME_BASIS = "measured_mesh_union"
_BASIS_FIELDS = {QUOTE_VOLUME_BASIS: "union_mm3"}

#: Preview statuses a quote may be built on. ``estimated`` is excluded on purpose.
_QUOTABLE_PREVIEW_STATUS = frozenset({"exact"})


class QuoteVolumeError(ValueError):
    """The measured-volume table is structurally unsafe to quote from."""


@dataclass(frozen=True)
class MeasuredVolume:
    """One promoted asset's measured volume, with its evidence attached."""

    asset_id: str
    asset_version: str
    geometry_signature: str
    source_receipt_sha256: str
    definition_path: str
    definition_sha256: str
    definition_kind: str
    template_id: str
    bundle: str
    basis: str
    volume_mm3: float
    union_mm3: float
    union_engine: str
    metal_part_count: int
    part_bytes_verified: int
    parts_sum_mm3: float
    overcount_mm3: float
    overcount_fraction: float
    rhino_nurbs_union_mm3: float | None
    rhino_vs_mesh_fraction: float | None
    metrics_version: str
    measured_on: str

    def evidence(self) -> dict[str, Any]:
        """What the quote must show so its basis is auditable, not asserted."""
        return {
            "basis": self.basis,
            "volume_mm3": self.volume_mm3,
            "union_mm3": self.union_mm3,
            "union_engine": self.union_engine,
            "metal_part_count": self.metal_part_count,
            "parts_sum_mm3": self.parts_sum_mm3,
            "parts_sum_role": "evidence_only_never_the_basis",
            "parts_sum_overcount_mm3": self.overcount_mm3,
            "parts_sum_overcount_fraction": self.overcount_fraction,
            "rhino_nurbs_union_mm3": self.rhino_nurbs_union_mm3,
            "rhino_vs_mesh_fraction": self.rhino_vs_mesh_fraction,
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "geometry_signature": self.geometry_signature,
            "source_receipt_sha256": self.source_receipt_sha256,
            "definition_path": self.definition_path,
            "definition_sha256": self.definition_sha256,
            "definition_kind": self.definition_kind,
            "template_id": self.template_id,
            "bundle": self.bundle,
            "part_bytes_verified": self.part_bytes_verified,
            "metrics_version": self.metrics_version,
            "measured_on": self.measured_on,
            "authority": "measurement_only_not_manufacturing_certification",
        }


@lru_cache(maxsize=4)
def _schema(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_table(table: Any) -> dict[str, Any]:
    """Validate the table's shape and the invariants a schema cannot express."""
    if not isinstance(table, dict):
        raise QuoteVolumeError("measured-volume table must be an object")
    errors = sorted(
        Draft202012Validator(_schema(str(TABLE_SCHEMA_PATH))).iter_errors(table),
        key=lambda err: list(err.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise QuoteVolumeError(
            f"measured-volume table invalid at {location}: {first.message}"
        )
    if table["quote_volume_basis"] not in _BASIS_FIELDS:
        raise QuoteVolumeError(
            f"unsupported quote volume basis: {table['quote_volume_basis']!r}"
        )

    seen_signatures: set[str] = set()
    seen_assets: set[tuple[str, str]] = set()
    for record in table["records"]:
        signature = record["geometry_signature"]
        if signature in seen_signatures:
            raise QuoteVolumeError(
                f"duplicate geometry signature in volume table: {signature}"
            )
        seen_signatures.add(signature)
        identity = (record["asset_id"], record["asset_version"])
        if identity in seen_assets:
            raise QuoteVolumeError(f"duplicate asset in volume table: {identity[0]}")
        seen_assets.add(identity)
        if record["union_mm3"] >= record["parts_sum_mm3"]:
            # A union can only remove double-counted metal, never add any. If it
            # did not, the boolean silently failed and the number is not a union.
            raise QuoteVolumeError(
                f"{record['asset_id']}: union_mm3 must be below the parts sum; "
                "a union that removed nothing is not a union"
            )
        if record["part_bytes_verified"] > record["metal_part_count"]:
            raise QuoteVolumeError(
                f"{record['asset_id']}: verified more parts than were measured"
            )
    return table


def load_table(path: Path | str = DEFAULT_TABLE_PATH) -> dict[str, Any]:
    """Read and validate the table. A missing table quotes nothing, it never raises."""
    table_path = Path(path)
    if not table_path.is_file():
        return {
            "schema": TABLE_SCHEMA,
            "quote_volume_basis": QUOTE_VOLUME_BASIS,
            "records": [],
            "missing": True,
        }
    return validate_table(json.loads(table_path.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _records(path: str) -> dict[str, MeasuredVolume]:
    table = load_table(path)
    basis = table["quote_volume_basis"]
    field = _BASIS_FIELDS[basis]
    out: dict[str, MeasuredVolume] = {}
    for record in table.get("records", []):
        out[record["geometry_signature"]] = MeasuredVolume(
            asset_id=record["asset_id"],
            asset_version=record["asset_version"],
            geometry_signature=record["geometry_signature"],
            source_receipt_sha256=record["source_receipt_sha256"],
            definition_path=record["definition_path"],
            definition_sha256=record["definition_sha256"],
            definition_kind=record["definition_kind"],
            template_id=record["template_id"],
            bundle=record["bundle"],
            basis=basis,
            volume_mm3=float(record[field]),
            union_mm3=float(record["union_mm3"]),
            union_engine=record["union_engine"],
            metal_part_count=record["metal_part_count"],
            part_bytes_verified=record["part_bytes_verified"],
            parts_sum_mm3=float(record["parts_sum_mm3"]),
            overcount_mm3=float(record["overcount_mm3"]),
            overcount_fraction=float(record["overcount_fraction"]),
            rhino_nurbs_union_mm3=record["rhino_nurbs_union_mm3"],
            rhino_vs_mesh_fraction=record["rhino_vs_mesh_fraction"],
            metrics_version=table["metrics_version"],
            measured_on=table["measured_on"],
        )
    return out


def clear_cache() -> None:
    """Forget the loaded table. Tests that write a table call this."""
    _records.cache_clear()


def volume_for_manifest(
    manifest: Any, *, table_path: Path | str = DEFAULT_TABLE_PATH
) -> MeasuredVolume | None:
    """The measured volume for a resolved preview, or ``None``.

    ``None`` is the safe answer and the common one: it leaves the quote at
    ``pending_geometry``. There is no path here that returns a volume belonging to
    a different configuration.
    """
    if not isinstance(manifest, dict):
        return None
    if manifest.get("preview_status") not in _QUOTABLE_PREVIEW_STATUS:
        return None
    geometry = manifest.get("geometry")
    if not isinstance(geometry, dict):
        return None
    record = _records(str(table_path)).get(geometry.get("signature"))
    if record is None:
        return None
    selected = manifest.get("selected_asset")
    if not isinstance(selected, dict):
        return None
    if (
        selected.get("asset_id") != record.asset_id
        or selected.get("asset_version") != record.asset_version
    ):
        # The table has drifted from the registry. Quoting the stale record would
        # price geometry that is no longer served.
        return None
    return record


def attach_quote(
    report: dict[str, Any],
    metal: MetalSpec,
    *,
    metal_price_per_gram: float | None = None,
    table_path: Path | str = DEFAULT_TABLE_PATH,
) -> dict[str, Any]:
    """Set ``report['quote']`` from the resolved preview manifest, in place.

    Call this immediately after ``attach_preview_manifest``: the manifest is what
    decides whether a volume exists, and the typed metal is what turns it into a
    weight.
    """
    record = volume_for_manifest(report.get("preview_manifest"), table_path=table_path)
    report["quote"] = quote_basis(
        metal,
        measured_volume=None if record is None else record.evidence(),
        metal_price_per_gram=metal_price_per_gram,
    )
    return report


def reconcile_quote(
    report: dict[str, Any], *, table_path: Path | str = DEFAULT_TABLE_PATH
) -> dict[str, Any]:
    """Re-derive ``report['quote']`` from the report's own resolved manifest.

    The server persistence boundary recomputes the PreviewManifest even when a
    caller supplied one, because a client must not choose which asset represents
    its ring. A volume is the same kind of claim and a stronger one — it becomes a
    price — so it is recomputed here too. Anything a caller wrote into the quote
    is discarded except the alloy spot price, which is a commercial input the
    caller legitimately owns.

    A report carrying no quote at all is left alone: this function exists to narrow
    a quote, never to invent one. A quote whose typed metal will not parse is
    *emptied* rather than trusted -- it cannot be re-derived, so it cannot be
    vouched for, and the volume-bearing fields are the ones that become a price.
    """
    quote = report.get("quote")
    if not isinstance(quote, dict):
        return report
    try:
        metal = MetalSpec.from_json(quote.get("metal"))
    except Exception:
        report["quote"] = {
            **quote,
            "status": "pending_geometry",
            "ring_volume_mm3": None,
            "volume_basis": None,
            "volume_evidence": None,
            "metal_weight_g": None,
            "metal_content_cost": None,
            "reason": (
                "Quote discarded: its typed metal could not be re-derived at the "
                "server boundary, so no weight or price can be vouched for."
            ),
        }
        return report
    price = quote.get("metal_price_per_gram")
    return attach_quote(
        report,
        metal,
        metal_price_per_gram=None if price is None else float(price),
        table_path=table_path,
    )


__all__ = [
    "DEFAULT_TABLE_PATH",
    "QUOTE_VOLUME_BASIS",
    "TABLE_SCHEMA",
    "MeasuredVolume",
    "QuoteVolumeError",
    "attach_quote",
    "clear_cache",
    "reconcile_quote",
    "load_table",
    "validate_table",
    "volume_for_manifest",
]
