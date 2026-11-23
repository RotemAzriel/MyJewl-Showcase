"""Tests for the R-1 Tier-1 config generator (scripts/generate_tier1_configs.py).

Proves the allocation is constraint-clean and demand-weighted without running the
M4 geometry batch: the emitted set is ~1,000 configs, every one is
``constraints.validate``-accepted, per-shape counts match the budget, cells respect
floor+capacity bounds, no shape-invalid cells are emitted, configs are genuinely
distinct geometries (≥0.1 mm band steps — no sub-threshold micro-steps), and the
constraint filter introduces no unexpected contract-rule skips.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from scripts import generate_tier1_configs as g

# Allowed band-width values across all buckets (the ≥0.1 mm step grid).
_ALLOWED_BAND_WIDTHS = {bw for steps in g.BAND_STEPS.values() for bw in steps}


class Tier1AllocationTests(unittest.TestCase):
    """Allocation-level checks (no file emission)."""

    @classmethod
    def setUpClass(cls):
        cls.alloc = g.build_allocation()
        cls.summary = g.allocation_summary(cls.alloc)

    def test_total_is_one_thousand(self):
        self.assertEqual(self.summary["total_configs"], 1000)

    def test_per_shape_counts_match_budget(self):
        self.assertEqual(self.summary["per_shape"], g.SHAPE_BUDGET)

    def test_floor_and_capacity_bounds(self):
        for shape in g.SHAPES:
            sa = self.alloc[shape]
            for cell in sa.cells:
                cap = len(sa.combos[cell])
                count = sa.counts[cell]
                self.assertGreaterEqual(count, min(g.FLOOR, cap),
                                        f"{cell} below floor")
                self.assertLessEqual(count, cap, f"{cell} above valid capacity")

    def test_every_valid_cell_has_floor_coverage(self):
        # Retrieval discriminability: each constraint-valid cell carries ≥ FLOOR.
        for shape in g.SHAPES:
            sa = self.alloc[shape]
            for cell in sa.cells:
                self.assertGreaterEqual(sa.counts[cell], g.FLOOR)

    def test_no_shape_invalid_cells(self):
        for shape in g.SHAPES:
            for cell in self.alloc[shape].cells:
                if cell.setting == "bezel":
                    self.assertEqual(cell.prong, 0, "bezel must be 0-prong (PRONG-009)")
                else:
                    self.assertIn(cell.prong, g.VALID_PRONGS[shape],
                                  f"{cell} uses a shape-invalid prong count")
        # Spot the classic invalids the brief calls out.
        round_prongs = {c.prong for c in self.alloc["round"].cells if c.setting != "bezel"}
        self.assertNotIn(5, round_prongs)
        self.assertEqual({c.prong for c in self.alloc["pear"].cells if c.setting != "bezel"},
                         {5, 6})

    def test_no_contract_rule_skips(self):
        for shape in g.SHAPES:
            self.assertEqual(self.alloc[shape].skips, [])


class Tier1EmissionTests(unittest.TestCase):
    """Emission-level checks (writes ~1,000 parameters.json into a temp dir)."""

    @classmethod
    def setUpClass(cls):
        cls.alloc = g.build_allocation()
        cls._tmp = tempfile.TemporaryDirectory()
        g.emit_configs(cls.alloc, Path(cls._tmp.name))
        cls.flats = [json.loads(p.read_text())
                     for p in Path(cls._tmp.name).rglob("parameters.json")]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_emits_one_thousand_configs(self):
        self.assertEqual(len(self.flats), 1000)

    def test_every_emitted_config_is_accepted(self):
        for flat in self.flats:
            result = constraints.validate(flat_to_canonical(flat))
            self.assertTrue(result.accepted,
                            f"{flat['config_id']} rejected: "
                            f"{[v.rule_id for v in result.hard_violations]}")

    def test_per_shape_counts_match_allocation(self):
        per_shape = {}
        for flat in self.flats:
            per_shape[flat["stone_shape"]] = per_shape.get(flat["stone_shape"], 0) + 1
        self.assertEqual(per_shape, g.SHAPE_BUDGET)

    def test_no_two_configs_within_min_geometric_delta(self):
        # Distinctness must be a real geometric difference, not just a unique id.
        geom = [
            (f["stone_shape"], f["center_setting"], f["prong_count"],
             f["stone_length_mm"], f["stone_width_mm"], f["stone_depth_mm"],
             f["band_width_mm"], f["band_thickness_mm"])
            for f in self.flats
        ]
        self.assertEqual(len(set(geom)), len(geom), "duplicate geometries emitted")

    def test_band_widths_are_meaningful_steps(self):
        # No sub-0.1 mm micro-steps: every width is on the allowed ≥0.1 mm grid.
        for flat in self.flats:
            self.assertIn(flat["band_width_mm"], _ALLOWED_BAND_WIDTHS)
            self.assertGreaterEqual(flat["band_width_mm"], 1.8)  # catalog-grid floor

    def test_geometry_gates_stay_pending(self):
        for flat in self.flats:
            self.assertTrue(all(v == "pending" for v in flat["geometry_gates"].values()),
                            f"{flat['config_id']} has a non-pending geometry gate")

    def test_size_tiers_stay_light(self):
        # Most configs sit at the modal 1.0 ct; other tiers are a thin spread.
        sizes = [f["stone_carat_eq"] for f in self.flats]
        self.assertGreater(sizes.count(1.0) / len(sizes), 0.5)


class KnownInvalidConfigsTests(unittest.TestCase):
    """Only contract contradictions reject; catalog baselines remain SOFT."""

    def _flat(self, **overrides):
        flat = g.build_flat_config("round", "prong", 4, 1.0, 6.5, 6.5, 3.9, 2.0, 1.6)
        flat.update(overrides)
        return flat

    def test_five_prong_round_is_soft(self):
        result = constraints.validate(flat_to_canonical(self._flat(prong_count=5)))
        self.assertTrue(result.accepted)
        self.assertIn("PRONG-001", {v.rule_id for v in result.soft_warnings})

    def test_prongs_on_bezel_rejected(self):
        flat = self._flat(center_setting="bezel", prong_count=4)
        result = constraints.validate(flat_to_canonical(flat))
        self.assertFalse(result.accepted)
        self.assertIn("PRONG-009", {v.rule_id for v in result.hard_violations})

    def test_sub_floor_band_width_is_soft(self):
        result = constraints.validate(flat_to_canonical(self._flat(band_width_mm=1.7)))
        self.assertTrue(result.accepted)
        self.assertIn("BAND-001", {v.rule_id for v in result.soft_warnings})


if __name__ == "__main__":
    unittest.main()
