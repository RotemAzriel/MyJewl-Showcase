"""Goal 0010 seven-shape configurator catalog and preview payload tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from fast_mvp import image_lane, manual_configurator
from fast_mvp.pipeline import StoneInputs, analyze_photo_request
from fast_mvp.typed_inputs import build_metal_spec


ROOT = Path(__file__).resolve().parents[1]
MANUAL_HTML = ROOT / "fast_mvp" / "static" / "manual.html"

SHAPE_DIMENSIONS = {
    "round": (6.5, 6.5, 3.9),
    "oval": (8.0, 6.0, 3.8),
    "cushion": (6.5, 6.5, 4.2),
    "pear": (8.5, 5.5, 3.7),
    "emerald": (7.0, 5.0, 3.5),
    "princess": (5.5, 5.5, 4.0),
    "radiant": (6.5, 5.5, 3.8),
}


def payload_for(shape: str) -> dict:
    length, width, depth = SHAPE_DIMENSIONS[shape]
    return {
        "stone_shape": shape,
        "stone_length_mm": length,
        "stone_width_mm": width,
        "stone_depth_mm": depth,
        "stone_carat_eq": 1.0,
        "ring_size_us": 6.5,
        "metal": {
            "color_family": "yellow",
            "purity": "14k",
            "finish": "polished",
        },
    }


class SevenShapeCatalogTests(unittest.TestCase):
    def test_revision_and_compact_shape_selects_cover_the_bounded_set(self):
        self.assertEqual(manual_configurator.CATALOG_REVISION, "modular-rings-v5")
        html = MANUAL_HTML.read_text(encoding="utf-8")
        for select_id in ("stone-shape", "cert-shape"):
            select = html.split(f'id="{select_id}"', 1)[1].split("</select>", 1)[0]
            self.assertEqual(
                set(re.findall(r'<option value="([^"]+)">', select)),
                set(SHAPE_DIMENSIONS),
            )
        self.assertNotIn("round + oval modular rings", html)

    def test_every_returned_shape_head_band_pair_configures_without_substitution(self):
        configured_pairs = 0
        for shape in manual_configurator.SUPPORTED_SHAPES:
            with self.subTest(shape=shape):
                payload = payload_for(shape)
                options = manual_configurator.compatible_options(payload)
                self.assertEqual(options["catalog_revision"], "modular-rings-v5")
                self.assertTrue(options["head_styles"])

                for head in options["head_styles"]:
                    head_id = head["head_style_id"]
                    self.assertTrue(head_id)
                    self.assertIn(shape, head["supported_shapes"])
                    self.assertEqual(
                        set(head["canonical_mapping"]),
                        {
                            "job.archetype",
                            "setting.center_setting",
                            "setting.prong_count",
                            "setting.head_family",
                            "setting.head_geometry",
                            "setting.gallery_structure",
                            "setting.side_stone_layout",
                            "setting.side_setting",
                        },
                    )
                    bands = options["band_styles_by_head"].get(head_id)
                    self.assertTrue(bands, head_id)

                    for band in bands:
                        configured_pairs += 1
                        band_id = band["band_style_id"]
                        self.assertTrue(band_id)
                        self.assertIn(shape, band["supported_shapes"])
                        self.assertIn(head_id, band["compatible_head_style_ids"])
                        self.assertEqual(
                            set(band["canonical_mapping"]),
                            {
                                "ring.band_width_mm",
                                "ring.band_thickness_mm",
                                "ring.profile",
                                "ring.band_profile",
                                "ring.shoulder_style",
                                "ring.pave",
                            },
                        )

                        report = manual_configurator.configure(
                            {
                                **payload,
                                "head_style_id": head_id,
                                "band_style_id": band_id,
                            }
                        )
                        self.assertEqual(report["status"], "accepted")
                        self.assertEqual(report["selection"]["head_style_id"], head_id)
                        self.assertEqual(report["selection"]["band_style_id"], band_id)
                        self.assertTrue(report["validation"]["schema_accepted"])
                        self.assertTrue(report["validation"]["constraint_accepted"])
                        self.assertEqual(
                            report["canonical_spec"]["job"]["archetype"],
                            head["canonical_mapping"]["job.archetype"],
                        )
                        self.assertEqual(
                            report["canonical_spec"]["ring"]["band_profile"],
                            band["canonical_mapping"]["ring.band_profile"],
                        )

        # 51 through Goal 0011, plus the Goal 0015 V5 parametric pair, which is
        # offered only at the three receipt-backed configurations.
        self.assertEqual(configured_pairs, 52)

    def test_unknown_and_incompatible_ids_are_rejected(self):
        payload = payload_for("round")
        with self.assertRaisesRegex(ValueError, "unknown head_style_id"):
            manual_configurator.configure(
                {
                    **payload,
                    "head_style_id": "unknown_head",
                    "band_style_id": "band_classic_solitaire",
                }
            )
        with self.assertRaisesRegex(ValueError, "not compatible"):
            manual_configurator.configure(
                {
                    **payload,
                    "head_style_id": "head_low_bezel",
                    "band_style_id": "band_micro_pave",
                }
            )


class TypedMetalPreviewTests(unittest.TestCase):
    SELECTION = {
        "head_style_id": "head_classic_4_prong",
        "band_style_id": "band_classic_solitaire",
    }

    def _configure_metal(self, color: str, purity: str, finish: str) -> dict:
        return manual_configurator.configure(
            {
                **payload_for("round"),
                **self.SELECTION,
                "metal": {
                    "color_family": color,
                    "purity": purity,
                    "finish": finish,
                },
            }
        )

    def test_colour_purity_and_finish_are_preview_only_identity_overlays(self):
        reports = {
            "base": self._configure_metal("yellow", "14k", "polished"),
            "colour": self._configure_metal("white", "14k", "polished"),
            "purity": self._configure_metal("yellow", "18k", "polished"),
            "finish": self._configure_metal("yellow", "14k", "brushed"),
        }
        self.assertEqual(
            {report["geometry_identity"] for report in reports.values()},
            {reports["base"]["geometry_identity"]},
        )
        self.assertEqual(len({report["flat_parameters"]["config_id"] for report in reports.values()}), 4)
        self.assertEqual(reports["colour"]["preview"]["metal_family"], "white")
        self.assertEqual(reports["purity"]["preview"]["metal_purity"], "18k")
        self.assertEqual(reports["finish"]["preview"]["metal_finish"], "brushed")

    def test_all_preview_builders_expose_typed_purity_and_finish(self):
        pipeline_report = analyze_photo_request(
            {
                **payload_for("round"),
                "metal": {
                    "color_family": "rose",
                    "purity": "18k",
                    "finish": "matte",
                },
            }
        )
        self.assertEqual(pipeline_report["preview"]["metal_purity"], "18k")
        self.assertEqual(pipeline_report["preview"]["metal_finish"], "matte")

        stone = StoneInputs("round", 6.5, 6.5, 3.9, 6.5, 1.0)
        metal = build_metal_spec("rose", "18k", finish="matte")
        lane_preview = image_lane._preview(
            stone,
            metal,
            {
                "center_setting": "prong",
                "prong_count": 4,
                "band_width_mm": 2.0,
                "band_thickness_mm": 1.6,
            },
        )
        self.assertEqual(lane_preview["metal_purity"], "18k")
        self.assertEqual(lane_preview["metal_finish"], "matte")


if __name__ == "__main__":
    unittest.main()
