import base64
import io
import unittest

from PIL import Image, ImageDraw

from fast_mvp.pipeline import analyze_photo_request
from fast_mvp.preprocessing import preprocess_for_retrieval
from fast_mvp.retrieval import (
    MountingCandidate,
    RetrievalResponse,
)


def _image_bytes(uniform: bool = False) -> bytes:
    image = Image.new("RGB", (320, 240), (245, 240, 230))
    if not uniform:
        draw = ImageDraw.Draw(image)
        draw.ellipse((60, 35, 260, 220), outline=(205, 160, 75), width=18)
        draw.rectangle((135, 70, 185, 125), fill=(235, 248, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(_image_bytes()).decode("ascii")


def _payload() -> dict:
    return {
        "image_data_url": _data_url(),
        "filename": "reference.png",
        "stone_shape": "round",
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "stone_carat_eq": 1.0,
        "ring_size_us": 6.5,
    }


class FakeProvider:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.calls = 0

    def query(self, image, *, stone_shape, top_k=5):
        self.calls += 1
        self.image = image
        self.stone_shape = stone_shape
        return RetrievalResponse(self.candidates, "fake-dinov3")


class FailingProvider:
    def query(self, image, *, stone_shape, top_k=5):
        raise TimeoutError("simulated endpoint timeout")


class ExtractorV2Tests(unittest.TestCase):
    def test_typed_stone_inputs_are_never_modified(self):
        provider = FakeProvider(
            [
                MountingCandidate(
                    "candidate",
                    "bezel",
                    8,
                    "rose",
                    0.40,
                    0.28,
                    0.91,
                )
            ]
        )
        result = analyze_photo_request(_payload(), mounting_provider=provider)

        flat = result["flat_parameters"]
        self.assertEqual(flat["stone_shape"], "round")
        self.assertEqual(flat["stone_length_mm"], 6.5)
        self.assertEqual(flat["stone_width_mm"], 6.5)
        self.assertEqual(flat["stone_depth_mm"], 3.9)
        self.assertEqual(flat["ring_size_us"], 6.5)
        self.assertEqual(flat["prong_count"], 0)
        self.assertEqual(result["extraction"]["provider"], "dinov3_hnsw")

    def test_band_ratios_respect_catalog_floors(self):
        provider = FakeProvider(
            [
                MountingCandidate(
                    "tiny-band",
                    "prong",
                    4,
                    "yellow",
                    0.01,
                    0.01,
                    0.85,
                )
            ]
        )
        result = analyze_photo_request(_payload(), mounting_provider=provider)
        self.assertEqual(result["flat_parameters"]["band_width_mm"], 1.8)
        self.assertEqual(result["flat_parameters"]["band_thickness_mm"], 1.5)
        self.assertTrue(result["validation"]["constraint_accepted"])

    def test_soft_catalog_deviation_remains_selectable(self):
        provider = FakeProvider(
            [
                MountingCandidate(
                    "custom-five-prong-round",
                    "prong",
                    5,
                    "yellow",
                    2.0 / 6.5,
                    1.6 / 6.5,
                    0.96,
                ),
                MountingCandidate(
                    "valid-four-prong-round",
                    "prong",
                    4,
                    "white",
                    2.0 / 6.5,
                    1.6 / 6.5,
                    0.90,
                ),
            ]
        )
        result = analyze_photo_request(_payload(), mounting_provider=provider)
        matches = result["extraction"]["matches"]
        self.assertTrue(matches[0]["constraint_accepted"])
        self.assertTrue(matches[0]["selected"])
        self.assertFalse(matches[1]["selected"])
        # Metal comes from the typed input (here the launch default), never from
        # the retrieved candidate's own metal label.
        self.assertEqual(result["flat_parameters"]["metal_alloy"], "14k_yellow")
        self.assertIn(
            "PRONG-001",
            {warning["rule_id"] for warning in result["validation"]["warnings"]},
        )

    def test_typed_metal_overrides_the_photo_the_filename_and_the_provider(self):
        """Metal is typed: a gold-looking photo named "yellow" cannot change it."""
        provider = FakeProvider(
            [MountingCandidate("cat-1", "prong", 6, "yellow", 2.0 / 6.5, 1.6 / 6.5, 0.94)]
        )
        payload = {
            **_payload(),
            "filename": "yellow-gold-ring.png",
            "metal_color_family": "white",
            "metal_karat": "18k",
        }
        result = analyze_photo_request(payload, mounting_provider=provider)
        # Retrieval still ran — metal is not a mounting override.
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["extraction"]["provider"], "dinov3_hnsw")
        self.assertEqual(result["flat_parameters"]["metal_alloy"], "18k_white")
        self.assertEqual(result["preview"]["metal"]["display_name"], "18k white gold")
        components = result["extraction"]["components"]
        self.assertEqual(components["metal_color_family"]["source"], "typed_input")
        self.assertEqual(components["metal_color_family"]["confidence"], 1.0)

    def test_quote_is_pending_until_geometry_supplies_a_volume(self):
        result = analyze_photo_request(
            {**_payload(), "metal_color_family": "rose", "metal_karat": "14k"}
        )
        self.assertEqual(result["quote"]["status"], "pending_geometry")

    def test_a_request_cannot_supply_its_own_ring_volume(self):
        """Goal 0023: a volume becomes a price, so the caller must not choose it.

        The volume comes from measuring the promoted asset's own export bytes. A
        request that names one is ignored, not honoured.
        """
        priced = analyze_photo_request(
            {
                **_payload(),
                "metal_color_family": "rose",
                "metal_karat": "14k",
                "ring_volume_mm3": 250.0,
                "metal_price_per_gram": 40.0,
            }
        )
        self.assertEqual(priced["quote"]["status"], "pending_geometry")
        self.assertIsNone(priced["quote"]["ring_volume_mm3"])
        self.assertIsNone(priced["quote"]["metal_weight_g"])
        self.assertIsNone(priced["quote"]["metal_content_cost"])

    def test_missing_runpod_credentials_fall_back_without_crashing(self):
        """Goal 0026 changed *why* this falls back, not *that* it does.

        Before the quarantine, an unconfigured deployment reached the retrieval
        provider and fell back only because the credentials happened to be absent.
        Now the paused lane refuses first and says so by name, so the fallback is
        declared rather than accidental. The product outcome is unchanged: the
        typed-shape prior, which reads nothing from the photograph.
        """
        result = analyze_photo_request(_payload())
        self.assertEqual(
            result["extraction"]["provider"], "perception_refused_typed_prior"
        )
        self.assertEqual(result["extraction"]["perception"]["reason"], "lane_paused")
        self.assertIn("Perception lane refused", result["extraction"]["fallback_reason"])
        self.assertTrue(result["validation"]["schema_accepted"])

    def test_acknowledged_lane_still_falls_back_when_runpod_is_unconfigured(self):
        """The original guarantee, on the path where perception is permitted."""
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest import mock

        from fast_mvp import perception_lane

        document = json.loads(
            Path(perception_lane.UNDECLARED_TEMPLATE_PATH).read_text(encoding="utf-8")
        )
        document["status"] = "declared"
        document["declared_by"] = {"name": "Test Operator", "role": "operator"}
        document["validity"] = {"valid_from": "2026-08-01", "expires_on": "2999-01-01"}
        document["accepted_conditions"] = list(perception_lane.STANDING_CONDITIONS)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "ack.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with mock.patch.object(
                perception_lane,
                "decide",
                lambda **kwargs: perception_lane.PerceptionDecision(
                    True, "acknowledged_diagnostic", "test", conditions=()
                ),
            ):
                result = analyze_photo_request(_payload())
        self.assertEqual(result["extraction"]["provider"], "heuristic_fallback")
        self.assertIn("not configured", result["extraction"]["fallback_reason"])
        self.assertTrue(result["validation"]["schema_accepted"])

    def test_timeout_and_empty_results_use_fallback(self):
        timed_out = analyze_photo_request(_payload(), mounting_provider=FailingProvider())
        self.assertEqual(timed_out["extraction"]["provider"], "heuristic_fallback")
        self.assertIn("simulated endpoint timeout", timed_out["extraction"]["fallback_reason"])

        empty = analyze_photo_request(_payload(), mounting_provider=FakeProvider([]))
        self.assertEqual(empty["extraction"]["provider"], "heuristic_fallback")
        self.assertIn("All retrieved candidates", empty["extraction"]["fallback_reason"])

    def test_explicit_mounting_overrides_bypass_provider(self):
        provider = FakeProvider([])
        payload = _payload()
        payload["mounting_overrides"] = {
            "center_setting": "basket",
            "metal_color_family": "white",
            "band_width_mm": 2.3,
            "band_thickness_mm": 1.7,
        }
        result = analyze_photo_request(payload, mounting_provider=provider)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result["extraction"]["provider"], "typed")
        self.assertEqual(result["flat_parameters"]["center_setting"], "basket")
        self.assertEqual(result["flat_parameters"]["prong_count"], 0)

    def test_dimensions_are_required_even_with_legacy_carat_alias(self):
        with self.assertRaisesRegex(ValueError, "stone_length_mm"):
            analyze_photo_request(
                {
                    "shape_hint": "round",
                    "carat": 1.0,
                    "ring_size_us": 6.5,
                }
            )

    def test_preprocessing_is_deterministic_and_masks_background(self):
        first = preprocess_for_retrieval(_image_bytes())
        second = preprocess_for_retrieval(_image_bytes())
        self.assertEqual(first.png_bytes, second.png_bytes)
        self.assertEqual(first.mask_source, "heuristic_object_mask")
        with Image.open(io.BytesIO(first.png_bytes)) as image:
            self.assertEqual(image.size, (256, 256))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))

    def test_uniform_image_uses_center_crop_fallback(self):
        processed = preprocess_for_retrieval(_image_bytes(uniform=True))
        self.assertEqual(processed.mask_source, "center_crop_fallback")


if __name__ == "__main__":
    unittest.main()
