#!/usr/bin/env python3
"""Goal 0021 -- the analytic envelope sweep, and the claims it is allowed to make.

Protocol step 4: an audit script whose verdict matters and that nothing asserts
drifts silently. ``scripts/goal0011_ui_promotion.py`` reported ``not_ready`` for
five days while its goal entry still said ``ready``. So the verdicts here are
asserted, not printed:

* the definition constants this module copies still match the V6 script;
* the analytic sections still agree with measured geometry at all three solved
  points, within the stated band;
* the sweep still refuses everything outside the declared bounds, and still
  reports the in-bounds points it refuses instead of hiding them;
* nothing in the sweep ever emits a manufacturing verdict.
"""

from __future__ import annotations

import json
import math
import pathlib
import unittest

from jewelry_gaas_foundation import envelope_sweep as es
from jewelry_gaas_foundation import master_parameters as mp
from jewelry_gaas_foundation.manufacturing_assumptions import MANUFACTURING_FLOORS

FIXTURE = pathlib.Path(__file__).parent / "data" / "goal0021_v6_measured_sections.json"

V6_BUNDLE_ROOT = pathlib.Path(
    "output/goal_0018_castable_master_20260812/azure_v6/received"
)


def _v6_script() -> str:
    from manual.tools.build_goal0018_castable_master_v6_ghx import SCRIPT

    return SCRIPT


class DefinitionConstantTests(unittest.TestCase):
    """The scale factors this module copies out of the definition must still match.

    ``derive_parameters`` does not publish these -- the Grasshopper script applies
    them when it builds two specific parts -- so ``envelope_sweep`` holds a second
    copy. A second copy that nothing checks is how the seat-rail figure was wrong
    by 9% during development: the factor is 0.92 in the Goal 0009 base script and
    0.84 from V4 on, and reading the wrong file is silent.
    """

    def setUp(self):
        self.script = _v6_script()

    def test_seat_rail_factor_matches_the_v6_definition(self):
        self.assertIn(
            'circle_pipe(P["basket_upper_radius"], P["basket_upper_z"], '
            'P["gallery_wire_radius"] * {:g})'.format(es.SEAT_RAIL_WIRE_FACTOR),
            self.script,
        )

    def test_prong_tip_bead_factor_matches_the_v6_definition(self):
        self.assertIn(
            'rg.Sphere(prongs[-1], P["prong_tip_radius"] * {:g})'.format(
                es.PRONG_TIP_BEAD_FACTOR
            ),
            self.script,
        )

    def test_prong_tip_flatten_factor_matches_the_v6_definition(self):
        self.assertIn(
            "1.0, 1.0, {:g}))".format(es.PRONG_TIP_BEAD_FLATTEN), self.script
        )

    def test_the_export_scale_is_declared_as_derived_from_the_flatten(self):
        """The empirical constant must stay tied to the source literal it explains."""
        self.assertAlmostEqual(
            es.PRONG_TIP_BEAD_EXPORT_SCALE,
            math.sqrt(es.PRONG_TIP_BEAD_FLATTEN),
            places=12,
        )
        self.assertNotAlmostEqual(
            es.PRONG_TIP_BEAD_EXPORT_SCALE, es.PRONG_TIP_BEAD_FLATTEN, places=3
        )


class EnvelopeGridTests(unittest.TestCase):
    """The sweep must measure the envelope, not restate its declared bounds."""

    @classmethod
    def setUpClass(cls):
        cls.report = es.sweep(steps=5)

    def test_no_out_of_bound_probe_is_ever_accepted(self):
        for point in es.grid_points(4):
            if point.in_declared_bounds:
                continue
            es.evaluate_point(point)
            self.assertFalse(
                point.accepted,
                f"master accepted an out-of-envelope point: {point.as_dict()}",
            )

    def test_refusals_are_reported_not_swallowed(self):
        self.assertGreater(self.report.refused, 0)
        self.assertEqual(
            self.report.refused, sum(item["count"] for item in self.report.refusals)
        )
        for item in self.report.refusals:
            self.assertIn("outside the approved envelope", item["reason"])

    def test_in_bounds_grid_refusals_are_accounted_for(self):
        refused_in_bounds = (
            self.report.points_in_declared_bounds
            - self.report.accepted_in_declared_bounds
        )
        self.assertEqual(
            refused_in_bounds,
            sum(item["count"] for item in self.report.boundary_refusals),
        )

    def test_a_depth_ratio_at_the_upper_bound_is_not_always_attainable(self):
        """A ring inside the declared envelope that the master still refuses.

        Fail-closed and narrow, but customer-visible: the depth ratio is
        reconstructed as depth / diameter, and that does not round-trip in
        IEEE-754, so a depth chosen to sit exactly on the 0.75 bound comes back
        one ULP outside it for some diameters. Asserted directly rather than left
        to the grid: at 9 steps every sampled diameter is a whole number and the
        effect vanishes, which would report a real gate behaviour as an absence.
        """
        request = mp.configuration_request(6.5, 3.008, 0.75 * 3.008)
        with self.assertRaises(mp.MasterParameterError):
            mp.derive_parameters(request)

        rows = {row["bound"]: row for row in es.boundary_attainability(samples=1001)}
        upper = rows["stone_depth_ratio maximum"]
        self.assertGreater(upper["diameters_refused"], 0)
        self.assertIn("round-trip", upper["explanation"])
        self.assertIn("NOT FIXED HERE", upper["explanation"])

        # The lower bound is 0.5, a power of two, so it round-trips exactly.
        self.assertEqual(rows["stone_depth_ratio minimum"]["diameters_refused"], 0)

    def test_the_boundary_probe_is_carried_in_the_report(self):
        self.assertEqual(
            len(self.report.boundary_attainability), 2, self.report.boundary_attainability
        )

    def test_the_envelope_gate_is_not_widened_by_this_module(self):
        """No bound may be re-declared here; master_parameters stays the only gate."""
        for axis in es.envelope_axes():
            published = mp.envelope()
            key = {
                "us_size": "ring_size_us",
                "stone_diameter_mm": "stone_diameter_mm",
                "stone_depth_ratio": "stone_depth_ratio",
                "band_width_mm": "band_width_mm",
                "band_thickness_mm": "band_thickness_mm",
            }[axis.key]
            self.assertEqual(axis.minimum, published[key]["minimum"])
            self.assertEqual(axis.maximum, published[key]["maximum"])


class F3ScalingTests(unittest.TestCase):
    """F3, pinned numerically: every head section is proportional to the stone.

    The finding is not "the small end is thin". It is that there is **no floor
    anywhere** -- section and stone diameter are a straight line through the
    origin, so the sections shrink without limit as the stone does.
    """

    @classmethod
    def setUpClass(cls):
        cls.report = es.sweep(steps=5)

    #: Measured coefficients, section_mm per mm of stone diameter.
    EXPECTED = {
        "prong_tip_bead": 0.0479353,
        "stone_seat_rail_wire": 0.0516923,
        "prong_wire_min": 0.0523077,
        "basket_petal_wire": 0.0553846,
        "gallery_lower_rail_wire": 0.0615385,
        "prong_root": 0.0923077,
    }

    def test_head_sections_are_exactly_proportional_to_stone_diameter(self):
        for key, coefficient in self.EXPECTED.items():
            summary = self.report.feature(key)
            self.assertEqual(
                summary.proportional_to, "stone_diameter_mm",
                f"{key} is no longer proportional to the stone diameter",
            )
            self.assertAlmostEqual(summary.coefficient, coefficient, places=6)

    def test_there_is_no_floor_at_the_small_end(self):
        """Section at the envelope minimum must equal coefficient x 3.0 mm exactly.

        If any floor were ever introduced this assertion breaks, which is the
        point: the number is meant to be noticed when it changes.
        """
        for key, coefficient in self.EXPECTED.items():
            summary = self.report.feature(key)
            self.assertAlmostEqual(
                summary.min_section_mm,
                coefficient * mp.STONE_DIAMETER_MIN_MM,
                places=6,
            )

    def test_even_the_largest_configuration_stays_under_the_trade_band(self):
        """The escalation of F3: it is the whole envelope, not the small end.

        The thinnest *exposed* section at the envelope maximum is still far below
        the 1.0-1.3 mm the research brief flags for a 1 ct centre prong.
        """
        exposed = [f for f in self.report.features if f.free_end and f.coefficient]
        worst = min(exposed, key=lambda f: f.max_section_mm)
        self.assertLess(worst.max_section_mm, 1.0)
        self.assertAlmostEqual(worst.max_section_mm, 0.575385, places=5)

    def test_which_typed_inputs_each_section_responds_to_is_measured(self):
        """A section can depend on two inputs without being proportional to either.

        The shoulder wire is the thinner of a band-width-driven root and a
        stone-driven neck, so reporting "not proportional" as "depends on the
        band" -- the obvious shortcut -- is wrong, and it is wrong in a document
        going to a casting shop.
        """
        shoulder = self.report.feature("shoulder_wire")
        self.assertIsNone(shoulder.proportional_to)
        self.assertEqual(
            set(shoulder.varies_with), {"stone_diameter_mm", "band_width_mm"}
        )

        shank = self.report.feature("shank")
        self.assertEqual(
            set(shank.varies_with), {"band_width_mm", "band_thickness_mm"}
        )

        # Ring size positions the head but must never change a wire section.
        for summary in self.report.features:
            self.assertNotIn("us_size", summary.varies_with, summary.key)

    def test_the_reference_prong_base_is_the_figure_claude_md_cites(self):
        summary = self.report.feature("prong_root")
        self.assertAlmostEqual(
            summary.coefficient * mp.REF_STONE_DIAMETER, 0.600, places=6
        )


class MeasuredAgreementTests(unittest.TestCase):
    """Step 3: the analytic table must agree with real measured geometry.

    Runs off a committed extract of a full ``geometry_metrics`` measurement,
    because ``output/`` is gitignored and a fresh checkout would otherwise be
    unable to check the claim at all.
    """

    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.results = [
            es.validate_against_measurement(
                entry["request"], entry["parts"], bundle=entry["bundle"]
            )
            for entry in cls.fixture["bundles"]
        ]

    def test_all_three_solved_configurations_agree(self):
        for result in self.results:
            self.assertTrue(
                result["agrees"],
                f"{result['bundle']}: "
                + ", ".join(
                    f"{c['part']} {c['residual_fraction']:+.4f}"
                    for c in result["comparisons"]
                    if c["within_band"] is False
                ),
            )

    def test_every_modelled_part_is_actually_compared(self):
        modelled = {
            name for feature in es.WIRE_FEATURES for name in feature.part_names
        }
        for result in self.results:
            compared = {c["part"] for c in result["comparisons"]}
            self.assertEqual(compared, modelled)
            self.assertEqual(result["parts_compared"], len(modelled))

    def test_unmodelled_parts_are_listed_rather_than_dropped(self):
        for result in self.results:
            self.assertIn("center_stone_round_brilliant", result["parts_skipped"])
            self.assertIn("castable_solid", result["parts_skipped"])

    def test_the_residual_is_a_size_invariant_bias_not_noise(self):
        """The prong residual must be the same at every size.

        A systematic, scale-invariant residual is the signature of a modelling
        bias (here the ray crossing a shorter chord on a taper). A residual that
        varied with size would mean the proportionality claim itself is wrong,
        and the table could not be trusted.
        """
        prong = [
            next(
                c for c in result["comparisons"]
                if c["part"] == "prong_01"
            )["residual_fraction"]
            for result in self.results
        ]
        self.assertEqual(len(prong), 3)
        self.assertLess(max(prong) - min(prong), 0.001)

    def test_the_agreement_band_is_not_wide_enough_to_hide_a_real_error(self):
        """Guards the guard: a band loose enough to pass anything proves nothing."""
        self.assertGreaterEqual(es.MEASUREMENT_AGREEMENT_LOW, -0.10)
        self.assertLessEqual(es.MEASUREMENT_AGREEMENT_HIGH, 0.05)

    def test_a_wrong_section_model_would_be_caught(self):
        """Feed a deliberately wrong analytic model and require a disagreement."""
        entry = self.fixture["bundles"][0]
        original = es.FEATURES_BY_KEY["prong_wire_min"].section_mm
        try:
            object.__setattr__(
                es.FEATURES_BY_KEY["prong_wire_min"],
                "section_mm",
                lambda p: 2.0 * min(p["prong_radii"]) * 1.25,
            )
            result = es.validate_against_measurement(entry["request"], entry["parts"])
            self.assertFalse(result["agrees"])
            self.assertGreater(result["parts_outside_band"], 0)
        finally:
            object.__setattr__(
                es.FEATURES_BY_KEY["prong_wire_min"], "section_mm", original
            )


class SourceDivergenceTests(unittest.TestCase):
    """The prong tip bead is exported as a sphere, not the flattened bead intended."""

    def test_the_divergence_is_recorded(self):
        keys = {item.key for item in es.SOURCE_DIVERGENCES}
        self.assertIn("prong_tip_bead_not_flattened", keys)

    def test_the_as_built_model_matches_the_measured_bead(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for entry in fixture["bundles"]:
            parameters = mp.derive_parameters(entry["request"])
            as_built = es._prong_tip_bead_section(parameters)
            intent = es.prong_tip_bead_source_intent_section(parameters)
            measured = next(
                p["min_wall_thickness_mm"]
                for p in entry["parts"]
                if p["name"] == "prong_tip_01"
            )
            self.assertAlmostEqual(as_built, measured, delta=0.002)
            # And the source's own intent is the thing that does NOT match.
            self.assertGreater(abs(intent - measured) / measured, 0.15)

    def test_the_shipped_bead_is_geometrically_a_sphere(self):
        """Checked against the STLs themselves when they are on disk."""
        import trimesh  # local: the rest of this module needs no mesh library

        bundle = V6_BUNDLE_ROOT / "goal0018-castable-master-control"
        if not bundle.is_dir():
            self.skipTest(f"V6 bundle not present: {bundle}")
        mesh = trimesh.load(bundle / "prong_tip_01.stl", force="mesh")
        extents = mesh.bounds[1] - mesh.bounds[0]
        self.assertAlmostEqual(float(extents[0]), float(extents[2]), places=5)
        self.assertAlmostEqual(float(extents[1]), float(extents[2]), places=5)


class NoVerdictTests(unittest.TestCase):
    """The sweep measures. It must never certify, and never imply calibration."""

    @classmethod
    def setUpClass(cls):
        cls.report = es.sweep(steps=4)
        cls.payload = cls.report.to_dict()

    def test_no_candidate_floor_is_calibrated(self):
        for candidate in es.CANDIDATE_FLOORS:
            self.assertFalse(candidate.calibrated, candidate.key)
            self.assertIn(
                candidate.provenance,
                {
                    "myjewl_uncalibrated_baseline",
                    "third_party_unverified",
                    "question_for_the_shop",
                },
            )

    def test_floor_sensitivity_rows_declare_themselves_uncalibrated(self):
        for item in self.payload["floor_sensitivity"]:
            self.assertFalse(item["calibrated"])

    def test_the_report_restates_that_it_certifies_nothing(self):
        self.assertEqual(
            self.payload["authority"],
            "measurement_only_not_manufacturing_certification",
        )
        self.assertEqual(
            self.payload["manufacturing_gates"], "pending_not_run_needs_calibration"
        )

    def test_the_report_never_says_passed_or_failed(self):
        text = json.dumps(self.payload).lower()
        for word in ('"passed"', '"pass"', '"failed"', '"fail"', "certified"):
            self.assertNotIn(word, text)

    def test_the_sweep_does_not_fill_in_a_missing_manufacturing_floor(self):
        """The two floors that matter most must still have no number at all."""
        unrepresented = {
            floor.key for floor in MANUFACTURING_FLOORS if floor.value_mm is None
        }
        self.assertIn("min_castable_wire_diameter_mm", unrepresented)
        self.assertIn("min_prong_base_diameter_mm", unrepresented)

    def test_baseline_crosscheck_reports_the_disagreement_without_resolving_it(self):
        rows = {row["floor_key"]: row for row in self.payload["baseline_crosscheck"]}
        width = rows["min_shank_width_mm"]
        self.assertTrue(width["envelope_admits_below_floor"])
        self.assertFalse(width["calibrated"])
        self.assertGreater(width["fraction_of_envelope_below_floor"], 0.0)


class MarkdownTests(unittest.TestCase):
    """The deliverable is a document for a casting shop, not for this repository."""

    @classmethod
    def setUpClass(cls):
        cls.text = es.to_markdown(es.sweep(steps=4), generated="2026-08-17")

    def test_it_states_that_it_certifies_nothing(self):
        self.assertIn("asserts no manufacturing verdict", self.text)

    def test_it_leaks_no_code_identifiers_or_repository_paths(self):
        for token in (
            "SOURCE_DIVERGENCES",
            "derive_parameters",
            "master_parameters",
            "manufacturing_assumptions",
            "WireFeature",
            "free_end",
            "CLAUDE.md",
            "docs/",
            ".py",
        ):
            self.assertNotIn(token, self.text, f"{token!r} leaked into the document")

    def test_it_carries_the_reproduction_command(self):
        self.assertIn("scripts.sweep_envelope", self.text)

    def test_it_names_every_feature(self):
        for feature in es.WIRE_FEATURES:
            self.assertIn(feature.label, self.text)


if __name__ == "__main__":
    unittest.main()
