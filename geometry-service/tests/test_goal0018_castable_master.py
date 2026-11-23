#!/usr/bin/env python3
"""Tests for the Goal 0018 V6 castable master.

V6 fixes two measured defects: F1 (inverted normals from ``Brep.CreateFromLoft``)
and F2 (no boolean union). These tests assert the fixes are present in the emitted
source, that V6 did not disturb V5, and that the derivation contract V5 established
still holds.

They verify the *source*, not the geometry -- proving the geometry requires a Rhino
solve, which is Goal 0018 Part 2's Azure run.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from jewelry_gaas_foundation.geometry_metrics import (
    ASSEMBLY_EXCLUDED_STEMS,
    DERIVED_SOLID_STEMS,
    measure_bundle,
)
from jewelry_gaas_foundation.master_parameters import derivation_block_source
from manual.tools.build_goal0014_parametric_master_ghx import SCRIPT as V5_SCRIPT
from manual.tools.build_goal0018_castable_master_v6_ghx import (
    SCRIPT as V6_SCRIPT,
    TEMPLATE_ID,
)


class SourceIntegrityTests(unittest.TestCase):
    def test_v6_is_valid_python(self):
        ast.parse(V6_SCRIPT)

    def test_v6_does_not_mutate_v5(self):
        """V5 stays byte-identical: the production registry pins its hash."""
        self.assertNotIn("goal0018", V5_SCRIPT)
        self.assertIn("goal0014-parametric-round-solitaire-master-v5", V5_SCRIPT)
        self.assertNotIn("ensure_outward", V5_SCRIPT)

    def test_derivation_block_is_still_inlined_verbatim(self):
        """The V5 contract: definition and test suite share one derivation source."""
        self.assertIn(derivation_block_source(), V6_SCRIPT)

    def test_template_id_is_distinct_from_v5(self):
        self.assertIn(TEMPLATE_ID, V6_SCRIPT)
        self.assertEqual(TEMPLATE_ID, "goal0018-castable-round-solitaire-master-v6")

    def test_no_frozen_geometry_literals_survive(self):
        """Inherited from V5: geometry must come from the derivation, not literals."""
        end = "# --- END MYJEWL MASTER PARAMETER DERIVATION v1 ---"
        geometry_source = V6_SCRIPT[V6_SCRIPT.index(end) + len(end):]
        for literal in ("5.35", "4.82", "1.30815", "12.10", "16.91"):
            self.assertNotIn(literal, geometry_source, f"frozen literal {literal}")


class OrientationFixTests(unittest.TestCase):
    """F1 -- orientation must be repaired at the Brep level, before meshing."""

    def test_ensure_outward_exists_and_tests_solid_orientation(self):
        self.assertIn("def ensure_outward(brep):", V6_SCRIPT)
        self.assertIn("rg.BrepSolidOrientation.Inward", V6_SCRIPT)
        self.assertIn("brep.Flip()", V6_SCRIPT)

    def test_tapered_sweep_returns_an_oriented_brep(self):
        self.assertIn("return ensure_outward(capped)", V6_SCRIPT)

    def test_every_metal_part_passes_through_the_repair(self):
        """Correctness must not depend on which constructor built the part."""
        self.assertIn(
            'ensure_outward(geometry) if role == "metal" else geometry', V6_SCRIPT
        )

    def test_repair_counter_resets_between_solves(self):
        """Rhino.Compute reuses module state across a batch."""
        self.assertIn("del ORIENTATION_REPAIRS[:]", V6_SCRIPT)
        self.assertIn('"orientation_repairs": len(ORIENTATION_REPAIRS)', V6_SCRIPT)


class UnionFixTests(unittest.TestCase):
    """F2 -- a real boolean union, and it must not corrupt the existing outputs."""

    def test_union_uses_a_real_boolean(self):
        self.assertIn("def union_metal(breps):", V6_SCRIPT)
        self.assertIn("rg.Brep.CreateBooleanUnion(solids, TOLERANCE)", V6_SCRIPT)

    def test_union_is_non_fatal(self):
        """A failed union reports itself rather than failing a working run."""
        self.assertIn('return None, "union_returned_empty"', V6_SCRIPT)
        self.assertIn('return None, "union_raised_"', V6_SCRIPT)
        self.assertIn('"castable_union_status": castable_status', V6_SCRIPT)

    def test_union_excludes_the_stone(self):
        """A caster receiving a fused stone gets a metal lump."""
        self.assertIn(
            '[geometry for name, role, geometry in parts if role == "metal"]',
            V6_SCRIPT,
        )
        self.assertIn('"stone_in_castable_solid": False', V6_SCRIPT)

    def test_castable_solid_is_added_after_the_preview_surfaces(self):
        """Otherwise OBJ/3DM carry the parts plus a coincident copy of their union."""
        obj_at = V6_SCRIPT.index("write_obj(master_obj, meshes)")
        append_at = V6_SCRIPT.index('meshes.append(("castable_solid"')
        self.assertLess(obj_at, append_at)

        three_dm_at = V6_SCRIPT.index("write_3dm(master_3dm, parts)")
        self.assertLess(three_dm_at, append_at)

    def test_declares_the_dimension_basis(self):
        """STL carries no metadata, so the basis is asserted in the receipt."""
        self.assertIn(
            '"dimension_basis": "finished_nominal_no_compensation_applied"', V6_SCRIPT
        )


class MetricsInteropTests(unittest.TestCase):
    """The measurement instrument must not double-count the derived solid."""

    def test_castable_solid_is_excluded_from_the_metal_assembly(self):
        self.assertIn("castable_solid", DERIVED_SOLID_STEMS)
        self.assertIn("castable_solid", ASSEMBLY_EXCLUDED_STEMS)

    def test_model_and_stone_remain_excluded(self):
        self.assertIn("model", ASSEMBLY_EXCLUDED_STEMS)
        self.assertIn("center_stone_round_brilliant", ASSEMBLY_EXCLUDED_STEMS)


class SolvedV6BundleTests(unittest.TestCase):
    """Against the receipt-backed V6 geometry solved on Azure 2026-08-12.

    This is the before/after that closes F1 and F2. It runs only when the
    exports are present.
    """

    BUNDLE = pathlib.Path(
        "output/goal_0018_castable_master_20260812/azure_v6/received/"
        "goal0018-castable-master-control"
    )
    V5_BUNDLE = pathlib.Path(
        "output/goal_0014_parametric_master_20260804/azure_v5/received/"
        "goal0014-parametric-master-control"
    )

    @classmethod
    def setUpClass(cls):
        if not cls.BUNDLE.is_dir():
            raise unittest.SkipTest(f"V6 bundle not present: {cls.BUNDLE}")
        cls.report = measure_bundle(cls.BUNDLE, thickness_samples=0)

    def test_f1_closed_no_inverted_parts_remain(self):
        self.assertEqual(self.report.assembly.inverted_part_count, 0)
        codes = {f.code for f in self.report.findings}
        self.assertNotIn("INVERTED_NORMALS", codes)

    def test_f2_closed_bundle_ships_a_valid_castable_solid(self):
        codes = {f.code for f in self.report.findings}
        self.assertNotIn("PARTS_NOT_UNIONED", codes)
        self.assertNotIn("UNION_DISAGREEMENT", codes)

        castable = next(
            p for p in self.report.parts if p.name == "castable_solid"
        )
        self.assertTrue(castable.watertight)
        self.assertTrue(castable.oriented_outward)

    def test_no_critical_findings_remain(self):
        critical = [f for f in self.report.findings if f.severity == "critical"]
        self.assertEqual(critical, [], f"unexpected critical findings: {critical}")

    def test_two_independent_boolean_engines_agree(self):
        """Rhino's NURBS union vs manifold3d's mesh union, same input."""
        castable = next(
            p for p in self.report.parts if p.name == "castable_solid"
        )
        independent = self.report.assembly.union_mm3
        delta = abs(castable.absolute_volume_mm3 - independent) / independent
        self.assertLess(delta, 0.005)

    def test_the_fix_changed_orientation_not_shape(self):
        """V6 volume must match V5: flipping normals must not move geometry."""
        if not self.V5_BUNDLE.is_dir():
            self.skipTest("V5 bundle not present for comparison")
        v5 = measure_bundle(self.V5_BUNDLE, thickness_samples=0)
        self.assertAlmostEqual(
            self.report.assembly.union_mm3, v5.assembly.union_mm3, places=3
        )
        self.assertAlmostEqual(
            self.report.assembly.parts_sum_mm3, v5.assembly.parts_sum_mm3, places=3
        )


if __name__ == "__main__":
    unittest.main()
