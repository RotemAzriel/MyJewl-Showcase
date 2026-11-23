#!/usr/bin/env python3
"""Tier-1 catalog config generator (R-1 allocation).

Turns the demand-weighted *floor + demand* allocation (priors below, decided in
``reaserch/planning/tier1_allocation.md``) into constraint-clean flat
``parameters.json`` configs (``data_pipeline_architecture.md`` §4.2).

Design
------
* **Headline grid:** ``shape × center_setting × prong_count × band_width bucket``.
  Only constraint-valid cells are enumerated (bezel → 0 prongs; round prong ∈
  {3,4,6}; band ≥ 1.8 mm; etc.).
* **Within-cell distinctness:** the multiple configs in one cell are made distinct
  by a **≥0.1 mm band-width step** (never finer), ``band_thickness ∈ {1.6, 1.8}``,
  and a **light** stone-size tier spread ``{1.0, 1.5, 0.5, 2.0}`` (1.0 ct first, so
  most configs sit at the modal size and the budget stays on mounting). Stone size
  is a typed input — catalog tiers are only retrieval anchors; broad scale
  robustness comes from render-time augmentation, not the geometry budget.
* **Per-cell distinct-capacity cap:** a cell can hold at most
  ``band_steps × |thickness| × |size_tiers|`` distinct geometries (standard band =
  6×2×4 = 48). Demand allocation is capped at each cell's capacity; overflow is
  redistributed to the next-densest valid cells — never to sub-0.1 mm micro-steps.
* **Pre-validation:** every candidate combo is run through
  ``config_adapter.flat_to_canonical`` + ``constraints.validate``. Only *accepted*
  combos count toward a cell's capacity. Contract-rule skips are logged; empirical
  manufacturing baselines do not certify or reject candidates.

This module decides *which configs to generate* only. It does **not** run geometry,
Rhino, or Blender; ``geometry_gates`` stay ``"pending"`` (manufacturing-certainty
rule). The live M4 batch is a separate step (``scripts/rhino_batch.py``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

# Make the package importable when run as ``python3 scripts/generate_tier1_configs.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jewelry_gaas_foundation import constraints  # noqa: E402
from jewelry_gaas_foundation.config_adapter import flat_to_canonical  # noqa: E402

# ---------------------------------------------------------------------------
# Merged demand priors (R-1 synthesis of claude / chatgpt / gemini studies).
# See reaserch/planning/tier1_allocation.md for derivation + reconciliation.
# All within-axis splits are LOW-confidence bootstrap priors to be overridden by
# the G-5 200–500 real photos.
# ---------------------------------------------------------------------------

SHAPES = ("round", "oval", "cushion", "pear", "emerald", "princess", "radiant")

# Outer: stone-shape demand budget (sums to 1,000). Round 62% is the sales/estate
# prior (hot spot 1), NOT the 28% survey figure.
SHAPE_BUDGET = {
    "round": 620, "oval": 140, "cushion": 60, "pear": 50,
    "emerald": 50, "princess": 40, "radiant": 40,
}

# Center-setting split per shape (basket discounted per operator decision: Gemini's
# 45–60% conflates basket-as-understructure with basket-as-style; merge ChatGPT ~8%
# + Claude ~28% → ~15–18% with a deep-pavilion lift for cushion/radiant).
SETTING_SPLIT = {
    "round":    {"prong": 0.75, "bezel": 0.10, "basket": 0.15},
    "oval":     {"prong": 0.75, "bezel": 0.10, "basket": 0.15},
    "cushion":  {"prong": 0.74, "bezel": 0.08, "basket": 0.18},
    "pear":     {"prong": 0.76, "bezel": 0.12, "basket": 0.12},
    "emerald":  {"prong": 0.73, "bezel": 0.15, "basket": 0.12},
    "princess": {"prong": 0.82, "bezel": 0.06, "basket": 0.12},
    "radiant":  {"prong": 0.74, "bezel": 0.08, "basket": 0.18},
}

# Prong-count split within prong/basket settings (hot spot 2: round ~46/50 for 4/6
# with a small 3-prong niche; floor covers 4 AND 6 everywhere). Baskets are
# prong-bearing, so they reuse the prong-count split.
PRONG_SPLIT = {
    "round":    {3: 0.04, 4: 0.46, 6: 0.50},
    "oval":     {4: 0.60, 6: 0.40},
    "cushion":  {4: 0.75, 6: 0.25},
    "pear":     {5: 0.60, 6: 0.40},
    "emerald":  {4: 0.85, 6: 0.15},
    "princess": {4: 0.88, 6: 0.12},
    "radiant":  {4: 0.80, 6: 0.20},
}

# Product-catalog prong baselines (round 3/4/6; pear 5/6; others 4/6).
# These are allocation choices, not universal manufacturing certification.
VALID_PRONGS = {
    "round": (3, 4, 6), "oval": (4, 6), "cushion": (4, 6), "pear": (5, 6),
    "emerald": (4, 6), "princess": (4, 6), "radiant": (4, 6),
}

# Band-width bucket split per shape (emerald/princess skew wide per estate skew).
BAND_SPLIT = {
    "round":    {"narrow": 0.25, "standard": 0.60, "wide": 0.15},
    "oval":     {"narrow": 0.25, "standard": 0.60, "wide": 0.15},
    "cushion":  {"narrow": 0.25, "standard": 0.60, "wide": 0.15},
    "pear":     {"narrow": 0.25, "standard": 0.60, "wide": 0.15},
    "emerald":  {"narrow": 0.20, "standard": 0.55, "wide": 0.25},
    "princess": {"narrow": 0.20, "standard": 0.55, "wide": 0.25},
    "radiant":  {"narrow": 0.25, "standard": 0.60, "wide": 0.15},
}

FLOOR = 2  # min configs per valid cell (retrieval discriminability; 1 is brittle).

# Within-cell fine grid. Band steps are ≥0.1 mm with half-open bucket edges so a
# given width belongs to exactly one bucket. The 1.8 mm floor is a catalog choice.
BAND_STEPS = {
    "narrow":   (1.8, 1.9),                          # [1.8, 2.0)  → cap factor 2
    "standard": (2.0, 2.1, 2.2, 2.3, 2.4, 2.5),      # [2.0, 2.5]  → cap factor 6
    "wide":     (2.6, 2.7, 2.8, 2.9, 3.0),           # (2.5, 3.0]  → cap factor 5
}
BAND_THICKNESS = (1.6, 1.8)
# Size tiers in fill priority order: 1.0 ct first keeps size light; only saturated
# (near-capacity) cells reach the thin 0.5/1.5/2.0 ct tiers.
SIZE_TIERS_PRIORITY = (1.0, 1.5, 0.5, 2.0)

# Shape geometry: representative L:W and D:W ratios (the exact mm is typed by the
# user at verify-time; these only anchor the catalog). Carat factors come from
# constraints.SHAPE_FACTORS so _derive_carat round-trips cleanly.
SHAPE_LW = {
    "round": 1.00, "oval": 1.40, "cushion": 1.05, "pear": 1.55,
    "emerald": 1.40, "princess": 1.00, "radiant": 1.15,
}
SHAPE_DW = {
    "round": 0.60, "oval": 0.62, "cushion": 0.68, "pear": 0.61,
    "emerald": 0.66, "princess": 0.72, "radiant": 0.68,
}

# Fixed representative values (NOT catalog axes). Metal is a cost overlay; ring size
# and stone size are typed inputs. US 6.5 is the modal size.
ARCHETYPE = "solitaire"
RING_SIZE_US = 6.5
METAL_TYPE = "gold"
METAL_ALLOY = "14k_yellow"
METAL_CODE = "14ky"
METAL_DENSITY_GCM3 = 12.9
STONE_COLOR = "diamond_colorless"
BLENDER_SAMPLE_COUNT = 128
RHINO_COMPUTE_VERSION = "8.0.0.0"
GENERATION_TIMESTAMP = "2026-06-15T00:00:00Z"
GEOMETRY_GATES_PENDING = {
    "minimum_structural_floors": "pending",
    "stone_metal_collision": "pending",
    "stone_stone_collision": "pending",
    "mountability_and_setting_access": "pending",
    "mesh_closedness_and_manifold_suitability": "pending",
}

SETTING_ORDER = ("prong", "bezel", "basket")
BUCKET_ORDER = ("narrow", "standard", "wide")


class Cell(NamedTuple):
    """A headline allocation cell. ``prong`` is 0 for bezel."""
    shape: str
    setting: str
    prong: int
    bucket: str


# ---------------------------------------------------------------------------
# Geometry + config building
# ---------------------------------------------------------------------------

def representative_dimensions(shape: str, carat: float) -> tuple[float, float, float]:
    """Return representative (length, width, depth) mm for a shape at a carat tier.

    Inverts the shape-specific GIA estimate used by ``constraints._derive_carat``
    so the emitted ``stone_carat_eq`` round-trips through validation.
    """
    r = SHAPE_LW[shape]
    q = SHAPE_DW[shape]
    factor = constraints._shape_factor(shape, r, 1.0)
    planar_ratio = ((r + 1.0) / 2.0) ** 2 if shape in {"round", "oval"} else r
    width = (carat / (planar_ratio * q * factor)) ** (1.0 / 3.0)
    return round(r * width, 2), round(width, 2), round(q * width, 2)


def _setting_token(setting: str, prong: int) -> str:
    return "bezel" if setting == "bezel" else f"{setting}{prong}"


def build_flat_config(
    shape: str, setting: str, prong: int,
    carat: float, length: float, width: float, depth: float,
    band_width: float, band_thickness: float,
) -> dict:
    """Build a full §4.2 flat parameters.json dict for one config."""
    token = _setting_token(setting, prong)
    config_id = (
        f"config_{ARCHETYPE}_{shape}_{carat:.2f}ct_{token}_{METAL_CODE}_"
        f"{band_width:.1f}mm_t{band_thickness:.1f}_us{int(RING_SIZE_US * 10)}"
    )
    inner_dia = round(11.63 + 0.8128 * RING_SIZE_US, 2)
    return {
        "config_id": config_id,
        "archetype": ARCHETYPE,
        "stone_shape": shape,
        "stone_length_mm": length,
        "stone_width_mm": width,
        "stone_depth_mm": depth,
        "stone_carat_eq": carat,
        "stone_color": STONE_COLOR,
        "center_setting": setting,
        "prong_count": prong,
        "metal_type": METAL_TYPE,
        "metal_alloy": METAL_ALLOY,
        "metal_density_gcm3": METAL_DENSITY_GCM3,
        "band_width_mm": band_width,
        "band_thickness_mm": band_thickness,
        "ring_size_us": RING_SIZE_US,
        "ring_inner_diameter_mm": inner_dia,
        "geometry_gates": dict(GEOMETRY_GATES_PENDING),
        "generation_timestamp": GENERATION_TIMESTAMP,
        "blender_sample_count": BLENDER_SAMPLE_COUNT,
        "rhino_compute_version": RHINO_COMPUTE_VERSION,
    }


def candidate_combos(cell: Cell) -> list[dict]:
    """Ordered candidate flat configs for a cell (size-light first)."""
    combos: list[dict] = []
    for carat in SIZE_TIERS_PRIORITY:
        length, width, depth = representative_dimensions(cell.shape, carat)
        for band_width in BAND_STEPS[cell.bucket]:
            for thickness in BAND_THICKNESS:
                combos.append(build_flat_config(
                    cell.shape, cell.setting, cell.prong,
                    carat, length, width, depth, band_width, thickness,
                ))
    return combos


def is_accepted(flat: dict) -> tuple[bool, list[str]]:
    """Run flat → canonical → constraints.validate; return (accepted, hard rule ids)."""
    result = constraints.validate(flat_to_canonical(flat))
    return result.accepted, [v.rule_id for v in result.hard_violations]


# ---------------------------------------------------------------------------
# Cell enumeration + demand weights
# ---------------------------------------------------------------------------

def enumerate_cells(shape: str) -> list[Cell]:
    """All constraint-valid headline cells for a shape (bezel → 0 prongs)."""
    cells: list[Cell] = []
    for setting in SETTING_ORDER:
        if setting == "bezel":
            for bucket in BUCKET_ORDER:
                cells.append(Cell(shape, "bezel", 0, bucket))
        else:
            for prong in VALID_PRONGS[shape]:
                for bucket in BUCKET_ORDER:
                    cells.append(Cell(shape, setting, prong, bucket))
    return cells


def cell_weight(cell: Cell) -> float:
    """Within-shape demand weight = setting × prong × band (bezel bypasses prong)."""
    setting_w = SETTING_SPLIT[cell.shape][cell.setting]
    band_w = BAND_SPLIT[cell.shape][cell.bucket]
    prong_w = 1.0 if cell.setting == "bezel" else PRONG_SPLIT[cell.shape][cell.prong]
    return setting_w * prong_w * band_w


# ---------------------------------------------------------------------------
# Capacity-capped floor + demand allocation
# ---------------------------------------------------------------------------

def capped_floor_demand(
    cells: list[Cell], budget: int, floor: int,
    caps: dict[Cell, int], weights: dict[Cell, float],
) -> dict[Cell, int]:
    """Floor every cell, then water-fill the remainder by demand, capped at capacity.

    Overflow above a cell's cap is redistributed to cells with spare capacity.
    Returns integer counts summing exactly to ``budget`` with floor ≤ count ≤ cap.
    """
    total_cap = sum(caps.values())
    if total_cap < budget:
        raise ValueError(f"insufficient valid capacity {total_cap} < budget {budget}")

    counts = {c: min(floor, caps[c]) for c in cells}
    remaining = budget - sum(counts.values())
    if remaining < 0:
        raise ValueError("budget below floor coverage")

    while remaining > 0:
        active = [c for c in cells if counts[c] < caps[c]]
        if not active:
            raise ValueError("no capacity left to place remaining configs")
        wsum = sum(weights[c] for c in active)
        progressed = False
        if wsum > 0:
            remainders: list[tuple[float, Cell]] = []
            placed = 0
            for c in active:
                want = remaining * weights[c] / wsum
                give = min(int(want), caps[c] - counts[c])
                counts[c] += give
                placed += give
                remainders.append((want - int(want), c))
            remaining -= placed
            progressed = placed > 0
            # Distribute the integer leftover by largest fractional remainder.
            remainders.sort(key=lambda t: t[0], reverse=True)
            for _, c in remainders:
                if remaining <= 0:
                    break
                if counts[c] < caps[c]:
                    counts[c] += 1
                    remaining -= 1
                    progressed = True
        if not progressed:
            # Safety net: place one unit into the cell with the most headroom.
            c = max(active, key=lambda c: caps[c] - counts[c])
            counts[c] += 1
            remaining -= 1
    return counts


# ---------------------------------------------------------------------------
# Allocation assembly
# ---------------------------------------------------------------------------

class ShapeAllocation(NamedTuple):
    cells: list[Cell]
    counts: dict[Cell, int]
    combos: dict[Cell, list[dict]]   # accepted flat configs per cell (ordered)
    skips: list[tuple[str, list[str]]]  # (config_id, hard rule ids) for rejected candidates


def allocate_shape(shape: str) -> ShapeAllocation:
    """Validate all candidates, compute valid capacity, then allocate the budget."""
    cells = enumerate_cells(shape)
    combos: dict[Cell, list[dict]] = {}
    caps: dict[Cell, int] = {}
    weights: dict[Cell, float] = {}
    skips: list[tuple[str, list[str]]] = []
    for cell in cells:
        accepted: list[dict] = []
        for flat in candidate_combos(cell):
            ok, hard = is_accepted(flat)
            if ok:
                accepted.append(flat)
            else:
                skips.append((flat["config_id"], hard))
        combos[cell] = accepted
        caps[cell] = len(accepted)
        weights[cell] = cell_weight(cell)
    counts = capped_floor_demand(cells, SHAPE_BUDGET[shape], FLOOR, caps, weights)
    return ShapeAllocation(cells, counts, combos, skips)


def build_allocation() -> dict[str, ShapeAllocation]:
    return {shape: allocate_shape(shape) for shape in SHAPES}


# ---------------------------------------------------------------------------
# Reporting + emission
# ---------------------------------------------------------------------------

def _cell_sort_key(cell: Cell) -> tuple:
    return (
        SHAPES.index(cell.shape),
        SETTING_ORDER.index(cell.setting),
        cell.prong,
        BUCKET_ORDER.index(cell.bucket),
    )


def allocation_rows(allocation: dict[str, ShapeAllocation]) -> list[dict]:
    """Flat table rows: shape, setting, prong, bucket, count, capacity."""
    rows: list[dict] = []
    for shape in SHAPES:
        sa = allocation[shape]
        for cell in sorted(sa.cells, key=_cell_sort_key):
            rows.append({
                "shape": cell.shape,
                "setting": cell.setting,
                "prong_count": cell.prong,
                "band": cell.bucket,
                "count": sa.counts[cell],
                "capacity": len(sa.combos[cell]),
            })
    return rows


def allocation_summary(allocation: dict[str, ShapeAllocation]) -> dict:
    rows = allocation_rows(allocation)
    per_shape = {s: sum(r["count"] for r in rows if r["shape"] == s) for s in SHAPES}
    total = sum(per_shape.values())
    n_skips = sum(len(allocation[s].skips) for s in SHAPES)
    top = sorted(rows, key=lambda r: r["count"], reverse=True)[:10]
    return {
        "total_configs": total,
        "per_shape": per_shape,
        "valid_cells": len(rows),
        "floor": FLOOR,
        "skipped_candidates": n_skips,
        "top_10_densest": top,
        "rows": rows,
    }


def emit_configs(allocation: dict[str, ShapeAllocation], out_dir: Path) -> dict:
    """Explode the allocation into per-config parameters.json dirs under out_dir.

    Writes one ``<config_id>/parameters.json`` per config plus ``tier1_index.json``
    (the full list) and ``skipped_candidates.jsonl``. Returns a summary.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for shape in SHAPES:
        sa = allocation[shape]
        for cell in sa.cells:
            for flat in sa.combos[cell][: sa.counts[cell]]:
                cfg_dir = out_dir / flat["config_id"]
                cfg_dir.mkdir(parents=True, exist_ok=True)
                (cfg_dir / "parameters.json").write_text(json.dumps(flat, indent=2))
                index.append({
                    "config_id": flat["config_id"],
                    "path": str((cfg_dir / "parameters.json").relative_to(out_dir)),
                    "shape": shape,
                    "center_setting": flat["center_setting"],
                    "prong_count": flat["prong_count"],
                    "band_width_mm": flat["band_width_mm"],
                    "stone_carat_eq": flat["stone_carat_eq"],
                })
    (out_dir / "tier1_index.json").write_text(json.dumps(index, indent=2))
    with (out_dir / "skipped_candidates.jsonl").open("w") as fh:
        for shape in SHAPES:
            for config_id, hard in allocation[shape].skips:
                fh.write(json.dumps({"config_id": config_id, "hard_violations": hard}) + "\n")
    return {"emitted": len(index), "out_dir": str(out_dir)}


def write_allocation_spec(allocation: dict[str, ShapeAllocation], path: Path) -> None:
    """Write the machine-consumable allocation table (the small authoritative spec)."""
    summary = allocation_summary(allocation)
    skips_by_rule: dict[str, int] = {}
    for shape in SHAPES:
        for _, hard in allocation[shape].skips:
            for rid in hard:
                skips_by_rule[rid] = skips_by_rule.get(rid, 0) + 1
    spec = {
        "schema": "tier1_allocation/v1",
        "decision": "R-1",
        "total_configs": summary["total_configs"],
        "per_shape_budget": SHAPE_BUDGET,
        "per_shape_allocated": summary["per_shape"],
        "valid_cells": summary["valid_cells"],
        "floor": FLOOR,
        "skipped_candidates_by_rule": skips_by_rule,
        "cells": summary["rows"],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(spec, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Tier-1 catalog config allocation.")
    parser.add_argument(
        "--spec-out", default="reaserch/planning/tier1_allocation.json",
        help="Path for the machine-consumable allocation spec (default: %(default)s).",
    )
    parser.add_argument(
        "--emit-configs", metavar="DIR", default=None,
        help="Also explode the allocation into per-config parameters.json dirs under DIR "
             "(M4-time; not committed). Geometry gates stay 'pending'.",
    )
    args = parser.parse_args(argv)

    allocation = build_allocation()
    summary = allocation_summary(allocation)
    write_allocation_spec(allocation, Path(args.spec_out))

    print(f"Tier-1 allocation: {summary['total_configs']} configs across "
          f"{summary['valid_cells']} valid cells (floor={FLOOR}).")
    print(f"Per-shape: {summary['per_shape']}")
    print(f"Skipped candidates (constraint-rejected, not emitted): {summary['skipped_candidates']}")
    print("Top 10 densest cells:")
    for r in summary["top_10_densest"]:
        prong = "—" if r["setting"] == "bezel" else r["prong_count"]
        print(f"  {r['shape']:9s} {r['setting']:7s} prong={prong!s:2s} "
              f"{r['band']:9s} → {r['count']}")
    print(f"Wrote allocation spec → {args.spec_out}")

    if args.emit_configs:
        result = emit_configs(allocation, Path(args.emit_configs))
        print(f"Emitted {result['emitted']} parameters.json configs → {result['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
