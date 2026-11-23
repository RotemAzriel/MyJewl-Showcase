#!/usr/bin/env python3
"""Tests for the Goal 0018 geometry measurement instrument.

Covers the three defects the instrument exists to surface (F1 inverted normals,
F2 missing union, F3 thin sections), the brand-protecting rule that it never
certifies, and the accuracy of the thickness method against analytic truth.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trimesh

from jewelry_gaas_foundation import manufacturing_assumptions as ma
from jewelry_gaas_foundation.geometry_metrics import (
    ASSEMBLY_EXCLUDED_STEMS,
    measure_bundle,
    measure_part,
)
from jewelry_gaas_foundation.stl_gates import check_mesh_closedness

BUNDLE = Path(
    "output/goal_0014_parametric_master_20260804/azure_v5/received/"
    "goal0014-parametric-master-control"
)


def _write(mesh: trimesh.Trimesh, directory: Path, name: str) -> Path:
    path = directory / f"{name}.stl"
    mesh.export(path)
    return path


class OrientationDetectionTests(unittest.TestCase):
    """F1 -- the defect Gate 1 cannot see."""

    def test_inverted_box_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            box.invert()
            path = _write(box, Path(tmp), "inverted")
            metrics = measure_part(path, thickness_samples=0)

            self.assertTrue(metrics.watertight)
            self.assertFalse(metrics.oriented_outward)
            self.assertLess(metrics.signed_volume_mm3, 0.0)
            self.assertAlmostEqual(metrics.absolute_volume_mm3, 8.0, places=5)

    def test_correct_box_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(trimesh.creation.box(extents=(2.0, 2.0, 2.0)), Path(tmp), "ok")
            metrics = measure_part(path, thickness_samples=0)
            self.assertTrue(metrics.oriented_outward)
            self.assertGreater(metrics.signed_volume_mm3, 0.0)

    def test_gate1_passes_what_this_module_catches(self):
        """The regression that let F1 survive: watertight != correctly oriented."""
        with tempfile.TemporaryDirectory() as tmp:
            box = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            box.invert()
            path = _write(box, Path(tmp), "inverted")

            self.assertEqual(check_mesh_closedness(path).status, "pass")
            self.assertFalse(measure_part(path, thickness_samples=0).oriented_outward)


class ThicknessAccuracyTests(unittest.TestCase):
    """F3 -- the measurement must be trustworthy on untapered wire."""

    def test_matches_analytic_diameter_on_a_torus(self):
        with tempfile.TemporaryDirectory() as tmp:
            tube_radius = 0.20
            torus = trimesh.creation.torus(
                major_radius=5.0, minor_radius=tube_radius,
                major_sections=180, minor_sections=48,
            )
            path = _write(torus, Path(tmp), "rail")
            metrics = measure_part(path, thickness_samples=4000)

            truth = tube_radius * 2.0
            self.assertIsNotNone(metrics.wall_thickness_p05_mm)
            # Faceting makes the polygonal section inscribe the true circle, so the
            # method reads slightly small. Never over-reads.
            self.assertLessEqual(metrics.wall_thickness_p05_mm, truth)
            self.assertAlmostEqual(metrics.wall_thickness_p05_mm, truth, delta=0.005)

            # The *minimum* must be trustworthy too, not just the percentile.
            # Before grazing-ray rejection this read near zero on clean geometry.
            self.assertGreater(metrics.min_wall_thickness_mm, truth * 0.95)

    def test_minimum_and_percentile_agree_on_clean_geometry(self):
        """Divergence between them is the tell that grazing rays are leaking in.

        This is the regression that put 0.0016 mm into four documents.
        """
        with tempfile.TemporaryDirectory() as tmp:
            torus = trimesh.creation.torus(
                major_radius=5.0, minor_radius=0.20,
                major_sections=180, minor_sections=48,
            )
            path = _write(torus, Path(tmp), "rail")
            metrics = measure_part(path, thickness_samples=4000)
            self.assertAlmostEqual(
                metrics.min_wall_thickness_mm,
                metrics.wall_thickness_p05_mm,
                delta=0.01,
            )

    def test_grazing_rejection_is_declared_in_the_method_string(self):
        """A consumer must be able to tell which method produced the number."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(trimesh.creation.box(extents=(1.0, 1.0, 1.0)), Path(tmp), "b")
            metrics = measure_part(path, thickness_samples=500)
            self.assertIn("grazing_rejected", metrics.wall_thickness_method or "")

    def test_method_and_sample_count_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(trimesh.creation.box(extents=(1.0, 1.0, 1.0)), Path(tmp), "b")
            metrics = measure_part(path, thickness_samples=500)
            self.assertIn("inward_ray_cast", metrics.wall_thickness_method or "")
            self.assertGreater(metrics.wall_thickness_samples or 0, 0)


class AssemblyUnionTests(unittest.TestCase):
    """F2 -- the naive sum and the union must never be conflated."""

    def test_overlapping_parts_report_distinct_sum_and_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            b = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
            b.apply_translation((1.0, 0.0, 0.0))  # 50% overlap
            _write(a, tmp_path, "part_a")
            _write(b, tmp_path, "part_b")

            report = measure_bundle(tmp_path, thickness_samples=0)
            assembly = report.assembly
            self.assertIsNotNone(assembly)
            self.assertAlmostEqual(assembly.parts_sum_mm3, 16.0, places=4)
            self.assertIsNotNone(assembly.union_mm3)
            self.assertAlmostEqual(assembly.union_mm3, 12.0, delta=0.01)
            self.assertGreater(assembly.overcount_mm3, 0.0)
            self.assertIn(
                "PARTS_NOT_UNIONED", {f.code for f in report.findings}
            )

    def test_disjoint_parts_are_reported_as_separate_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
            b = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
            b.apply_translation((10.0, 0.0, 0.0))
            _write(a, tmp_path, "a")
            _write(b, tmp_path, "b")

            report = measure_bundle(tmp_path, thickness_samples=0)
            self.assertEqual(report.assembly.connected_components, 2)
            self.assertIn("DISCONNECTED_PARTS", {f.code for f in report.findings})


class NonCertificationTests(unittest.TestCase):
    """Brand-protecting rule: measurement is never a manufacturing verdict."""

    def test_report_declares_non_certifying_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(trimesh.creation.box(extents=(1.0, 1.0, 1.0)), Path(tmp), "p")
            report = measure_bundle(Path(tmp), thickness_samples=0)

            self.assertEqual(
                report.authority, "measurement_only_not_manufacturing_certification"
            )
            self.assertEqual(
                report.manufacturing_gates, "pending_not_run_needs_calibration"
            )

    def test_no_finding_ever_claims_a_pass_or_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(trimesh.creation.box(extents=(1.0, 1.0, 1.0)), Path(tmp), "p")
            report = measure_bundle(Path(tmp), thickness_samples=200)
            for finding in report.findings:
                self.assertIn(finding.severity, {"critical", "structural", "note"})
                self.assertNotIn("passed", finding.summary.lower())
                self.assertNotIn("certif", finding.summary.lower())

    def test_uncalibrated_floors_carry_no_fabricated_value(self):
        for floor in ma.unrepresented_floors():
            self.assertIsNone(floor.value_mm)
            self.assertFalse(floor.calibrated)

    def test_held_tolerances_are_not_presented_as_agreed(self):
        held = {item.key for item in ma.held_tolerances()}
        self.assertIn("prong_base_diameter_mm", held)
        self.assertIn("prong_coverage_mm", held)
        for item in ma.agreed_tolerances():
            self.assertTrue(item.agreed)


class RealBundleTests(unittest.TestCase):
    """Against the receipt-backed production geometry, when it is present."""

    @classmethod
    def setUpClass(cls):
        if not BUNDLE.is_dir():
            raise unittest.SkipTest(f"bundle not present: {BUNDLE}")
        cls.report = measure_bundle(BUNDLE, thickness_samples=800)

    def test_reproduces_the_twelve_inverted_parts(self):
        self.assertEqual(self.report.assembly.inverted_part_count, 12)
        inverted = [f for f in self.report.findings if f.code == "INVERTED_NORMALS"]
        self.assertEqual(len(inverted), 12)

    def test_assembly_is_one_touching_group(self):
        self.assertEqual(self.report.assembly.connected_components, 1)

    def test_union_is_a_single_watertight_solid_smaller_than_the_sum(self):
        assembly = self.report.assembly
        self.assertTrue(assembly.union_watertight)
        self.assertEqual(assembly.union_body_count, 1)
        self.assertLess(assembly.union_mm3, assembly.parts_sum_mm3)

    def test_model_stl_is_never_the_thin_section_candidate(self):
        """model.stl is disjoint bodies; inward rays cross the gaps between them."""
        self.assertIn("model", ASSEMBLY_EXCLUDED_STEMS)
        thin = [f for f in self.report.findings if f.code == "THIN_SECTION_UNCHECKED"]
        for finding in thin:
            self.assertNotIn(finding.part, ASSEMBLY_EXCLUDED_STEMS)

    def test_mass_is_reported_from_union_not_the_naive_sum(self):
        masses = self.report.masses_g({"14k_yellow": 13.07})
        entry = masses["14k_yellow"]
        self.assertIsNotNone(entry["from_union_g"])
        self.assertLess(entry["from_union_g"], entry["from_parts_sum_g"])


if __name__ == "__main__":
    unittest.main()
