"""Typed customer inputs — the product contract (Goal 0005, corrected 2026-07-27).

Three things the customer supplies. **None of them is ever inferred from pixels.**

1. **Stone** — type/shape plus L×W×D in millimetres, *or* a GIA/IGI certificate
   that states them (the preferred path; manual entry is the fallback).
2. **Ring size** — US.
3. **Metal** — colour family, karat/purity, and finish.

Metal is a **cost/appearance overlay, never geometry** (settled decision 6). It
reaches the 3D preview, the material appearance, the metal weight, and the quote.
It is never inferred, retrieved, or scored by the image lane: two rings identical
except for metal are the *same geometry* and must retrieve identically, so metal
is excluded from the similarity axes entirely (``fast_mvp.catalog_index``).

Nothing here is a manufacturing rule. Alloy densities are nominal trade figures
for quoting weight; the shop's own alloy recipe overrides them, and no number in
this module certifies anything. Constraint thresholds continue to live only in
``jewelry_gaas_foundation.constraints``, and alloy *prices* live only in
``fast_mvp.alloy_prices`` (Goal 0024) — a cost moves no gate and touches no
geometry.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fast_mvp import alloy_prices

METAL_COLOR_FAMILIES = ("yellow", "white", "rose")

# Purity/karat space = exactly what the proposer's alloy enum and the Blender
# material table already support. Extending it means extending both, plus
# ``scripts/build_catalog_renders.ALLOYS_BY_FAMILY``.
GOLD_KARATS = ("14k", "18k")
PLATINUM_PURITIES = ("pt950",)
METAL_PURITIES = GOLD_KARATS + PLATINUM_PURITIES
METAL_FINISHES = ("polished", "matte", "brushed")

# Nominal trade densities (g/cm³) used for **quoting weight only**. Pt950 at 20.7
# is the same figure the constraint engine cites in its METAL-003 note.
_DENSITY_G_CM3: dict[tuple[str, str], float] = {
    ("14k", "yellow"): 13.1,
    ("14k", "white"): 12.9,
    ("14k", "rose"): 13.0,
    ("18k", "yellow"): 15.5,
    ("18k", "white"): 15.2,
    ("18k", "rose"): 15.2,
    ("pt950", "white"): 20.7,
}

DEFAULT_METAL_ALLOY = "14k_yellow"

#: Grams per troy pennyweight (1/20 troy ounce), exact by definition. DWT is the
#: unit casting houses actually quote in -- Matrix's METAL WEIGHTS panel puts it
#: beside grams for the same reason, and a bench that reads grams where it expects
#: dwt is out by 1.55x.
GRAMS_PER_DWT = 1.55517384

LAB_CHOICES = ("gia", "igi")

# GIA/IGI print round measurements as "6.50 - 6.55 x 4.00 mm" (min-max diameter
# then depth) and fancy shapes as "8.01 x 5.98 x 3.92 mm".
_MEASUREMENT_RANGE = re.compile(
    r"^\s*([\d.]+)\s*-\s*([\d.]+)\s*[x×]\s*([\d.]+)\s*(?:mm)?\s*$", re.IGNORECASE
)
_MEASUREMENT_TRIPLE = re.compile(
    r"^\s*([\d.]+)\s*[x×]\s*([\d.]+)\s*[x×]\s*([\d.]+)\s*(?:mm)?\s*$", re.IGNORECASE
)


class TypedInputError(ValueError):
    """A typed input is missing or self-contradictory."""


@dataclass(frozen=True)
class MetalSpec:
    """Typed metal: colour family + karat/purity + finish."""

    color_family: str
    purity: str
    alloy: str
    metal_type: str
    density_g_cm3: float
    display_name: str
    finish: str = "polished"
    source: str = "typed"

    @property
    def is_typed(self) -> bool:
        return self.source == "typed"

    def to_json(self) -> dict[str, Any]:
        return {
            "color_family": self.color_family,
            "purity": self.purity,
            "alloy": self.alloy,
            "metal_type": self.metal_type,
            "density_g_cm3": self.density_g_cm3,
            "display_name": self.display_name,
            "finish": self.finish,
            "source": self.source,
            "role": "preview_appearance_weight_and_quote_only",
        }

    @classmethod
    def from_json(cls, payload: Any) -> "MetalSpec":
        """Rebuild from :meth:`to_json`.

        The server re-derives a persisted quote from the report it is about to
        store, and the typed metal it must use is the one already in that report —
        re-parsing the original request is not available at that boundary. The
        density is taken from the payload rather than re-looked-up so a spec that
        was built under a different density table cannot silently change weight.
        """
        try:
            return cls(
                color_family=str(payload["color_family"]),
                purity=str(payload["purity"]),
                alloy=str(payload["alloy"]),
                metal_type=str(payload["metal_type"]),
                density_g_cm3=float(payload["density_g_cm3"]),
                display_name=str(payload["display_name"]),
                finish=str(payload.get("finish", "polished")),
                source=str(payload.get("source", "typed")),
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise TypedInputError(f"metal payload is not a typed metal: {exc}") from exc


def _normalize_purity(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace(" ", "").replace("-", "")
    text = text.replace("karat", "k").replace("kt", "k").replace("ct", "k")
    aliases = {
        "14": "14k",
        "14k": "14k",
        "585": "14k",
        "18": "18k",
        "18k": "18k",
        "750": "18k",
        "pt": "pt950",
        "pt950": "pt950",
        "platinum": "pt950",
        "platinum950": "pt950",
        "plat": "pt950",
        "950": "pt950",
    }
    return aliases.get(text)


def _normalize_family(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace(" ", "_")
    aliases = {
        "yellow": "yellow",
        "yellow_gold": "yellow",
        "white": "white",
        "white_gold": "white",
        "platinum": "white",
        "silver": "white",
        "rose": "rose",
        "rose_gold": "rose",
        "pink": "rose",
    }
    return aliases.get(text)


def _normalize_finish(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "polished": "polished",
        "high_polish": "polished",
        "matte": "matte",
        "matt": "matte",
        "brushed": "brushed",
        "satin": "brushed",
    }
    return aliases.get(text)


def _split_alloy(value: Any) -> tuple[str | None, str | None]:
    """Split an alloy descriptor such as ``18k_rose`` or ``pt950`` into parts."""
    if value in (None, ""):
        return None, None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    text = text.replace("_gold", "").replace("_platinum", "")
    parts = [part for part in text.split("_") if part]
    purity: str | None = None
    family: str | None = None
    for part in parts:
        purity = purity or _normalize_purity(part)
        family = family or _normalize_family(part)
    return purity, family


def build_metal_spec(
    color_family: str | None,
    purity: str | None,
    *,
    finish: str | None = "polished",
    source: str = "typed",
) -> MetalSpec:
    """Assemble a metal spec, rejecting combinations that do not exist."""
    resolved_purity = _normalize_purity(purity) or "14k"
    resolved_family = _normalize_family(color_family)
    if resolved_purity == "pt950":
        if resolved_family not in (None, "white"):
            raise TypedInputError(
                "pt950 is a white metal; choose 14k or 18k for a yellow or rose ring."
            )
        resolved_family = "white"
    resolved_family = resolved_family or "yellow"
    resolved_finish = _normalize_finish(finish)
    if resolved_finish is None:
        raise TypedInputError(
            f"metal finish must be one of {', '.join(METAL_FINISHES)}."
        )

    if resolved_purity not in METAL_PURITIES:
        raise TypedInputError(
            f"metal purity must be one of {', '.join(METAL_PURITIES)}."
        )
    density = _DENSITY_G_CM3.get((resolved_purity, resolved_family))
    if density is None:
        raise TypedInputError(
            f"no {resolved_purity} alloy exists in {resolved_family}."
        )

    if resolved_purity == "pt950":
        alloy = "pt950"
        metal_type = "platinum"
        display = "Platinum 950"
    else:
        alloy = f"{resolved_purity}_{resolved_family}"
        metal_type = "gold"
        display = f"{resolved_purity} {resolved_family} gold"
    return MetalSpec(
        color_family=resolved_family,
        purity=resolved_purity,
        alloy=alloy,
        metal_type=metal_type,
        density_g_cm3=density,
        display_name=display,
        finish=resolved_finish,
        source=source,
    )


def parse_metal_spec(payload: dict[str, Any]) -> MetalSpec:
    """Read the typed metal from a request payload.

    Accepted spellings — a nested ``metal`` object, flat
    ``metal_color_family``/``metal_karat``, a combined ``metal_alloy``, or the
    legacy ``mounting_overrides.metal_color_family``. When the customer has not
    picked a metal yet the launch default is used and labelled
    ``unspecified_default``: absent is absent, never "read off the photo".
    """
    nested = payload.get("metal") if isinstance(payload.get("metal"), dict) else {}
    overrides = (
        payload.get("mounting_overrides")
        if isinstance(payload.get("mounting_overrides"), dict)
        else {}
    )

    family = _first(
        nested.get("color_family"),
        nested.get("color"),
        payload.get("metal_color_family"),
        payload.get("metal_color"),
        overrides.get("metal_color_family"),
    )
    purity = _first(
        nested.get("purity"),
        nested.get("karat"),
        payload.get("metal_karat"),
        payload.get("metal_purity"),
        payload.get("metal_karat_or_purity"),
        overrides.get("metal_karat"),
    )
    finish = _first(
        nested.get("finish"),
        payload.get("metal_finish"),
        overrides.get("metal_finish"),
    ) or "polished"
    alloy_purity, alloy_family = _split_alloy(
        _first(nested.get("alloy"), payload.get("metal_alloy"), overrides.get("metal_alloy"))
    )
    family = family if family is not None else alloy_family
    purity = purity if purity is not None else alloy_purity

    if family is None and purity is None:
        return build_metal_spec(
            None, None, finish=finish, source="unspecified_default"
        )
    return build_metal_spec(family, purity, finish=finish, source="typed")


def _first(*values: Any) -> Any | None:
    for value in values:
        if value not in (None, "", "auto"):
            return value
    return None


@dataclass(frozen=True)
class StoneCertificate:
    """A GIA/IGI report as the *source* of the typed stone spec."""

    lab: str
    report_number: str
    shape: str | None = None
    length_mm: float | None = None
    width_mm: float | None = None
    depth_mm: float | None = None
    carat: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "lab": self.lab.upper(),
            "report_number": self.report_number,
            "shape": self.shape,
            "measurements_mm": (
                None
                if self.length_mm is None
                else {
                    "length": self.length_mm,
                    "width": self.width_mm,
                    "depth": self.depth_mm,
                }
            ),
            "carat": self.carat,
        }


def parse_measurements(value: Any) -> tuple[float, float, float] | None:
    """Parse a certificate measurement field into (length, width, depth) mm.

    Handles the printed forms — ``"6.50 - 6.55 x 4.00 mm"`` (round: min-max
    diameter then depth) and ``"8.01 x 5.98 x 3.92 mm"`` — plus an explicit
    object. Anything else returns ``None``; a certificate that does not state
    measurements does not get invented ones.
    """
    if isinstance(value, dict):
        values = (value.get("length"), value.get("width"), value.get("depth"))
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in values
        ):
            return None
        return tuple(float(item) for item in values)
    if not isinstance(value, str):
        return None

    def lossless_number(token: str) -> float:
        try:
            decimal = Decimal(token)
            number = float(decimal)
        except (InvalidOperation, OverflowError, ValueError) as exc:
            raise ValueError("certificate measurement is not representable") from exc
        if not decimal.is_finite() or not math.isfinite(number):
            raise ValueError("certificate measurement must be finite")
        if Decimal(str(number)) != decimal:
            raise ValueError("certificate measurement would lose precision")
        return number

    ranged = _MEASUREMENT_RANGE.match(value)
    if ranged:
        try:
            low, high, depth = (lossless_number(group) for group in ranged.groups())
        except ValueError:
            return None
        return (max(low, high), min(low, high), depth)
    triple = _MEASUREMENT_TRIPLE.match(value)
    if triple:
        try:
            first, second, depth = (
                lossless_number(group) for group in triple.groups()
            )
        except ValueError:
            return None
        return (max(first, second), min(first, second), depth)
    return None


def parse_certificate(payload: dict[str, Any]) -> StoneCertificate | None:
    """Read a supplied GIA/IGI certificate block, or ``None`` when absent."""
    raw = payload.get("certificate") or payload.get("stone_certificate")
    if not isinstance(raw, dict):
        return None
    lab = str(raw.get("lab") or raw.get("laboratory") or "").strip().lower()
    if lab not in LAB_CHOICES:
        raise TypedInputError(
            f"certificate lab must be one of {', '.join(choice.upper() for choice in LAB_CHOICES)}."
        )
    report_number = str(raw.get("report_number") or raw.get("number") or "").strip()
    if not report_number:
        raise TypedInputError("certificate report_number is required.")
    measurements = parse_measurements(
        raw.get("measurements_mm") or raw.get("measurements")
    )
    carat_raw = raw.get("carat") or raw.get("carat_weight")
    try:
        carat = None if carat_raw in (None, "") else float(carat_raw)
    except (TypeError, ValueError) as exc:
        raise TypedInputError("certificate carat must be a number.") from exc
    shape = raw.get("shape")
    return StoneCertificate(
        lab=lab,
        report_number=report_number,
        shape=str(shape).strip().lower() if shape else None,
        length_mm=measurements[0] if measurements else None,
        width_mm=measurements[1] if measurements else None,
        depth_mm=measurements[2] if measurements else None,
        carat=carat,
    )


def apply_certificate(payload: dict[str, Any]) -> tuple[dict[str, Any], StoneCertificate | None]:
    """Fill the typed stone fields from a certificate when they are absent.

    The certificate is the *preferred* stone path, so its measurements populate
    the same typed fields manual entry would. An explicitly typed value always
    wins — the customer measuring their own stone is still a typed input.
    """
    certificate = parse_certificate(payload)
    if certificate is None:
        return payload, None
    filled = dict(payload)
    for key, value in (
        ("stone_shape", certificate.shape),
        ("stone_length_mm", certificate.length_mm),
        ("stone_width_mm", certificate.width_mm),
        ("stone_depth_mm", certificate.depth_mm),
        ("stone_carat_eq", certificate.carat),
    ):
        if value is not None and filled.get(key) in (None, ""):
            filled[key] = value
    return filled, certificate


def stone_spec_source(certificate: StoneCertificate | None) -> str:
    return f"certificate:{certificate.lab}" if certificate else "manual_entry"


def weights_by_alloy(
    ring_volume_mm3: float, *, selected_alloy: str | None = None
) -> list[dict[str, Any]]:
    """The one measured volume expressed in **every** alloy MyJewl can build.

    This is the product's actual deliverable. MyJewl is Geometry-as-a-Service: it
    measures a ring and says how much metal it takes. Metal is a cost/appearance
    overlay and never geometry (settled decision 6), so a single measured volume
    answers the weight question for all seven alloys at once — the ring is the same
    ring, only the density changes. Whoever sells it applies their own price.

    Returned in catalog order (14k yellow/white/rose, 18k yellow/white/rose, pt950)
    so a customer comparing alloys reads a stable table. ``selected_alloy`` marks
    the typed choice without changing any of the numbers.

    Columns mirror the trade's own presentation — Matrix's METAL WEIGHTS panel
    reads **Metal · Grams · DWT · SPG** — because this table is read by benches and
    casting houses, not only by customers. ``spg`` (specific gravity, relative to
    water at 1.000 g/cm³) is numerically the density in g/cm³; both are emitted so
    neither audience has to translate.

    Densities are nominal trade figures, not a shop's alloy recipe, so every row is
    indicative. Real libraries disagree: Matrix's stock 14KY is 12.88 against the
    13.1 used here, ~1.7%. Nothing here certifies manufacturability.
    """
    volume = float(ring_volume_mm3)
    rows: list[dict[str, Any]] = []
    for purity, family in _DENSITY_G_CM3:
        # Built through build_metal_spec so the alloy token, display name, and
        # density can never drift from what the rest of the product uses.
        spec = build_metal_spec(family, purity)
        grams = volume * spec.density_g_cm3 / 1000.0
        rows.append(
            {
                "alloy": spec.alloy,
                "display_name": spec.display_name,
                "purity": spec.purity,
                "color_family": spec.color_family,
                "metal_type": spec.metal_type,
                "density_g_cm3": spec.density_g_cm3,
                "spg": spec.density_g_cm3,
                "metal_weight_g": round(grams, 3),
                "metal_weight_dwt": round(grams / GRAMS_PER_DWT, 3),
                "selected": spec.alloy == selected_alloy,
            }
        )
    return rows


def _resolved_price(
    metal: MetalSpec, metal_price_per_gram: float | None
) -> "alloy_prices.AlloyPrice":
    """Which price applies to this alloy, and where it came from.

    A caller-supplied spot price wins over the declared sheet. Unlike a volume, a
    spot price is legitimately the caller's to give — an integrator quoting against
    its own hedged rate is the normal case — so that path stays open. What it does
    not get is provenance: it carries no citation, no ``as_of``, and no expiry, and
    it is labelled accordingly.
    """
    if metal_price_per_gram is not None:
        return alloy_prices.AlloyPrice(
            alloy=metal.alloy,
            price_per_gram=float(metal_price_per_gram),
            currency=None,
            usable=True,
            reason="caller_supplied",
            detail=(
                "Spot price supplied with the request. It carries no citation, no "
                "as-of date, no currency, and no expiry."
            ),
            source="caller_supplied_request_field",
        )
    return alloy_prices.active_price(metal.alloy)


def quote_basis(
    metal: MetalSpec,
    *,
    ring_volume_mm3: float | None = None,
    measured_volume: dict[str, Any] | None = None,
    metal_price_per_gram: float | None = None,
) -> dict[str, Any]:
    """Metal weight and metal-content cost from the typed metal.

    Weight needs a ring volume, and volume comes from Rhino geometry — so until a
    receipt-backed volume exists for *this* configuration the quote is
    ``pending_geometry`` rather than a guess. With a volume the weight is
    ``volume × density``; with a price too, the metal content is priced.
    Stone, labour, and finishing are out of scope, and nothing here certifies
    manufacturability.

    ``measured_volume`` (Goal 0023) is the real path: the evidence block from
    ``fast_mvp.quote_volumes``, measured from the export bytes the promoted asset
    was built from. Its ``volume_mm3`` is the union, never the naive parts sum,
    and the whole block travels into the quote so the basis is auditable rather
    than asserted. ``ring_volume_mm3`` remains for callers holding a bare number
    (a measurement harness, a test); it carries no provenance, so it is reported
    as such.

    The price (Goal 0024) resolves the same way, and is reported the same way:
    ``price_source`` names where it came from and ``price_evidence`` carries the
    citation, the currency, the as-of date, and — at the number itself — what the
    cost excludes. When nothing prices it, ``price_refusal`` names *which*
    condition failed. That block is present even on a ``pending_geometry`` quote,
    so the shipped "no sheet declared" state is visible everywhere rather than only
    where a weight already exists.

    **``metal_price_per_gram`` reports the caller's input only, never the sheet's
    price.** The applied number is ``price_evidence["price_per_gram"]``. Keeping
    them separate is what stops ``quote_volumes.reconcile_quote`` — which re-derives
    a persisted quote and hands the caller's price back — from laundering a sheet
    price into a caller-supplied one on the way through.
    """
    quote: dict[str, Any] = {
        "metal": metal.to_json(),
        "basis": "ring_volume_mm3 × alloy density (SPG); volume comes from Rhino geometry",
        "ring_volume_mm3": None,
        "volume_basis": None,
        "volume_evidence": None,
        "metal_weight_g": None,
        "metal_weight_dwt": None,
        "metal_weight_by_alloy": None,
        "metal_price_per_gram": metal_price_per_gram,
        "metal_content_cost": None,
        "currency": None,
        "price_source": None,
        "price_evidence": None,
        "price_refusal": None,
        "excludes": list(alloy_prices.COST_EXCLUDES),
        "manufacturing_state": "pending_not_run_needs_calibration",
        "non_certifying_note": (
            "Indicative metal content only. Densities are nominal trade figures; "
            "the shop's alloy recipe and a calibrated quote override them. This is "
            "not a manufacturing certification."
        ),
    }

    # Resolve the price first so its state is reported even when no volume exists.
    # A cost needs both; each absence is reported on its own terms.
    price = _resolved_price(metal, metal_price_per_gram)
    if price.usable:
        quote["currency"] = price.currency
        quote["price_evidence"] = price.evidence()
        quote["price_source"] = (
            "caller_supplied_spot_price_no_provenance"
            if price.reason == "caller_supplied"
            else f"alloy_price_sheet:{price.sheet_id}@{price.sheet_version}"
        )
    else:
        quote["price_refusal"] = {
            "reason": price.reason,
            "detail": price.detail,
            "alloy": price.alloy,
            "sheet_id": price.sheet_id,
            "sheet_version": price.sheet_version,
            "no_fallback_note": (
                "No last-known price is reused and no other alloy's price is "
                "substituted. An unpriced quote is the correct output here."
            ),
        }

    volume: float | None = None
    if measured_volume is not None:
        volume = float(measured_volume["volume_mm3"])
        quote["volume_basis"] = measured_volume.get("basis")
        quote["volume_evidence"] = dict(measured_volume)
    elif ring_volume_mm3 is not None:
        volume = float(ring_volume_mm3)
        quote["volume_basis"] = "caller_supplied_volume_no_provenance"

    if volume is None:
        quote["status"] = "pending_geometry"
        quote["reason"] = (
            "No receipt-backed ring volume for this configuration: geometry is "
            "produced by an operator-approved Rhino run, the app never triggers "
            "paid compute, and no neighbouring configuration's volume is "
            "substituted for a typed input."
        )
        return quote

    weight_g = volume * metal.density_g_cm3 / 1000.0
    quote["status"] = "indicative"
    quote["ring_volume_mm3"] = round(volume, 3)
    quote["metal_weight_g"] = round(weight_g, 3)
    # Pennyweight beside grams: it is the unit a casting house quotes in.
    quote["metal_weight_dwt"] = round(weight_g / GRAMS_PER_DWT, 3)
    # The same ring in every alloy. One measured volume answers all seven, because
    # metal never changes the geometry — which is exactly why a customer switching
    # alloy needs no new solve, only a different density.
    quote["metal_weight_by_alloy"] = weights_by_alloy(
        volume, selected_alloy=metal.alloy
    )
    if price.usable and price.price_per_gram is not None:
        quote["metal_content_cost"] = round(weight_g * price.price_per_gram, 2)
    else:
        quote["reason"] = (
            "Metal weight is measured; the metal content is unpriced. "
            f"{price.detail}"
        )
    return quote


def preview_metal(metal: MetalSpec) -> dict[str, Any]:
    """What the 3D preview needs to render the material."""
    return {
        "color_family": metal.color_family,
        "purity": metal.purity,
        "alloy": metal.alloy,
        "metal_type": metal.metal_type,
        "display_name": metal.display_name,
        "source": metal.source,
    }


__all__ = [
    "DEFAULT_METAL_ALLOY",
    "GOLD_KARATS",
    "GRAMS_PER_DWT",
    "LAB_CHOICES",
    "METAL_COLOR_FAMILIES",
    "METAL_PURITIES",
    "MetalSpec",
    "StoneCertificate",
    "TypedInputError",
    "apply_certificate",
    "build_metal_spec",
    "parse_certificate",
    "parse_measurements",
    "parse_metal_spec",
    "preview_metal",
    "quote_basis",
    "stone_spec_source",
    "weights_by_alloy",
]
