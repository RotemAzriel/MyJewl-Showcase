"""Goal 0006 manual configurator contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fast_mvp import manual_configurator, workflow
from fast_mvp.typed_inputs import TypedInputError
from jewelry_gaas_foundation.config_adapter import flat_to_canonical


BASE = {
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "stone_carat_eq": 1.0,
    "ring_size_us": 6.5,
    "metal": {"color_family": "yellow", "purity": "14k"},
}

SELECTION = {
    "head_style_id": "head_classic_4_prong",
    "band_style_id": "band_classic_solitaire",
}


class CompatibilityCatalogTests(unittest.TestCase):
    def test_options_include_only_explicitly_compatible_combinations(self):
        options = manual_configurator.compatible_options(BASE)
        head_ids = {
            item["head_style_id"] for item in options["head_styles"]
        }
        self.assertEqual(
            head_ids,
            {
                "head_v5_parametric_petal_4_prong",
                "head_v4_mid_petal_4_prong",
                "head_classic_4_prong",
                "head_crown_6_prong",
                "head_low_bezel",
                "head_petal_trilogy_4_prong",
            },
        )
        v5_bands = options["band_styles_by_head"]["head_v5_parametric_petal_4_prong"]
        self.assertEqual(
            [item["band_style_id"] for item in v5_bands],
            ["band_v5_parametric_split_solitaire"],
        )
        v4_bands = options["band_styles_by_head"]["head_v4_mid_petal_4_prong"]
        self.assertEqual(
            [item["band_style_id"] for item in v4_bands],
            ["band_v4_split_solitaire"],
        )
        bezel_bands = options["band_styles_by_head"]["head_low_bezel"]
        self.assertEqual(
            [item["band_style_id"] for item in bezel_bands],
            ["band_classic_solitaire"],
        )

    def test_api_boundary_rejects_unknown_and_incompatible_ids(self):
        with self.assertRaisesRegex(ValueError, "unknown head_style_id"):
            manual_configurator.configure(
                {**BASE, **SELECTION, "head_style_id": "retailer-style"}
            )
        with self.assertRaisesRegex(ValueError, "not compatible"):
            manual_configurator.configure(
                {
                    **BASE,
                    "head_style_id": "head_low_bezel",
                    "band_style_id": "band_micro_pave",
                }
            )

    def test_catalog_supports_expanded_diamond_shape_set(self):
        for shape in ("cushion", "pear", "emerald", "princess", "radiant"):
            options = manual_configurator.compatible_options(
                {
                    **BASE,
                    "stone_shape": shape,
                    "stone_length_mm": 6.0,
                    "stone_width_mm": 6.0,
                }
            )
            self.assertGreaterEqual(len(options["head_styles"]), 3)


class DeterministicConfigurationTests(unittest.TestCase):
    def test_supported_selection_is_deterministic_and_live_gated(self):
        first = manual_configurator.configure({**BASE, **SELECTION})
        second = manual_configurator.configure({**BASE, **SELECTION})
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["geometry_identity"], second["geometry_identity"])
        self.assertEqual(
            first["flat_parameters"]["config_id"],
            second["flat_parameters"]["config_id"],
        )
        self.assertTrue(first["validation"]["schema_accepted"])
        self.assertTrue(first["validation"]["constraint_accepted"])
        self.assertEqual(
            set(first["flat_parameters"]["geometry_gates"].values()), {"pending"}
        )
        self.assertEqual(
            first["canonical_spec"]["ring"]["profile"], "comfort-fit solitaire"
        )

    def test_metal_does_not_change_geometry_identity_or_compatibility(self):
        yellow_options = manual_configurator.compatible_options(BASE)
        white_payload = {
            **BASE,
            "metal": {"color_family": "white", "purity": "18k"},
        }
        white_options = manual_configurator.compatible_options(white_payload)
        self.assertEqual(
            yellow_options["band_styles_by_head"],
            white_options["band_styles_by_head"],
        )
        yellow = manual_configurator.configure({**BASE, **SELECTION})
        white = manual_configurator.configure({**white_payload, **SELECTION})
        self.assertEqual(yellow["geometry_identity"], white["geometry_identity"])
        self.assertNotEqual(
            yellow["flat_parameters"]["config_id"],
            white["flat_parameters"]["config_id"],
        )
        self.assertEqual(
            yellow["canonical_spec"]["ring"], white["canonical_spec"]["ring"]
        )
        self.assertEqual(
            yellow["canonical_spec"]["setting"],
            white["canonical_spec"]["setting"],
        )
        self.assertEqual(white["quote"]["status"], "pending_geometry")

    def test_metal_is_required_and_invalid_platinum_colour_is_rejected(self):
        without_metal = {key: value for key, value in BASE.items() if key != "metal"}
        with self.assertRaisesRegex(ValueError, "metal colour"):
            manual_configurator.configure({**without_metal, **SELECTION})
        with self.assertRaises(TypedInputError):
            manual_configurator.configure(
                {
                    **BASE,
                    **SELECTION,
                    "metal": {"color_family": "rose", "purity": "pt950"},
                }
            )

    def test_flat_adapter_preserves_optional_catalog_band_profile(self):
        report = manual_configurator.configure({**BASE, **SELECTION})
        adapted = flat_to_canonical(report["flat_parameters"])
        self.assertEqual(adapted["ring"]["profile"], "comfort-fit solitaire")


class CertificateAndWorkflowTests(unittest.TestCase):
    def test_reviewed_certificate_and_manual_measurements_share_contract(self):
        certificate_payload = {
            "certificate": {
                "lab": "gia",
                "report_number": "1234567890",
                "shape": "round",
                "measurements": "6.50 - 6.55 x 4.00 mm",
                "carat": 1.0,
                "reviewed": True,
            },
            "ring_size_us": 6.5,
            "metal": BASE["metal"],
            **SELECTION,
        }
        manual_payload = {
            **BASE,
            "stone_length_mm": 6.55,
            "stone_width_mm": 6.5,
            "stone_depth_mm": 4.0,
            **SELECTION,
        }
        certificate = manual_configurator.configure(certificate_payload)
        manual = manual_configurator.configure(manual_payload)
        self.assertEqual(certificate["geometry_identity"], manual["geometry_identity"])
        self.assertEqual(
            certificate["typed_inputs"]["stone_dimensions_mm"],
            manual["typed_inputs"]["stone_dimensions_mm"],
        )
        self.assertEqual(
            certificate["typed_inputs"]["stone_spec_source"], "certificate:gia"
        )

    def test_unreviewed_certificate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be reviewed"):
            manual_configurator.compatible_options(
                {
                    "certificate": {
                        "lab": "igi",
                        "report_number": "IGI-1",
                        "shape": "oval",
                        "measurements": "8.0 x 6.0 x 3.8 mm",
                    },
                    "ring_size_us": 6.0,
                }
            )

    def test_manual_run_persists_to_review_and_explicit_queue(self):
        report = manual_configurator.configure({**BASE, **SELECTION})
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            persisted = workflow.persist_proposal(report, runs)
            request = workflow.request_geometry(persisted["job_id"], runs)
            self.assertEqual(request["status"], "requested")
            review = workflow.review_index(runs)["runs"][0]
            self.assertEqual(review["lane"], "manual")
            self.assertEqual(review["geometry_identity"], report["geometry_identity"])
            self.assertEqual(review["selection"], SELECTION)
            self.assertEqual(
                review["config_summary"]["head_style_id"],
                SELECTION["head_style_id"],
            )
            self.assertEqual(set(review["geometry_gates"].values()), {"pending"})


if __name__ == "__main__":
    unittest.main()
