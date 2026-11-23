"""Where an alloy price is allowed to come from, and everywhere it is not.

Goal 0024. Goal 0023 made the *weight* real: the four promoted configurations
report a metal weight measured from receipt-verified export bytes. The cost stayed
``null`` because nothing in the repository knew what an alloy costs. This module is
the slot that number lands in -- and, just as importantly, the set of refusals that
keep a *guessed* number out of it.

It mirrors ``jewelry_gaas_foundation.shop_profile`` deliberately: versioned, cited,
dated, per-alloy, with an expiry, and a committed template that **ships declaring
nothing and refuses all of its own values**. Inventing market data is the same
failure as inventing a volume.

**A price is usable only if all of these hold:**

1. a sheet is loaded at all;
2. the sheet's ``status`` is ``declared``;
3. the sheet has not expired;
4. the alloy has an entry -- there is no wildcard, because pt950 and 14k_yellow
   differ by roughly an order of magnitude per gram, so a price spanning alloys is
   always wrong for at least one of them;
5. the entry's ``status`` is ``declared``;
6. the entry has not itself expired -- alloys move independently, so a sheet-level
   expiry alone is not enough;
7. the entry states its ``price_basis`` (what the number covers), because a reader
   cannot otherwise tell whether casting loss and refining are already in it.

Anything failing those leaves the metal content unpriced, with a reason naming
*which* condition failed. **There is no fall back to a last known price**: a stale
price is worse than no price, because it looks live.

**Why this module lives in ``fast_mvp`` and not in the foundation package.** Price
must never become a constraint input, a retrieval axis, or part of a geometry
signature (settled decision 6). ``jewelry_gaas_foundation`` does not import
``fast_mvp`` anywhere, so putting the price here makes that a structural property of
the layering rather than a comment someone has to honour. A test asserts it.

Nothing here certifies manufacturability. A cost is a commercial estimate over an
already-measured weight; geometry gates stay pending / not_run / needs_calibration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PRICE_SHEET_SCHEMA_PATH = ROOT / "schemas" / "alloy_price_sheet_v0_1.schema.json"
UNDECLARED_TEMPLATE_PATH = (
    ROOT / "profiles" / "alloy_price_sheet_undeclared_template_v0_1.json"
)
SHEET_SCHEMA = "myjewl.alloy_price_sheet.v0_1"

#: Point this at a real sheet to price. Unset, the shipped template loads and
#: refuses everything, which is the state the repository ships in.
SHEET_PATH_ENV = "MYJEWL_ALLOY_PRICE_SHEET"

#: Bases a price may declare. ``unknown`` is deliberately absent: it parses, but
#: :meth:`AlloyPriceSheet.price` refuses it.
USABLE_PRICE_BASES = frozenset({"spot_metal_only", "fabricated_metal_including_loss"})

#: What a metal-content cost never includes. The quote carries this list beside the
#: number so a customer cannot read one figure as the price of a finished ring.
COST_EXCLUDES = ("stone", "labor", "finishing", "setting", "margin")

COST_SCOPE_NOTE = (
    "Metal content only, at the declared alloy price. A finished ring also costs "
    "the stone, labour, setting, finishing, and margin -- this is not the ring's "
    "price, and it is not a manufacturing certification."
)


class AlloyPriceError(ValueError):
    """The price sheet is structurally or semantically unusable."""


@dataclass(frozen=True)
class AlloyPrice:
    """A resolved price per gram plus why it is or is not usable.

    ``usable`` is the only thing callers should branch on. When it is false,
    ``price_per_gram`` is ``None`` and the metal content stays unpriced.
    """

    alloy: str
    price_per_gram: float | None
    currency: str | None
    usable: bool
    reason: str
    detail: str
    sheet_id: str | None = None
    sheet_version: str | None = None
    as_of: str | None = None
    expires_on: str | None = None
    price_basis: str | None = None
    source: str | None = None

    def evidence(self) -> dict[str, Any]:
        """What the quote shows beside the cost so the price is auditable.

        The exclusions and the scope note travel *here*, at the price, rather than
        only at the top of the quote -- a number read on its own is the thing that
        misleads.
        """
        return {
            "alloy": self.alloy,
            "price_per_gram": self.price_per_gram,
            "currency": self.currency,
            "as_of": self.as_of,
            "expires_on": self.expires_on,
            "price_basis": self.price_basis,
            "source": self.source,
            "sheet_id": self.sheet_id,
            "sheet_version": self.sheet_version,
            "covers": "metal_content_only",
            "excludes": list(COST_EXCLUDES),
            "scope_note": COST_SCOPE_NOTE,
            "authority": "commercial_input_only_not_manufacturing_certification",
            "certifies_manufacturing": False,
            "affects_geometry": False,
        }


def _validate_with_schema(document: dict[str, Any]) -> None:
    schema = json.loads(PRICE_SHEET_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda err: list(err.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise AlloyPriceError(f"alloy price sheet is invalid at {location}: {first.message}")


class AlloyPriceSheet:
    """A loaded, schema-valid alloy price sheet.

    Construction never prices anything. :meth:`price` is the only way a number
    leaves this object, and it refuses everything that is not fully attributable.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        if not isinstance(document, dict):
            raise AlloyPriceError("alloy price sheet must be an object")
        _validate_with_schema(document)
        if document["schema"] != SHEET_SCHEMA:
            raise AlloyPriceError("unsupported alloy price sheet schema")
        if document.get("certifies_manufacturing") is not False:
            raise AlloyPriceError("an alloy price can never certify manufacturing")
        if document.get("affects_geometry") is not False:
            raise AlloyPriceError("an alloy price can never affect geometry")
        self._document = json.loads(json.dumps(document))  # defensive deep copy

    # -- identity -----------------------------------------------------------
    @property
    def sheet_id(self) -> str:
        return str(self._document["sheet_id"])

    @property
    def sheet_version(self) -> str:
        return str(self._document["sheet_version"])

    @property
    def status(self) -> str:
        return str(self._document["status"])

    @property
    def currency(self) -> str:
        return str(self._document["currency"])

    @property
    def label(self) -> str:
        return f"{self.sheet_id}@{self.sheet_version}"

    @property
    def expires_on(self) -> str:
        return str(self._document["validity"]["expires_on"])

    @property
    def document(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._document))

    def expired_on(self, today: date | None = None) -> bool:
        today = today or date.today()
        return today > date.fromisoformat(self.expires_on)

    # -- price resolution ---------------------------------------------------
    def price(self, alloy: str, *, today: date | None = None) -> AlloyPrice:
        """Resolve one alloy's price, refusing anything not fully attributable."""
        today = today or date.today()
        alloy = str(alloy or "").strip().lower()

        def refuse(reason: str, detail: str) -> AlloyPrice:
            return AlloyPrice(
                alloy=alloy,
                price_per_gram=None,
                currency=None,
                usable=False,
                reason=reason,
                detail=detail,
                sheet_id=self.sheet_id,
                sheet_version=self.sheet_version,
            )

        if self.status != "declared":
            return refuse(
                "sheet_undeclared",
                f"Alloy price sheet {self.label} declares no prices. No price was invented.",
            )
        if self.expired_on(today):
            return refuse(
                "sheet_expired",
                f"Alloy price sheet {self.label} expired on {self.expires_on}. "
                "A stale price is not reused; re-declare the sheet.",
            )

        entry = self._document.get("prices", {}).get(alloy)
        if entry is None:
            return refuse(
                "no_price_for_alloy",
                f"Alloy price sheet {self.label} declares no price for {alloy}. "
                "No other alloy's price is substituted.",
            )
        if entry["status"] != "declared":
            return refuse(
                "price_undeclared",
                f"The {alloy} entry in {self.label} is a placeholder, not a declared price.",
            )
        entry_expiry = str(entry["expires_on"])
        if today > date.fromisoformat(entry_expiry):
            return refuse(
                "price_expired",
                f"The {alloy} price in {self.label} expired on {entry_expiry}. "
                "A stale price is not reused; re-declare it.",
            )
        if entry["price_basis"] not in USABLE_PRICE_BASES:
            return refuse(
                "price_basis_unknown",
                f"The {alloy} price in {self.label} does not state what it covers, "
                "so a reader cannot tell whether casting loss is already included.",
            )

        return AlloyPrice(
            alloy=alloy,
            price_per_gram=float(entry["price_per_gram"]),
            currency=self.currency,
            usable=True,
            reason="declared",
            detail=(
                f"{self.currency} {entry['price_per_gram']}/g for {alloy} "
                f"({entry['price_basis']}), as of {entry['as_of']}, from "
                f"{entry['provenance']['source']}."
            ),
            sheet_id=self.sheet_id,
            sheet_version=self.sheet_version,
            as_of=str(entry["as_of"]),
            expires_on=entry_expiry,
            price_basis=str(entry["price_basis"]),
            source=str(entry["provenance"]["source"]),
        )

    def usable_prices(self, *, today: date | None = None) -> dict[str, float]:
        """Only the prices that survive every attribution check."""
        resolved: dict[str, float] = {}
        for alloy in self._document.get("prices", {}):
            item = self.price(alloy, today=today)
            if item.usable and item.price_per_gram is not None:
                resolved[alloy] = item.price_per_gram
        return resolved


def load_sheet(path: Path | str) -> AlloyPriceSheet:
    """Load and validate a price sheet from disk."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlloyPriceError(f"cannot read alloy price sheet: {path}") from exc
    return AlloyPriceSheet(document)


def undeclared_template() -> AlloyPriceSheet:
    """Load the shipped template. It declares nothing and prices nothing."""
    return load_sheet(UNDECLARED_TEMPLATE_PATH)


# ---------------------------------------------------------------------------
# Active-sheet registry.
#
# ``quote_basis`` keeps its signature, the same reason ``constraints.validate``
# does: an out-of-band active sheet means every existing call site picks the price
# up without being edited, and the "no sheet declared" state is visible everywhere
# at once rather than in whichever surface remembered to ask.
# ---------------------------------------------------------------------------

_ACTIVE_SHEET: AlloyPriceSheet | None = None
_ACTIVE_SET = False


def set_active_sheet(sheet: AlloyPriceSheet | None) -> None:
    """Install (or clear) the sheet the quote reads prices from."""
    global _ACTIVE_SHEET, _ACTIVE_SET
    if sheet is not None and not isinstance(sheet, AlloyPriceSheet):
        raise AlloyPriceError("active sheet must be an AlloyPriceSheet or None")
    _ACTIVE_SHEET = sheet
    _ACTIVE_SET = True


def clear_active_sheet() -> None:
    """Forget any installed sheet and re-read the environment on next use."""
    global _ACTIVE_SHEET, _ACTIVE_SET
    _ACTIVE_SHEET = None
    _ACTIVE_SET = False
    _sheet_from_path.cache_clear()


@lru_cache(maxsize=4)
def _sheet_from_path(path: str) -> AlloyPriceSheet | None:
    try:
        return load_sheet(path)
    except AlloyPriceError:
        # A malformed sheet must not price, and must not take the app down either.
        # Returning None leaves every quote unpriced with ``no_price_sheet``.
        return None


def active_sheet() -> AlloyPriceSheet | None:
    """The installed sheet, the one named by the environment, or the template.

    Resolution order: an explicitly installed sheet (including an explicit
    ``None``), then ``MYJEWL_ALLOY_PRICE_SHEET``, then the shipped undeclared
    template. The template is loaded rather than skipped so the refusal a customer
    sees names a real sheet and a real reason.
    """
    if _ACTIVE_SET:
        return _ACTIVE_SHEET
    override = os.environ.get(SHEET_PATH_ENV)
    if override:
        return _sheet_from_path(override)
    return _sheet_from_path(str(UNDECLARED_TEMPLATE_PATH))


def active_price(alloy: str, *, today: date | None = None) -> AlloyPrice:
    """Resolve an alloy price against the active sheet, or refuse when there is none."""
    sheet = active_sheet()
    if sheet is None:
        return AlloyPrice(
            alloy=str(alloy or "").strip().lower(),
            price_per_gram=None,
            currency=None,
            usable=False,
            reason="no_price_sheet",
            detail=(
                "No alloy price sheet is loaded, so the metal content is unpriced. "
                "No price was invented."
            ),
        )
    return sheet.price(alloy, today=today)


__all__ = [
    "COST_EXCLUDES",
    "COST_SCOPE_NOTE",
    "PRICE_SHEET_SCHEMA_PATH",
    "SHEET_PATH_ENV",
    "SHEET_SCHEMA",
    "UNDECLARED_TEMPLATE_PATH",
    "USABLE_PRICE_BASES",
    "AlloyPrice",
    "AlloyPriceError",
    "AlloyPriceSheet",
    "active_price",
    "active_sheet",
    "clear_active_sheet",
    "load_sheet",
    "set_active_sheet",
    "undeclared_template",
]
