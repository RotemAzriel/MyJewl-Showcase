"""Goal 0016 — an in-envelope miss must become a fulfillable request, not a refusal.

Goal 0015 restricted the V5 catalog offer to the three solved configurations, so a
customer who typed US 7 was refused before they could describe what they wanted.
Goal 0016 moves the boundary to the master's own envelope. These tests hold both
halves of that move: what widened (what can be *expressed* and *queued*) and what
did not (what can be *claimed* — still nothing beyond a receipt-backed solve).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from fast_mvp import manual_configurator, workflow
from fast_mvp.preview_resolution import load_registry, resolve_preview
from jewelry_gaas_foundation import master_parameters
from scripts import goal0015_promote_v5 as promoter

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "fast_mvp" / "preview_assets" / "registry_v0_1.json"

V5_HEAD_ID = "head_v5_parametric_petal_4_prong"
V5_BAND_ID = "band_v5_parametric_split_solitaire"
V5_TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"

#: Solved on Azure and promoted: these preview exactly.
SOLVED = (
    (5.0, 5.0, 3.0, 4.0),
    (6.5, 6.5, 3.9, 6.5),
    (8.0, 8.0, 4.8, 9.0),
)

#: Inside the master's envelope, never solved. Each must be expressible, honestly
#: reported as having no preview, and queueable as a derived work order.
IN_ENVELOPE_UNSOLVED = (
    ("one ring size below a solve", 5.0, 5.0, 3.0, 3.5),
    ("between two solves", 6.5, 6.5, 3.9, 7.0),
    ("solved size, unsolved stone", 6.0, 6.0, 3.6, 6.5),
    ("solved stone, unsolved size", 8.0, 8.0, 4.8, 8.5),
)

#: Outside the master's envelope. These must still be refused, with the master's
#: own reason rather than an unqualified "not compatible".
OUT_OF_ENVELOPE = (
    ("stone too large for the master", 11.5, 11.5, 6.9, 6.5),
    ("stone too shallow", 6.5, 6.5, 3.0, 6.5),
    ("not round", 7.0, 6.0, 4.2, 6.5),
)


def payload(length, width, depth, ring_size, **overrides):
    return {
        "stone_shape": "round",
        "stone_length_mm": length,
        "stone_width_mm": width,
        "stone_depth_mm": depth,
        "ring_size_us": ring_size,
        "metal": {"color_family": "white", "purity": "pt950", "finish": "polished"},
        **overrides,
    }


def v5_report(length, width, depth, ring_size):
    return manual_configurator.configure(
        payload(
            length,
            width,
            depth,
            ring_size,
            head_style_id=V5_HEAD_ID,
            band_style_id=V5_BAND_ID,
        )
    )


class ExpressibilityTests(unittest.TestCase):
    def test_an_unsolved_in_envelope_configuration_is_offered(self):
        for label, length, width, depth, ring_size in IN_ENVELOPE_UNSOLVED:
            with self.subTest(configuration=label):
                options = manual_configurator.compatible_options(
                    payload(length, width, depth, ring_size)
                )
                head = next(
                    item
                    for item in options["head_styles"]
                    if item["head_style_id"] == V5_HEAD_ID
                )
                self.assertEqual(
                    head["preview_availability"]["status"],
                    manual_configurator.MADE_TO_ORDER,
                )
                self.assertEqual(
                    head["preview_availability"]["fulfillable_by_master"],
                    V5_TEMPLATE_ID,
                )
                self.assertTrue(
                    any(
                        band["band_style_id"] == V5_BAND_ID
                        for band in options["band_styles_by_head"][V5_HEAD_ID]
                    )
                )

    def test_a_solved_configuration_is_marked_preview_available(self):
        for length, width, depth, ring_size in SOLVED:
            with self.subTest(ring_size=ring_size):
                options = manual_configurator.compatible_options(
                    payload(length, width, depth, ring_size)
                )
                head = next(
                    item
                    for item in options["head_styles"]
                    if item["head_style_id"] == V5_HEAD_ID
                )
                availability = head["preview_availability"]
                self.assertEqual(
                    availability["status"], manual_configurator.PREVIEW_AVAILABLE
                )
                self.assertTrue(availability["configuration_id"])

    def test_the_catalog_offer_is_exactly_the_masters_envelope(self):
        # The catalog must not maintain a second copy of the envelope: whatever the
        # master refuses, the catalog refuses, and for the master's own reason.
        head = next(
            item
            for item in manual_configurator.HEAD_STYLES
            if item.style_id == V5_HEAD_ID
        )
        band = next(
            item
            for item in manual_configurator.BAND_STYLES
            if item.style_id == V5_BAND_ID
        )
        self.assertIsNotNone(head.parametric_master)
        self.assertEqual(
            (head.min_stone_dimension_mm, head.max_stone_dimension_mm),
            (
                master_parameters.STONE_DIAMETER_MIN_MM,
                master_parameters.STONE_DIAMETER_MAX_MM,
            ),
        )
        self.assertEqual(
            (band.min_ring_size_us, band.max_ring_size_us),
            (master_parameters.US_SIZE_MIN, master_parameters.US_SIZE_MAX),
        )

    def test_out_of_envelope_configurations_are_refused_with_the_masters_reason(self):
        for label, length, width, depth, ring_size in OUT_OF_ENVELOPE:
            with self.subTest(configuration=label):
                with self.assertRaises(ValueError) as caught:
                    v5_report(length, width, depth, ring_size)
                message = str(caught.exception)
                self.assertIn("not compatible", message)
                # The message must carry a diagnosis, not just a rejection: the
                # customer should learn which bound they crossed.
                self.assertTrue(
                    "envelope" in message
                    or "round master requires" in message
                    or "round-only" in message
                    or "supports" in message,
                    message,
                )

    def test_the_band_re_checks_its_own_section_against_the_master(self):
        head = next(
            item
            for item in manual_configurator.HEAD_STYLES
            if item.style_id == V5_HEAD_ID
        )
        band = next(
            item
            for item in manual_configurator.BAND_STYLES
            if item.style_id == V5_BAND_ID
        )
        stone = manual_configurator._parse_stone_inputs(
            payload(6.5, 6.5, 3.9, 6.5)
        )
        self.assertIsNone(band.refusal(stone, head))
        thick = manual_configurator.BandStyle(
            "band_out_of_envelope",
            "Out-of-envelope band",
            "A band section the master would refuse.",
            ("round",),
            (V5_HEAD_ID,),
            ("solitaire",),
            master_parameters.BAND_WIDTH_MAX_MM + 1.0,
            band.band_thickness_mm,
            band.band_profile,
            band.legacy_profile,
            band.shoulder_style,
            band.pave,
        )
        self.assertIsNotNone(thick.refusal(stone, head))


class NoSubstitutionTests(unittest.TestCase):
    """The widened offer must not have widened a single claim."""

    def test_an_unsolved_configuration_still_resolves_to_no_asset(self):
        for label, length, width, depth, ring_size in IN_ENVELOPE_UNSOLVED:
            with self.subTest(configuration=label):
                report = v5_report(length, width, depth, ring_size)
                manifest = report["preview_manifest"]
                self.assertEqual(report["status"], "accepted")
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertEqual(manifest["reason_code"], "no_approved_preview_asset")
                self.assertIsNone(manifest["selected_asset"])
                self.assertEqual(manifest["approximated_parameters"], [])

    def test_production_still_holds_zero_estimated_assets(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        self.assertEqual(
            [
                asset["asset_id"]
                for asset in registry["assets"]
                if asset["classification"] != "exact"
            ],
            [],
        )

    def test_a_solved_neighbour_is_never_returned_for_an_unsolved_request(self):
        solved = v5_report(6.5, 6.5, 3.9, 6.5)
        self.assertEqual(solved["preview_manifest"]["preview_status"], "exact")
        neighbour_asset = solved["preview_manifest"]["selected_asset"]["asset_id"]
        for offset in (0.5, 1.0, -0.5):
            with self.subTest(offset=offset):
                canonical = copy.deepcopy(solved["canonical_spec"])
                canonical["ring"]["size"] = 6.5 + offset
                manifest = resolve_preview(canonical, solved["flat_parameters"])
                self.assertEqual(manifest["preview_status"], "unavailable")
                self.assertIsNone(manifest["selected_asset"])
                self.assertNotIn(neighbour_asset, json.dumps(manifest))

    def test_availability_is_derived_from_the_registry_not_declared(self):
        # Promoting an asset is what makes the catalog offer it as available. An
        # empty registry must make everything made-to-order rather than keeping a
        # stale hand-written list of "solved" configurations alive.
        self.assertEqual(
            {
                item.configuration_id
                for item in manual_configurator.solved_configurations(V5_TEMPLATE_ID)
            },
            {"us4_round5", "us6p5_round6p5", "us9_round8"},
        )
        self.assertEqual(
            manual_configurator.solved_configurations(
                V5_TEMPLATE_ID, ROOT / "does-not-exist.json"
            ),
            (),
        )

    def test_a_style_with_no_master_is_reported_unproven_not_made_to_order(self):
        options = manual_configurator.compatible_options(payload(6.5, 6.5, 3.9, 7.0))
        for head in options["head_styles"]:
            if head["head_style_id"] == V5_HEAD_ID:
                continue
            with self.subTest(head=head["head_style_id"]):
                self.assertEqual(
                    head["preview_availability"]["status"],
                    manual_configurator.UNPROVEN,
                )
                self.assertIsNone(
                    head["preview_availability"]["fulfillable_by_master"]
                )


class QueuedWorkOrderTests(unittest.TestCase):
    def test_a_made_to_order_run_carries_a_derived_work_order(self):
        report = v5_report(6.5, 6.5, 3.9, 7.0)
        fulfillment = report["fulfillment"]
        self.assertEqual(fulfillment["availability"], manual_configurator.MADE_TO_ORDER)
        self.assertEqual(fulfillment["preview_status"], "unavailable")
        request = fulfillment["geometry_request"]
        self.assertEqual(request["solve_status"], "not_solved")
        self.assertEqual(request["master"]["template_id"], V5_TEMPLATE_ID)
        self.assertEqual(
            request["typed_request"]["canonical_spec"]["ring"]["size"], 7.0
        )
        # The derived parameters must be this configuration's, not a neighbour's.
        self.assertAlmostEqual(
            request["derived_parameters"]["inner_diameter"],
            master_parameters.inner_diameter_mm(7.0),
            places=6,
        )
        self.assertEqual(
            request["derived_parameters"]["derivation_version"],
            master_parameters.MASTER_PARAMETER_DERIVATION_VERSION,
        )

    def test_the_recorded_operator_path_names_scripts_that_exist(self):
        steps = manual_configurator._OPERATOR_FULFILLMENT_STEPS
        self.assertTrue(steps)
        for step in steps:
            with self.subTest(step=step):
                module = step.split("python3 -m ")[1].split()[0]
                self.assertTrue(
                    (ROOT / (module.replace(".", "/") + ".py")).is_file(), module
                )

    def test_a_queued_geometry_request_carries_the_work_order(self):
        import tempfile

        report = v5_report(6.5, 6.5, 3.9, 7.0)
        with tempfile.TemporaryDirectory() as raw:
            runs_dir = Path(raw)
            persisted = workflow.persist_proposal(report, runs_dir)
            request = workflow.request_geometry(persisted["job_id"], runs_dir)
        fulfillment = request["master_fulfillment"]
        self.assertEqual(
            fulfillment["geometry_request"]["master"]["template_id"], V5_TEMPLATE_ID
        )
        self.assertEqual(fulfillment["geometry_request"]["solve_status"], "not_solved")
        self.assertIn("has not been solved", request["operator_preflight"])
        # The app queues work; it never starts paid compute.
        self.assertEqual(request["status"], "requested")
        self.assertIn("explicit operator approval", request["note"])

    def test_a_solved_run_records_that_it_is_already_solved(self):
        report = v5_report(6.5, 6.5, 3.9, 6.5)
        fulfillment = report["fulfillment"]
        self.assertEqual(
            fulfillment["availability"], manual_configurator.PREVIEW_AVAILABLE
        )
        self.assertEqual(fulfillment["geometry_request"]["solve_status"], "solved")


@unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
class DataDrivenPromotionTests(unittest.TestCase):
    """A fourth configuration must need no edit to any promotion script."""

    def test_configurations_are_discovered_from_the_received_exports(self):
        discovered = promoter.discover_configurations(promoter.RECEIVED_ROOT)
        self.assertEqual(
            [item[0].configuration_id for item in discovered],
            ["us4_round5", "us6p5_round6p5", "us9_round8"],
        )
        self.assertTrue(all(not item[2] for item in discovered))

    def test_the_promoter_declares_no_configuration_list_of_its_own(self):
        source = (ROOT / "scripts" / "goal0015_promote_v5.py").read_text(
            encoding="utf-8"
        )
        for slug in ("us4_round5", "us6p5_round6p5", "us9_round8"):
            self.assertNotIn(slug, source)

    def test_the_promoter_reproduces_the_production_registry_byte_for_byte(self):
        rebuilt = json.dumps(promoter.build_registry(), indent=2, sort_keys=True) + "\n"
        self.assertEqual(rebuilt, REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_promotion_requires_a_recorded_visual_review(self):
        reviews = promoter.load_visual_reviews(promoter.VISUAL_REVIEWS_PATH)
        discovered = promoter.discover_configurations(promoter.RECEIVED_ROOT)
        for solved, _receipt, _rehearsal in discovered:
            self.assertIn(solved.configuration_id, reviews)
            self.assertTrue(reviews[solved.configuration_id]["path"])

    def test_an_unreviewed_configuration_cannot_be_promoted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            partial = Path(raw) / "reviews.json"
            document = json.loads(
                promoter.VISUAL_REVIEWS_PATH.read_text(encoding="utf-8")
            )
            document["reviews"].pop("us9_round8")
            partial.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no recorded visual review"):
                promoter.build_registry(REGISTRY_PATH, visual_reviews_path=partial)

    def test_the_registry_differential_count_comes_from_the_report(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        report = json.loads(
            (ROOT / promoter.DIFFERENTIAL_REPORT).read_text(encoding="utf-8")
        )
        for asset in registry["assets"]:
            if asset["provenance"]["template_id"] != V5_TEMPLATE_ID:
                continue
            with self.subTest(asset=asset["asset_id"]):
                self.assertEqual(
                    asset["approval"]["config_differential"]["config_count"],
                    report["config_count"],
                )


@unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
class RehearsalSafetyTests(unittest.TestCase):
    """The rehearsal proves the plumbing and must never reach a customer."""

    def test_a_rehearsal_receipt_is_refused_by_default(self):
        import tempfile

        from scripts.goal0016_fulfillment_rehearsal import write_rehearsal_export

        with tempfile.TemporaryDirectory() as raw:
            sandbox = Path(raw) / "received"
            write_rehearsal_export(promoter.RECEIVED_ROOT, sandbox)
            discovered = promoter.discover_configurations(sandbox)
            self.assertEqual(sum(1 for item in discovered if item[2]), 1)
            with self.assertRaisesRegex(ValueError, "rehearsal"):
                promoter.build_registry(REGISTRY_PATH, received_root=sandbox)

    def test_the_production_registry_holds_no_rehearsal_asset(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        serialized = json.dumps(registry)
        self.assertNotIn("rehearsal", serialized)
        self.assertNotIn("us7_round7", serialized)

    def test_manufacturing_stays_non_certifying(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        for asset in registry["assets"]:
            with self.subTest(asset=asset["asset_id"]):
                self.assertFalse(asset["manufacturing_authority"])
                self.assertEqual(
                    asset["manufacturing_state"], "pending_not_run_needs_calibration"
                )
        report = v5_report(6.5, 6.5, 3.9, 7.0)
        self.assertFalse(report["fulfillment"]["manufacturing_authority"])
        self.assertEqual(
            report["fulfillment"]["manufacturing_status"],
            "pending_not_run_needs_calibration",
        )


if __name__ == "__main__":
    unittest.main()
