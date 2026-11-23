"""Aureon guided funnel: style presets, step state -> canonical spec, plain-language checks.

The page keeps ONE client-side state object. Every key maps to exactly one place in
``canonical_spec_v0_1`` (``STATE_TO_SPEC`` below), and ``funnel_to_form`` +
``server.spec_from_form`` turn it into the spec the builder compiles:

    ring_type -> job.archetype (solitaire | halo); the gallery the style comes from
    style  -> setting.center_setting, setting.prong_count, ring.shank_style,
              setting.halo_style   (preset)
    shape  -> stones[0].shape
    length / width / depth  (or report_measurements) -> stones[0].dimensions_mm
    report_no -> stones[0].cert.report_number  (lab GIA; no live lookup)
    alloy  -> metal.type + metal.alloy
    size_standard / size -> ring.size_standard / ring.size
    band (slim | classic | bold, or advanced mm) -> ring.band_width_mm + band_thickness_mm
    melee  -> stones[role halo | accent].dimensions_mm   (Fine-tune; None = default)
    coverage -> ring.pave_coverage                         (Fine-tune, pave bands)

``funnel_issues`` turns schema / constraint / input problems into short, jargon-free
messages, each tied to the step that fixes it. Rule IDs stay available as ``rule``
for the page's "details" link. Nothing here certifies manufacturing: these are
design checks, and the five geometry gates stay ``pending``.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from jewelry_gaas_foundation.artisan_plan import (
    HALO_MELEE_DEFAULT_MM,
    HALO_SHAPES,
    HIDDEN_HALO_MELEE_DEFAULT_MM,
    PAVE_COVERAGE_END_DEG,
    PAVE_DEFAULT_COVERAGE,
    PAVE_DEFAULT_MELEE_MM,
    PAVE_WALL_MM,
    POINTED_SHAPES,
)
from jewelry_gaas_foundation.constraints import _derive_carat
from jewelry_gaas_foundation.constraints import validate as validate_constraints
from jewelry_gaas_foundation.validation import validate_job_spec

# Funnel steps, in order. "type" and "style" are galleries; "review" closes the questions.
STEPS = ("type", "style", "shape", "stone", "metal", "size", "band", "review")
STEP_TITLES = {
    "type": "Ring type",
    "style": "Style",
    "shape": "Stone shape",
    "stone": "Stone size",
    "metal": "Metal",
    "size": "Ring size",
    "band": "Band width",
    "review": "Review",
}


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    blurb: str
    head: str  # prong | basket | bezel
    prong_count: int | None  # None for a bezel (bezels take no prongs)
    shank: str  # plain | cathedral | pave
    ring_type: str = "solitaire"
    halo: str = "none"  # none | single | hidden
    # Shapes this style can't build honestly (live 2026-11-23): Artisan's peghead puts
    # four CORNER prongs on cornered stones even when asked for six; its bezel rejects
    # cushions at every height, and on a pear it unions into a solid that isn't closed.
    exclude_shapes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CORNERED = ("cushion", "princess", "emerald", "radiant")
NO_BEZEL = ("cushion", "pear")


@dataclass(frozen=True)
class RingType:
    """A ring-type card: the first question. Its styles are the second."""

    id: str
    name: str
    blurb: str
    shapes: tuple[str, ...]
    fine_tune: tuple[str, ...] = ()  # melee | coverage

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Six starting styles, all buildable today. Generic names only (no trademarks).
STYLES: tuple[Style, ...] = (
    Style("classic-four-prong", "Classic Four-Prong",
          "Four slim prongs lift the stone and let in the most light.", "prong", 4, "plain"),
    Style("six-prong", "Six-Prong Solitaire",
          "Six prongs for extra security and a rounder outline.", "prong", 6, "plain",
          exclude_shapes=CORNERED),
    Style("cathedral", "Cathedral",
          "Prongs rise from graceful arches in the band.", "prong", 4, "cathedral"),
    Style("secure-basket", "Secure Basket",
          "A rail joins the prongs around the stone: sturdy and snag-resistant.",
          "basket", 4, "plain"),
    Style("modern-bezel", "Modern Bezel",
          "A smooth rim of metal wraps the stone. Sleek and protective.", "bezel", None, "plain",
          exclude_shapes=NO_BEZEL),
    Style("bezel-cathedral", "Bezel Cathedral",
          "A rim-set stone lifted on cathedral arches.", "bezel", None, "cathedral",
          exclude_shapes=NO_BEZEL),
    # Hidden halo: Artisan's hidden halo carries its own four-prong head.
    Style("hidden-halo", "Hidden Halo",
          "A secret ring of diamonds under the stone, seen from the side.",
          "prong", 4, "plain", "hidden-halo", "hidden"),
    Style("hidden-halo-pave", "Hidden Halo + Pavé",
          "The hidden halo, with diamonds along the band's shoulders.",
          "prong", 4, "pave", "hidden-halo", "hidden"),
    Style("pave-four-prong", "Four-Prong Pavé",
          "Four prongs over a band set with small diamonds.", "prong", 4, "pave", "pave"),
    Style("pave-six-prong", "Six-Prong Pavé",
          "Six prongs over a band set with small diamonds.", "prong", 6, "pave", "pave",
          exclude_shapes=CORNERED),
    Style("pave-bezel", "Bezel Pavé",
          "A smooth bezel rim on a band set with small diamonds.", "bezel", None, "pave", "pave",
          exclude_shapes=NO_BEZEL),
    # Halo: the halo brings its own center prongs (4; 5 on a pear).
    Style("classic-halo", "Classic Halo",
          "A frame of small diamonds makes the stone look larger.",
          "prong", 4, "plain", "halo", "single"),
    Style("halo-pave", "Halo + Pavé",
          "The halo, with diamonds along the band's shoulders.",
          "prong", 4, "pave", "halo", "single"),
)
STYLE_BY_ID = {s.id: s for s in STYLES}

ALL_SHAPES = ("round", "oval", "cushion", "princess", "emerald", "radiant", "pear", "marquise")
# Hidden halo: Artisan's hidden-halo head has four round prongs and no V-tip, so the
# pointed shapes (pear, marquise) stay out (PRONG-003). Princess is out of the halo:
# Artisan's halo kernel doesn't support it.
HIDDEN_HALO_FUNNEL_SHAPES = ("round", "oval", "cushion", "princess", "emerald", "radiant")
RING_TYPES: tuple[RingType, ...] = (
    RingType("solitaire", "Solitaire", "One stone, nothing to distract from it.", ALL_SHAPES),
    RingType("hidden-halo", "Hidden Halo", "A surprise of diamonds under the stone.",
             HIDDEN_HALO_FUNNEL_SHAPES, ("melee",)),
    RingType("pave", "Pavé Band", "Small diamonds along the band's shoulders.",
             ALL_SHAPES, ("melee", "coverage")),
    RingType("halo", "Halo", "A frame of diamonds around the stone.",
             tuple(s for s in ALL_SHAPES if s in HALO_SHAPES), ("melee",)),
)
RING_TYPE_BY_ID = {t.id: t for t in RING_TYPES}

# Fine-tune: melee (small stone) sizes offered, and the pave coverage presets.
MELEE_CHOICES = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6)
COVERAGES: dict[str, dict[str, str]] = {
    "shoulders": {"label": "Shoulders", "blurb": "Diamonds on the top of the band only."},
    "half": {"label": "Half band", "blurb": "Diamonds about halfway down each side."},
    "three_quarter": {"label": "Three-quarter", "blurb": "Diamonds most of the way round."},
}
assert set(COVERAGES) == set(PAVE_COVERAGE_END_DEG)

# Shape vocabulary: label, typical length:width ratio and depth as a share of width,
# used only by the "typical size for N carats" helper. The spec always carries the
# typed millimetres; carat is a helper, never an input to geometry.
SHAPES: dict[str, dict[str, Any]] = {
    "round":    {"label": "Round",    "ratio": 1.00, "depth_pct": 0.61},
    "oval":     {"label": "Oval",     "ratio": 1.40, "depth_pct": 0.60},
    "cushion":  {"label": "Cushion",  "ratio": 1.00, "depth_pct": 0.66},
    "princess": {"label": "Princess", "ratio": 1.00, "depth_pct": 0.70},
    "emerald":  {"label": "Emerald",  "ratio": 1.40, "depth_pct": 0.65},
    "radiant":  {"label": "Radiant",  "ratio": 1.20, "depth_pct": 0.65},
    "pear":     {"label": "Pear",     "ratio": 1.60, "depth_pct": 0.60},
    "marquise": {"label": "Marquise", "ratio": 2.00, "depth_pct": 0.60},
}
CARAT_CHIPS = (0.5, 0.75, 1.0, 1.5, 2.0)

# Pointed stones always get V-tip prongs at their points, on six prongs: the count
# verified live for pear and marquise (docs/artisan_mvp.md §3).
POINTED_PRONGS = 6

ALLOYS: dict[str, dict[str, str]] = {
    "14k yellow gold": {"label": "14k Yellow Gold", "swatch": "#D9B45A"},
    "18k yellow gold": {"label": "18k Yellow Gold", "swatch": "#CFA43C"},
    "14k white gold":  {"label": "14k White Gold",  "swatch": "#E4E2DC"},
    "18k white gold":  {"label": "18k White Gold",  "swatch": "#DCDAD4"},
    "14k rose gold":   {"label": "14k Rose Gold",   "swatch": "#E2A98F"},
    "18k rose gold":   {"label": "18k Rose Gold",   "swatch": "#D99A80"},
    "platinum 950":    {"label": "Platinum",        "swatch": "#CFD2D4"},
}

# Band presets -> (width, thickness) mm. A cathedral band needs >= 2.0 mm of width
# (SETTING-003), so "slim" on a cathedral is 2.0 mm.
BANDS: dict[str, dict[str, Any]] = {
    "slim":    {"label": "Slim",    "blurb": "Delicate. Lets the stone lead.",
                "width": 1.8, "thickness": 1.5},
    "classic": {"label": "Classic", "blurb": "The everyday balance.",
                "width": 2.0, "thickness": 1.6},
    "bold":    {"label": "Bold",    "blurb": "Substantial and durable.",
                "width": 2.6, "thickness": 1.9},
}
CATHEDRAL_MIN_WIDTH = 2.0

# Input guards (plausibility, not manufacturing rules): catch typos like 39 for 3.9.
STONE_MM_RANGE = (2.0, 20.0)
DEPTH_MM_RANGE = (1.0, 14.0)
US_SIZE_RANGE = (3.0, 13.0)
EU_SIZE_RANGE = (44.0, 70.0)

DEFAULT_STATE: dict[str, Any] = {
    "ring_type": "solitaire",
    "style": "classic-four-prong",
    "shape": "round",
    "size_mode": "mm",  # mm | report  (UI only: where the millimetres come from)
    "length": 6.45,
    "width": 6.45,
    "depth": 3.94,
    "report_no": "",
    "report_measurements": "",
    "alloy": "14k yellow gold",
    "size_standard": "US",
    "size": 6.5,
    "band": "classic",
    "band_width": None,  # advanced override (mm); None = use the preset
    "band_thickness": None,
    "melee": None,  # Fine-tune: small-stone size (mm); None = the type's default
    "coverage": PAVE_DEFAULT_COVERAGE,
}

# The 1:1 mapping, documented and tested: state key -> canonical spec path(s).
STATE_TO_SPEC: dict[str, tuple[str, ...]] = {
    "ring_type": ("job.archetype",),
    "style": ("setting.center_setting", "setting.prong_count", "ring.shank_style",
              "setting.halo_style"),
    "shape": ("stones[0].shape",),
    # A round stone has one diameter: its width follows its length.
    "length": ("stones[0].dimensions_mm.length", "stones[0].dimensions_mm.width"),
    "width": ("stones[0].dimensions_mm.width",),
    "depth": ("stones[0].dimensions_mm.depth",),
    "report_measurements": ("stones[0].dimensions_mm",),
    "report_no": ("stones[0].cert.report_number",),
    "alloy": ("metal.type", "metal.alloy"),
    "size_standard": ("ring.size_standard",),
    "size": ("ring.size",),
    "band": ("ring.band_width_mm", "ring.band_thickness_mm"),
    "band_width": ("ring.band_width_mm",),
    "band_thickness": ("ring.band_thickness_mm",),
    "melee": ("stones[halo].dimensions_mm", "stones[accent].dimensions_mm"),
    "coverage": ("ring.pave_coverage",),
}


class FunnelError(ValueError):
    """The funnel state is malformed (unknown style / shape / metal / band)."""


# ---------------------------------------------------------------------------
# Stone-size helpers
# ---------------------------------------------------------------------------

def typical_dimensions(shape: str, carat: float) -> dict[str, float]:
    """Typical L x W x D (mm) of a well-cut stone of ``carat``. An estimate only."""
    info = SHAPES[shape]
    ratio, depth_pct = info["ratio"], info["depth_pct"]
    # Invert carat = L * W * D * factor with L = ratio * W and D = depth_pct * W,
    # using the same shape-specific factors as the constraint engine.
    probe = {"shape": shape, "dimensions_mm": {"length": ratio, "width": 1.0, "depth": depth_pct}}
    unit = _derive_carat(probe) or 0.0
    width = (carat / unit) ** (1.0 / 3.0)
    return {
        "length": round(ratio * width, 2),
        "width": round(width, 2),
        "depth": round(depth_pct * width, 2),
    }


_NUM = r"(\d+(?:[.,]\d+)?)"
_SEP = r"\s*[x×X*]\s*"
_ROUND_RE = re.compile(_NUM + r"\s*[-–]\s*" + _NUM + _SEP + _NUM)
_FANCY_RE = re.compile(_NUM + _SEP + _NUM + _SEP + _NUM)


def parse_report_measurements(text: str, shape: str) -> dict[str, float] | None:
    """Read a GIA-style Measurements line into L x W x D mm.

    Rounds print ``min - max x depth`` (``6.52 - 6.55 x 3.98 mm``); the larger
    diameter is used so the seat always fits the stone. Fancy shapes print
    ``length x width x depth`` (``8.02 x 5.51 x 3.45 mm``).
    """
    if not text:
        return None
    cleaned = text.replace(",", ".")
    match = _ROUND_RE.search(cleaned)
    if match:
        low, high, depth = (float(g) for g in match.groups())
        diameter = max(low, high)
        if shape == "round":
            return {"length": diameter, "width": diameter, "depth": depth}
        return {"length": diameter, "width": min(low, high), "depth": depth}
    match = _FANCY_RE.search(cleaned)
    if match:
        length, width, depth = (float(g) for g in match.groups())
        if shape == "round":
            diameter = max(length, width)
            return {"length": diameter, "width": diameter, "depth": depth}
        return {"length": length, "width": width, "depth": depth}
    return None


def us_size_from_diameter(diameter_mm: float) -> float:
    """Nearest US quarter size for an inner diameter (inverse of 11.63 + 0.8128 x size)."""
    return round(((diameter_mm - 11.63) / 0.8128) * 4) / 4


# ---------------------------------------------------------------------------
# State -> form (-> spec via server.spec_from_form)
# ---------------------------------------------------------------------------

def resolve_style(state: dict[str, Any]) -> Style:
    """The style decides the ring; ``ring_type`` (when sent) must agree with it."""
    style = STYLE_BY_ID.get(str(state.get("style") or ""))
    if style is None:
        raise FunnelError(f"unknown style {state.get('style')!r}")
    ring_type = state.get("ring_type")
    if ring_type not in (None, "") and ring_type != style.ring_type:
        raise FunnelError(f"style {style.id!r} is not a {ring_type!r} style")
    return style


def resolve_ring_type(style: Style) -> RingType:
    return RING_TYPE_BY_ID[style.ring_type]


def effective_prongs(style: Style, shape: str) -> int | None:
    """Prong count the build uses. Bezels take none; pointed stones take six, V-tipped.

    A halo brings its own center prongs (5 on a pear, else 4); the hidden halo has 4.
    """
    if style.head == "bezel":
        return None
    if style.halo == "single":
        return 5 if shape == "pear" else 4
    if style.halo == "hidden":
        return 4
    if shape in POINTED_SHAPES:
        return POINTED_PRONGS
    return style.prong_count


def pave_melee_cap(band_width: float) -> float:
    """Largest melee a band of ``band_width`` holds with a wall each side (0.1 mm steps)."""
    return math.floor((band_width - 2 * PAVE_WALL_MM) * 10 + 1e-9) / 10


def melee_size(state: dict[str, Any], style: Style, band_width: float) -> float | None:
    """Small-stone size (mm) for the style: the Fine-tune pick, else the type's default.

    Halo and hidden halo use the pick for their melee; a pave band's melee follows it
    too when there's no halo, capped by the band's width by default.
    """
    picked = _number_or_none(state.get("melee"))
    if style.halo == "single":
        return picked if picked is not None else HALO_MELEE_DEFAULT_MM
    if style.halo == "hidden":
        return picked if picked is not None else HIDDEN_HALO_MELEE_DEFAULT_MM
    if style.shank == "pave":
        if picked is not None:
            return picked
        return min(PAVE_DEFAULT_MELEE_MM, pave_melee_cap(band_width))
    return None


def pave_melee(state: dict[str, Any], style: Style, band_width: float) -> float | None:
    """Melee on the pave band itself (with a halo, the band keeps its default)."""
    if style.shank != "pave":
        return None
    if style.halo == "none":
        return melee_size(state, style, band_width)
    return min(PAVE_DEFAULT_MELEE_MM, pave_melee_cap(band_width))


def band_dimensions(state: dict[str, Any], shank: str) -> tuple[float, float]:
    band = str(state.get("band") or "classic")
    if band not in BANDS:
        raise FunnelError(f"unknown band {band!r}")
    width = float(BANDS[band]["width"])
    thickness = float(BANDS[band]["thickness"])
    if shank == "cathedral":
        width = max(width, CATHEDRAL_MIN_WIDTH)
    override_w = _number_or_none(state.get("band_width"))
    override_t = _number_or_none(state.get("band_thickness"))
    return (override_w if override_w is not None else width,
            override_t if override_t is not None else thickness)


def stone_dimensions(state: dict[str, Any]) -> dict[str, Any]:
    """The stone's mm from the active source: typed mm, or the pasted report line."""
    shape = str(state.get("shape") or "")
    if state.get("size_mode") == "report":
        parsed = parse_report_measurements(str(state.get("report_measurements") or ""), shape)
        if parsed is not None:
            return parsed
    dims = {k: state.get(k) for k in ("length", "width", "depth")}
    if shape == "round" and dims.get("length") not in (None, ""):
        dims["width"] = dims["length"]
    return dims


def funnel_to_form(state: dict[str, Any]) -> dict[str, Any]:
    """Map the funnel state onto the builder's flat form (``server.spec_from_form``)."""
    style = resolve_style(state)
    shape = str(state.get("shape") or "")
    if shape not in SHAPES:
        raise FunnelError(f"unknown shape {shape!r}")
    alloy = str(state.get("alloy") or "")
    if alloy not in ALLOYS:
        raise FunnelError(f"unknown metal {alloy!r}")
    width, thickness = band_dimensions(state, style.shank)
    dims = stone_dimensions(state)
    coverage = str(state.get("coverage") or PAVE_DEFAULT_COVERAGE)
    if style.shank == "pave" and coverage not in COVERAGES:
        raise FunnelError(f"unknown coverage {coverage!r}")
    return {
        "shape": shape,
        "length": dims.get("length"),
        "width": dims.get("width"),
        "depth": dims.get("depth"),
        "cert": str(state.get("report_no") or "").strip(),
        "head": style.head,
        "prong_count": effective_prongs(style, shape),
        "shank": style.shank,
        "size_standard": str(state.get("size_standard") or "US"),
        "size": state.get("size"),
        "band_width": width,
        "band_thickness": thickness,
        "alloy": alloy,
        "halo": style.halo,
        "halo_melee": melee_size(state, style, width) if style.halo != "none" else None,
        "pave_melee": pave_melee(state, style, width),
        "pave_coverage": coverage if style.shank == "pave" else None,
    }


def is_funnel_state(body: dict[str, Any]) -> bool:
    return "style" in body


# ---------------------------------------------------------------------------
# Plain-language checks
# ---------------------------------------------------------------------------

LEVELS = ("block", "warn", "tip", "note", "info")


def _issue(step: str, level: str, text: str, fix: str = "", rule: str = "") -> dict[str, str]:
    """level: block (fix before building) | warn (worth a look) | tip | note | info."""
    return {"step": step, "level": level, "text": text, "fix": fix, "rule": rule}


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def input_issues(state: dict[str, Any]) -> list[dict[str, str]]:
    """Malformed or implausible input, caught before any rule runs."""
    issues: list[dict[str, str]] = []
    shape = str(state.get("shape") or "")
    report_text = str(state.get("report_measurements") or "").strip()
    if state.get("size_mode") == "report":
        if not report_text:
            issues.append(_issue(
                "stone", "block", "Paste the Measurements line from the stone's report.",
                "Or switch to typing the millimetres.",
            ))
            return issues
        if parse_report_measurements(report_text, shape) is None:
            issues.append(_issue(
                "stone", "block", "We couldn't read those measurements.",
                "Copy the Measurements line from the report, for example 6.52 - 6.55 x 3.98 mm.",
            ))
            return issues

    dims = stone_dimensions(state)
    for key in ("length", "width", "depth"):
        value = _number_or_none(dims.get(key))
        low, high = DEPTH_MM_RANGE if key == "depth" else STONE_MM_RANGE
        if value is None:
            issues.append(_issue("stone", "block", f"Enter the stone's {key} in millimetres.",
                                 "Use the numbers printed on the stone's report."))
        elif not low <= value <= high:
            issues.append(_issue(
                "stone", "block",
                f"A {key} of {value:g} mm is outside what we build ({low:g}–{high:g} mm).",
                "Check the decimal point against the stone's report.",
            ))
    width, depth = _number_or_none(dims.get("width")), _number_or_none(dims.get("depth"))
    if width and depth and not issues and not 0.4 <= depth / width <= 0.95:
        issues.append(_issue(
            "stone", "warn", f"A depth of {depth:g} mm is unusual for a {width:g} mm wide stone.",
            "Double-check the depth on the report: the setting is built to it.",
        ))

    standard = str(state.get("size_standard") or "US")
    size = _number_or_none(state.get("size"))
    if standard not in ("US", "EU"):
        issues.append(_issue("size", "block", "Choose a US or EU ring size."))
    elif size is None:
        issues.append(_issue("size", "block", "Choose a ring size.",
                             "Not sure? US 6.5 is the most common, and a jeweler can resize it."))
    else:
        low, high = US_SIZE_RANGE if standard == "US" else EU_SIZE_RANGE
        if not low <= size <= high:
            issues.append(_issue(
                "size", "block", f"{standard} size {size:g} is outside the range we build "
                f"({low:g}–{high:g}).", "Pick a size in range, or have the finger measured.",
            ))

    for key in ("band_width", "band_thickness"):
        if state.get(key) not in (None, "") and _number_or_none(state.get(key)) is None:
            issues.append(_issue("band", "block", "Band millimetres must be numbers.",
                                 "Clear the advanced fields to use Slim, Classic or Bold."))

    style = STYLE_BY_ID.get(str(state.get("style") or ""))
    if style is None:
        return issues
    ring_type = resolve_ring_type(style)
    label = SHAPES.get(shape, {}).get("label", shape)
    if shape and shape not in ring_type.shapes:
        issues.append(_issue(
            "shape", "block", f"The {ring_type.name} isn't available for {label} stones.",
            "Choose another shape, or go back and pick another ring type.",
        ))
    elif shape in style.exclude_shapes:
        why = ("cornered stones are held by four corner prongs" if style.prong_count == 6
               else "the rim can't follow its outline yet")
        issues.append(_issue(
            "shape", "block", f"The {style.name} isn't available for {label} stones: {why}.",
            "Choose another shape, or go back and pick another style.",
        ))
    if state.get("melee") not in (None, ""):
        picked = _number_or_none(state.get("melee"))
        if picked is None or not MELEE_CHOICES[0] <= picked <= MELEE_CHOICES[-1]:
            issues.append(_issue(
                "style", "block", "Choose a small-diamond size between "
                f"{MELEE_CHOICES[0]:g} and {MELEE_CHOICES[-1]:g} mm.", "Or reset it to the default.",
            ))
    if style.shank == "pave" and not any(i["step"] == "band" for i in issues):
        try:
            width, _ = band_dimensions(state, style.shank)
        except FunnelError:
            return issues
        melee = pave_melee(state, style, width)
        cap = pave_melee_cap(width)
        if melee is not None and melee > cap + 1e-9:
            issues.append(_issue(
                "band", "block", f"{melee:g} mm diamonds need a band at least "
                f"{melee + 2 * PAVE_WALL_MM:.1f} mm wide.",
                f"Choose a wider band, or diamonds of {cap:g} mm or less.",
            ))
    return issues


def explain_violation(v: Any, state: dict[str, Any]) -> dict[str, str] | None:
    """One constraint violation -> a plain message on the step that fixes it (None = hide)."""
    rule = v.rule_id
    size = f"{state.get('size_standard', 'US')} {state.get('size')}"
    band = BANDS.get(str(state.get("band")), {}).get("label", "this")
    if rule in ("BAND-001", "BAND-002"):
        return _issue("band", "block", "This band is too thin to wear every day.",
                      "Choose Classic or Bold.", rule)
    if rule == "SETTING-003":
        return _issue("band", "block", "A cathedral band needs a little more width.",
                      "Choose Classic or Bold.", rule)
    if rule == "BAND-005":
        return _issue("size", "block",
                      f"This stone is too wide for a size {size} ring with a {band} band.",
                      "Choose a larger ring size or a slimmer band.", rule)
    if rule == "BAND-006":
        return _issue("band", "warn", "The band may look thin next to this stone.",
                      "Bold balances a larger stone.", rule)
    if rule == "PRONG-007":
        return _issue("stone", "block", "This stone is too small to hold with prongs.",
                      "Choose a bezel style, or check the measurements.", rule)
    if rule == "PRONG-008":
        return _issue("style", "tip", "For a stone this size, six prongs add security.",
                      "The Six-Prong style is a good fit.", rule)
    if rule == "PRONG-004":
        return _issue("shape", "note",
                      "Princess corners are delicate: ask your setter to check the corner prongs.",
                      "", rule)
    if rule == "PRONG-003" and v.tier == "ADVISORY":
        return None  # the builder adds V-tips; replaced by the info note on the shape step
    if rule in ("METAL-001", "METAL-002"):
        return _issue("review", "note",
                      "For a large stone on a cathedral, your caster should confirm prong thickness.",
                      "", rule)
    if rule == "METAL-003":
        return None  # density note, no action needed
    if rule == "STONE-001":
        return _issue("style", "warn", "The halo diamonds are large next to this stone.",
                      "A smaller diamond size in Fine-tune keeps the halo delicate.", rule)
    if rule == "STONE-005":
        return _issue("style", "block", "A halo can't go around a bezel.",
                      "Pick a prong style.", rule)
    level = {"HARD": "block", "SOFT": "warn"}.get(v.tier, "tip")
    return _issue("review", level, v.message, v.suggested_fix, rule)


def funnel_issues(state: dict[str, Any], spec: dict[str, Any] | None) -> list[dict[str, str]]:
    """Every issue for the state, in plain language and tagged with the step that fixes it."""
    issues = input_issues(state)
    shape = str(state.get("shape") or "")
    style = resolve_style(state)
    if shape in POINTED_SHAPES and style.head != "bezel":
        points = "point" if shape == "pear" else "points"
        if style.halo == "single":
            issues.append(_issue("shape", "info", f"The halo's frame wraps the {points}."))
        else:
            issues.append(_issue("shape", "info", f"V-tip prongs are added to protect the {points}."))
    if spec is None or any(i["level"] == "block" for i in issues):
        return issues
    schema = validate_job_spec(spec)
    for item in schema.issues:
        if item.severity == "error":
            issues.append(_issue(
                "review", "block", "Something in this ring's details is incomplete.",
                "Go back through the steps; the details link names the field.",
                f"{item.code} {item.path}: {item.message}",
            ))
    constraints = validate_constraints(spec)
    for v in [*constraints.hard_violations, *constraints.soft_warnings, *constraints.advisory_notes]:
        explained = explain_violation(v, state)
        if explained is not None:
            issues.append(explained)
    return issues


def carat_estimate(state: dict[str, Any]) -> float | None:
    dims = stone_dimensions(state)
    try:
        carat = _derive_carat({"shape": state.get("shape"), "dimensions_mm": dims})
    except (TypeError, ValueError):
        return None
    return round(carat, 2) if carat else None


def catalog(static_url: str = "/static/styles", available: set[str] | None = None) -> dict[str, Any]:
    """Everything the page needs to draw the funnel (``GET /api/styles``)."""
    available = available or set()

    def asset(style_id: str, *exts: str) -> str | None:
        for ext in exts:
            if f"{style_id}.{ext}" in available:
                return f"{static_url}/{style_id}.{ext}"
        return None

    styles = [
        {**s.to_dict(), "thumbnail": asset(s.id, "jpg", "webp", "png"), "glb": asset(s.id, "glb")}
        for s in STYLES
    ]
    first_style = {}
    for s in styles:
        first_style.setdefault(s["ring_type"], s)
    return {
        "ring_types": [
            {**t.to_dict(), "styles": [s.id for s in STYLES if s.ring_type == t.id],
             "thumbnail": first_style[t.id]["thumbnail"]}
            for t in RING_TYPES
        ],
        "styles": styles,
        "shapes": {
            key: {
                "label": info["label"],
                "pointed": key in POINTED_SHAPES,
                "typical": {f"{c:g}": typical_dimensions(key, c) for c in CARAT_CHIPS},
            }
            for key, info in SHAPES.items()
        },
        "carat_chips": list(CARAT_CHIPS),
        "alloys": [{"id": key, **info} for key, info in ALLOYS.items()],
        "bands": [{"id": key, **info} for key, info in BANDS.items()],
        "melee": {
            "choices": list(MELEE_CHOICES),
            "defaults": {"single": HALO_MELEE_DEFAULT_MM, "hidden": HIDDEN_HALO_MELEE_DEFAULT_MM,
                         "pave": PAVE_DEFAULT_MELEE_MM},
            "wall_mm": PAVE_WALL_MM,
        },
        "coverages": [{"id": key, **info} for key, info in COVERAGES.items()],
        "steps": [{"id": s, "title": STEP_TITLES[s]} for s in STEPS],
        "defaults": DEFAULT_STATE,
    }
