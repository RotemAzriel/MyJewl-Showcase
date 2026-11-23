"""Tests for the Goal 0005 image lane: extractor, index, consensus, abstention, eval.

Hermetic: no Anthropic call, no Azure, no Blender. The Anthropic client is
mocked at the boundary; the catalog index is built from synthetic images.

The tests are keyed to *behaviour that must hold* — the gate is authoritative,
band proportions are scaled by the typed stone, **metal is typed and never
affects matching**, unsupported archetypes abstain, gate verdicts are withheld on
a non-reference backend, the geometry batch always deallocates — not to any
particular empirical threshold, so a re-tiering of the constraint engine or a
policy recalibration does not break them.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import image_extractor, image_lane, typed_inputs, workflow  # noqa: E402
from fast_mvp.catalog_index import CatalogIndex  # noqa: E402
from fast_mvp.embedding import TileStatisticsEmbedder, build_embedder  # noqa: E402
from fast_mvp.pipeline import _parse_stone_inputs  # noqa: E402
from scripts import curate_real_holdout, eval_image_to_config  # noqa: E402
from scripts.operator_fulfill_geometry import (  # noqa: E402
    CommandResult,
    OperatorGeometryBatch,
)

TYPED = {
    "stone_shape": "round",
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "ring_size_us": 6.5,
    "stone_carat_eq": 1.0,
    # Metal is typed, like the stone spec and the ring size.
    "metal_color_family": "white",
    "metal_karat": "18k",
}

OBSERVATION = {
    "archetype": "solitaire",
    "center_setting": "prong",
    "center_setting_confidence": 0.9,
    "prong_count": 6,
    "prong_count_visible": True,
    "prong_count_confidence": 0.8,
    "band_width_ratio": 0.3,
    "band_width_ratio_confidence": 0.6,
    "band_thickness_ratio": 0.25,
    "band_thickness_ratio_confidence": 0.5,
    "observations": "six prongs, cathedral shoulders",
}


def _png(color: tuple[int, int, int], size: int = 160, bar: int = 0) -> bytes:
    """Small synthetic image; ``bar`` adds a stripe so images differ structurally."""
    image = Image.new("RGB", (size, size), color)
    if bar:
        for y in range(size // 2 - bar, size // 2 + bar):
            for x in range(size):
                image.putpixel((x, y), (255 - color[0], 255 - color[1], 255 - color[2]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _fake_response(payload: dict, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class FakeAnthropic:
    """Records the request and replays a canned response."""

    def __init__(self, response):
        self.calls: list[dict] = []
        self._response = response
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class TypedInputContractTests(unittest.TestCase):
    """Metal, stone spec, and ring size are typed — the contract itself."""

    def test_metal_is_read_from_several_spellings(self):
        for payload in (
            {"metal_color_family": "rose", "metal_karat": "18k"},
            {"metal": {"color_family": "rose", "karat": "18k"}},
            {"metal_alloy": "18k_rose"},
            {"metal_alloy": "18K Rose Gold"},
            {"metal_color_family": "rose", "metal_purity": "750"},
            # Legacy client that still sends metal inside mounting_overrides.
            {"mounting_overrides": {"metal_color_family": "rose"}, "metal_karat": "18k"},
        ):
            with self.subTest(payload=payload):
                spec = typed_inputs.parse_metal_spec(payload)
                self.assertEqual(spec.alloy, "18k_rose")
                self.assertEqual(spec.metal_type, "gold")
                self.assertTrue(spec.is_typed)

    def test_absent_metal_is_a_declared_default_not_an_inference(self):
        spec = typed_inputs.parse_metal_spec({})
        self.assertEqual(spec.alloy, typed_inputs.DEFAULT_METAL_ALLOY)
        self.assertEqual(spec.source, "unspecified_default")
        self.assertFalse(spec.is_typed)

    def test_platinum_is_white_and_a_contradiction_is_rejected(self):
        self.assertEqual(
            typed_inputs.parse_metal_spec({"metal_alloy": "pt950"}).color_family, "white"
        )
        with self.assertRaises(typed_inputs.TypedInputError):
            typed_inputs.parse_metal_spec(
                {"metal_color_family": "yellow", "metal_karat": "pt950"}
            )

    def test_certificate_measurements_are_parsed_or_refused(self):
        self.assertEqual(
            typed_inputs.parse_measurements("6.50 - 6.55 x 4.00 mm"), (6.55, 6.5, 4.0)
        )
        self.assertEqual(
            typed_inputs.parse_measurements({"length": 8.0, "width": 6.0, "depth": 3.9}),
            (8.0, 6.0, 3.9),
        )
        self.assertIsNone(typed_inputs.parse_measurements("about six and a half mm"))

    def test_a_typed_dimension_beats_the_certificate(self):
        filled, certificate = typed_inputs.apply_certificate(
            {
                "stone_depth_mm": 4.2,
                "certificate": {
                    "lab": "gia",
                    "report_number": "1",
                    "shape": "round",
                    "measurements_mm": "6.50 - 6.55 x 4.00 mm",
                },
            }
        )
        self.assertEqual(filled["stone_depth_mm"], 4.2)
        self.assertEqual(filled["stone_width_mm"], 6.5)
        self.assertEqual(typed_inputs.stone_spec_source(certificate), "certificate:gia")

    def test_certificate_without_a_lab_or_number_is_refused(self):
        for bad in ({"lab": "acme", "report_number": "1"}, {"lab": "gia"}):
            with self.subTest(bad=bad):
                with self.assertRaises(typed_inputs.TypedInputError):
                    typed_inputs.parse_certificate({"certificate": bad})

    def test_quote_never_invents_a_weight(self):
        spec = typed_inputs.parse_metal_spec({"metal_alloy": "pt950"})
        pending = typed_inputs.quote_basis(spec)
        self.assertEqual(pending["status"], "pending_geometry")
        self.assertIsNone(pending["metal_weight_g"])
        priced = typed_inputs.quote_basis(
            spec, ring_volume_mm3=100.0, metal_price_per_gram=30.0
        )
        self.assertAlmostEqual(priced["metal_weight_g"], 2.07, places=3)
        self.assertAlmostEqual(priced["metal_content_cost"], 62.1, places=2)
        self.assertIn("labor", priced["excludes"])


class ImageExtractorTests(unittest.TestCase):
    def test_parse_observation_rejects_unknown_setting(self):
        bad = {**OBSERVATION, "center_setting": "tension"}
        with self.assertRaises(image_extractor.ProposalFormatError):
            image_extractor.parse_observation(bad)

    def test_parse_observation_rejects_non_positive_ratio(self):
        for field in ("band_width_ratio", "band_thickness_ratio"):
            with self.assertRaises(image_extractor.ProposalFormatError):
                image_extractor.parse_observation({**OBSERVATION, field: 0})

    def test_parse_observation_rejects_a_metal_reading(self):
        with self.assertRaises(image_extractor.ProposalFormatError):
            image_extractor.parse_observation(
                {**OBSERVATION, "metal_color_family": "white"}
            )

    def test_band_proportions_are_scaled_by_the_typed_stone_not_the_image(self):
        stone = _parse_stone_inputs(TYPED)
        metal = typed_inputs.parse_metal_spec(TYPED)
        observation = image_extractor.parse_observation(OBSERVATION)
        mounting = image_extractor.observation_to_mounting(observation, stone, metal)
        self.assertAlmostEqual(mounting.band_width_mm, 0.3 * 6.5, places=6)
        self.assertAlmostEqual(mounting.band_thickness_mm, 0.25 * 6.5, places=6)

    def test_mounting_alloy_is_the_typed_metal(self):
        stone = _parse_stone_inputs(TYPED)
        observation = image_extractor.parse_observation(OBSERVATION)
        mounting = image_extractor.observation_to_mounting(
            observation, stone, typed_inputs.parse_metal_spec(TYPED)
        )
        self.assertEqual(mounting.metal_alloy, "18k_white")

    def test_schema_has_no_typed_input_field(self):
        properties = set(image_extractor.VISION_SCHEMA["properties"])
        for forbidden in (
            "stone_shape",
            "stone_length_mm",
            "carat",
            "ring_size_us",
            "metal_color_family",
            "metal_karat",
        ):
            self.assertNotIn(forbidden, properties)

    def test_schema_reports_both_band_ratios(self):
        properties = set(image_extractor.VISION_SCHEMA["properties"])
        self.assertIn("band_width_ratio", properties)
        self.assertIn("band_thickness_ratio", properties)

    def test_live_call_uses_structured_outputs_and_an_image_block(self):
        client = FakeAnthropic(_fake_response(OBSERVATION))
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((200, 200, 200)), client=client
        )
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["provider"], image_extractor.DEFAULT_VISION_MODEL)
        request = client.calls[0]
        self.assertEqual(
            request["output_config"]["format"]["type"], "json_schema"
        )
        blocks = request["messages"][0]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["type"], "base64")

    def test_refusal_falls_back_offline_rather_than_raising(self):
        client = FakeAnthropic(_fake_response(OBSERVATION, stop_reason="refusal"))
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((200, 200, 200)), client=client
        )
        self.assertEqual(report["provider"], "offline_heuristic")
        self.assertIn("declined", (report["fallback_reason"] or ""))

    def test_unsupported_archetype_abstains_and_emits_no_config(self):
        client = FakeAnthropic(_fake_response({**OBSERVATION, "archetype": "halo"}))
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((200, 200, 200)), client=client
        )
        self.assertEqual(report["status"], "abstained")
        self.assertEqual(report["abstention_reason"], "unsupported_archetype")
        self.assertIsNone(report["flat_parameters"])

    def test_not_a_ring_abstains_with_its_own_reason(self):
        client = FakeAnthropic(_fake_response({**OBSERVATION, "archetype": "not_a_ring"}))
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((10, 10, 10)), client=client
        )
        self.assertEqual(report["abstention_reason"], "not_a_ring")

    def test_offline_path_still_passes_the_live_gate(self):
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((210, 180, 100)), allow_llm=False
        )
        self.assertEqual(report["status"], "accepted")
        self.assertTrue(report["validation"]["schema_accepted"])
        self.assertTrue(report["validation"]["constraint_accepted"])

    def test_offline_floor_does_not_read_metal_from_the_pixels(self):
        """A yellow-looking photo must not override the typed white metal."""
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((230, 190, 90)), allow_llm=False
        )
        self.assertNotIn("metal_color_family", report["observation"])
        self.assertEqual(report["flat_parameters"]["metal_alloy"], "18k_white")

    def test_typed_metal_travels_in_the_report(self):
        report = image_extractor.extract_mounting(
            TYPED, image_bytes=_png((210, 180, 100)), allow_llm=False
        )
        metal = report["typed_inputs"]["metal"]
        self.assertEqual(metal["alloy"], "18k_white")
        self.assertEqual(metal["source"], "typed")
        self.assertEqual(report["typed_inputs"]["stone_spec_source"], "manual_entry")

    def test_a_certificate_supplies_the_typed_stone_spec(self):
        report = image_extractor.extract_mounting(
            {
                "ring_size_us": 6.5,
                "metal_color_family": "rose",
                "metal_karat": "14k",
                "certificate": {
                    "lab": "GIA",
                    "report_number": "2141438171",
                    "shape": "round",
                    "measurements_mm": "6.50 - 6.55 x 4.00 mm",
                    "carat": 1.01,
                },
            },
            image_bytes=_png((210, 180, 100)),
            allow_llm=False,
        )
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["typed_inputs"]["stone_spec_source"], "certificate:gia")
        self.assertEqual(report["flat_parameters"]["stone_width_mm"], 6.5)
        self.assertEqual(report["flat_parameters"]["stone_depth_mm"], 4.0)


class EmbeddingTests(unittest.TestCase):
    def test_descriptor_is_deterministic_and_unit_norm(self):
        embedder = TileStatisticsEmbedder()
        image = _png((120, 90, 60), bar=6)
        first = embedder.embed(image)
        second = embedder.embed(image)
        np.testing.assert_allclose(first, second)
        self.assertAlmostEqual(float(np.linalg.norm(first)), 1.0, places=5)
        self.assertEqual(first.shape[0], embedder.info.dimension)

    def test_fallback_backend_is_not_marked_as_the_reference_paradigm(self):
        self.assertFalse(build_embedder("fallback").info.is_reference_paradigm)

    def test_distinct_images_get_distinct_embeddings(self):
        embedder = TileStatisticsEmbedder()
        left = embedder.embed(_png((240, 200, 120), bar=2))
        right = embedder.embed(_png((40, 60, 200), bar=20))
        self.assertLess(float(left @ right), 0.999)


def _build_index(tmp: Path) -> CatalogIndex:
    """Two round configs (4 vs 6 prong) plus one oval, embedded deterministically.

    Each config's views are rendered in a *different* metal, mirroring the real
    catalog's per-shard metal randomisation — so any test that still passes here
    has proven metal is not part of the match.
    """
    embedder = TileStatisticsEmbedder()
    specs = [
        ("round_prong4", "round", "prong", 4, 0.30, 0.25, (230, 190, 110), 4),
        ("round_prong6", "round", "prong", 6, 0.45, 0.28, (80, 90, 220), 24),
        ("oval_prong4", "oval", "prong", 4, 0.30, 0.25, (120, 220, 120), 12),
    ]
    metals = [("14k_yellow", "yellow"), ("18k_white", "white"), ("14k_rose", "rose")]
    vectors = []
    records = []
    for config_id, shape, setting, prongs, ratio, thickness, color, bar in specs:
        for view in range(3):
            image = _png(color, bar=bar + view)
            alloy, family = metals[view]
            vectors.append(embedder.embed(image))
            records.append(
                {
                    "config_id": config_id,
                    "image_path": f"{config_id}/view_{view}.png",
                    "split": "catalog",
                    "view_index": view,
                    "stone_shape": shape,
                    "stone_length_mm": 6.5,
                    "stone_width_mm": 6.5,
                    "stone_depth_mm": 3.9,
                    "ring_size_us": 6.5,
                    "center_setting": setting,
                    "prong_count": prongs,
                    "band_width_ratio": ratio,
                    "band_thickness_ratio": thickness,
                    # Render provenance only — never a retrieval axis.
                    "metal_alloy": alloy,
                    "metal_color_family": family,
                }
            )
    return CatalogIndex(np.vstack(vectors), records, {"embedding": embedder.info.to_json()})


class CatalogIndexTests(unittest.TestCase):
    def test_query_never_crosses_the_typed_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            embedder = TileStatisticsEmbedder()
            vector = embedder.embed(_png((120, 220, 120), bar=12))
            neighbors = index.query(vector, stone_shape="round", top_k=9)
            self.assertTrue(neighbors)
            self.assertTrue(
                all(item.record["stone_shape"] == "round" for item in neighbors)
            )

    def test_unknown_shape_returns_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            vector = TileStatisticsEmbedder().embed(_png((1, 2, 3)))
            self.assertEqual(index.query(vector, stone_shape="marquise"), [])

    def test_save_load_roundtrip_preserves_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            index.save(Path(tmp) / "idx")
            reloaded = CatalogIndex.load(Path(tmp) / "idx")
            np.testing.assert_allclose(index.embeddings, reloaded.embeddings)
            self.assertEqual(index.records, reloaded.records)

    def test_metadata_row_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            CatalogIndex(np.zeros((3, 4), dtype=np.float32), [{"a": 1}], {})

    def test_reported_mounting_excludes_metal_and_keeps_it_as_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            vector = TileStatisticsEmbedder().embed(_png((230, 190, 110), bar=4))
            neighbor = index.query(vector, stone_shape="round", top_k=1)[0].to_json()
            self.assertNotIn("metal_color_family", neighbor["mounting"])
            self.assertIn("band_thickness_ratio", neighbor["mounting"])
            self.assertIn("metal_color_family", neighbor["render_provenance"])


class ImageLaneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.index = _build_index(Path(self._tmp.name))
        self.embedder = TileStatisticsEmbedder()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, image: bytes, **kwargs):
        return image_lane.propose_from_image(
            TYPED,
            index=self.index,
            embedder=self.embedder,
            images=[image],
            **kwargs,
        )

    # The fixture holds only three views per config, so a top-5 query
    # necessarily spans two different round configs and the lane rightly asks
    # for another view. K=3 is the policy knob that isolates one config's views,
    # which is what the accept path needs to be exercised.
    TIGHT = image_lane.AbstentionPolicy(top_k=3)

    def test_exact_catalog_view_is_accepted_and_gate_validated(self):
        report = self._run(_png((230, 190, 110), bar=4), policy=self.TIGHT)
        self.assertEqual(report["status"], image_lane.STATUS_ACCEPTED)
        self.assertTrue(report["validation"]["constraint_accepted"])
        self.assertTrue(report["validation"]["schema_accepted"])
        self.assertEqual(report["flat_parameters"]["prong_count"], 4)

    def test_accepted_band_proportions_are_scaled_by_the_typed_stone(self):
        report = self._run(_png((230, 190, 110), bar=4), policy=self.TIGHT)
        width_ratio = report["attributes"]["band_width_ratio"]["value"]
        thickness_ratio = report["attributes"]["band_thickness_ratio"]["value"]
        self.assertAlmostEqual(
            report["flat_parameters"]["band_width_mm"], round(width_ratio * 6.5, 2), places=2
        )
        self.assertAlmostEqual(
            report["flat_parameters"]["band_thickness_mm"],
            round(thickness_ratio * 6.5, 2),
            places=2,
        )

    def test_the_lane_infers_only_the_internal_mounting_axes(self):
        report = self._run(_png((230, 190, 110), bar=4), policy=self.TIGHT)
        self.assertEqual(
            set(report["attributes"]),
            {"center_setting", "prong_count", "band_width_ratio", "band_thickness_ratio"},
        )
        self.assertTrue(
            all(source == "retrieval_consensus" for source in report["attribute_sources"].values())
        )
        self.assertNotIn("metal_color_family", report["attributes"])
        self.assertIn("metal_color_family", report["typed_only_axes"])
        self.assertEqual(report["catalog_scope"], "myjewl_internal_validated_catalog")

    def test_metal_does_not_affect_matching_but_does_reach_the_config(self):
        """Same photo, two typed metals: identical match, different alloy."""
        image = _png((230, 190, 110), bar=4)
        white = self._run(image, policy=self.TIGHT)
        rose = image_lane.propose_from_image(
            {**TYPED, "metal_color_family": "rose", "metal_karat": "14k"},
            index=self.index,
            embedder=self.embedder,
            images=[image],
            policy=self.TIGHT,
        )
        self.assertEqual(white["top_k"], rose["top_k"])
        self.assertEqual(white["attributes"], rose["attributes"])
        self.assertEqual(white["flat_parameters"]["metal_alloy"], "18k_white")
        self.assertEqual(rose["flat_parameters"]["metal_alloy"], "14k_rose")
        self.assertEqual(rose["flat_parameters"]["metal_type"], "gold")

    def test_typed_metal_reaches_preview_and_quote(self):
        report = self._run(_png((230, 190, 110), bar=4), policy=self.TIGHT)
        self.assertEqual(report["preview"]["metal"]["alloy"], "18k_white")
        self.assertEqual(report["preview"]["metal_family"], "white")
        # No geometry yet, so no volume, so no weight — stated, not guessed.
        self.assertEqual(report["quote"]["status"], "pending_geometry")
        self.assertIsNone(report["quote"]["metal_weight_g"])

    def test_a_request_cannot_supply_its_own_ring_volume(self):
        """Goal 0023: the image lane never accepts a volume from the request.

        The volume is measured from the promoted asset's export bytes and resolved
        against the preview manifest. A caller-supplied figure would price a ring
        from a number nobody measured — worse than the substitution Goal 0014
        closed, because it is fabricated rather than merely adjacent.
        """
        report = image_lane.propose_from_image(
            {**TYPED, "ring_volume_mm3": 200.0, "metal_price_per_gram": 60.0},
            index=self.index,
            embedder=self.embedder,
            images=[_png((230, 190, 110), bar=4)],
            policy=self.TIGHT,
        )
        quote = report["quote"]
        self.assertEqual(quote["status"], "pending_geometry")
        self.assertIsNone(quote["ring_volume_mm3"])
        self.assertIsNone(quote["metal_weight_g"])
        self.assertIsNone(quote["metal_content_cost"])
        # The spot price is a commercial input the caller does own; it survives.
        self.assertEqual(quote["metal_price_per_gram"], 60.0)

    def test_out_of_distribution_abstains_instead_of_matching(self):
        policy = image_lane.AbstentionPolicy(min_top1_similarity=1.01)
        report = self._run(_png((230, 190, 110), bar=4), policy=policy)
        self.assertEqual(report["status"], image_lane.STATUS_ABSTAINED)
        self.assertEqual(report["abstention_reason"], "out_of_distribution")
        self.assertIsNone(report["flat_parameters"])
        self.assertIn("not a match", report["message"])

    def test_ambiguous_setting_offers_a_choice(self):
        policy = image_lane.AbstentionPolicy(
            min_top1_similarity=0.0, min_setting_agreement=1.01
        )
        report = self._run(_png((230, 190, 110), bar=4), policy=policy)
        self.assertEqual(report["status"], image_lane.STATUS_NEEDS_CHOICE)
        self.assertTrue(report["choices"])
        self.assertIsNone(report["flat_parameters"])

    def test_split_attribute_requests_a_discriminating_view(self):
        policy = image_lane.AbstentionPolicy(
            min_top1_similarity=0.0,
            min_setting_agreement=0.0,
            min_attribute_agreement=1.01,
        )
        report = self._run(_png((230, 190, 110), bar=4), policy=policy)
        self.assertEqual(report["status"], image_lane.STATUS_NEEDS_VIEW)
        self.assertTrue(report["requested_view"])
        self.assertIsNone(report["flat_parameters"])

    def test_unsupported_archetype_hint_abstains_before_retrieval(self):
        report = self._run(_png((230, 190, 110), bar=4), archetype_hint="halo")
        self.assertEqual(report["status"], image_lane.STATUS_ABSTAINED)
        self.assertEqual(report["abstention_reason"], "unsupported_archetype")

    def test_shape_without_catalog_geometry_abstains(self):
        report = image_lane.propose_from_image(
            {**TYPED, "stone_shape": "pear"},
            index=self.index,
            embedder=self.embedder,
            images=[_png((230, 190, 110), bar=4)],
        )
        self.assertEqual(report["status"], image_lane.STATUS_ABSTAINED)
        self.assertEqual(report["abstention_reason"], "empty_catalog_for_shape")

    def test_no_image_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            image_lane.propose_from_image(
                TYPED, index=self.index, embedder=self.embedder, images=[]
            )

    def test_consensus_weights_by_similarity(self):
        from fast_mvp.catalog_index import Neighbor

        neighbors = [
            Neighbor(0, 0.9, {"center_setting": "prong", "prong_count": 6}),
            Neighbor(1, 0.1, {"center_setting": "bezel", "prong_count": 0}),
        ]
        consensus = image_lane.consensus_from_neighbors(neighbors)
        self.assertEqual(consensus["center_setting"].value, "prong")
        self.assertGreater(consensus["center_setting"].agreement, 0.8)


class ImageLanePersistenceTests(unittest.TestCase):
    def test_accepted_image_proposal_persists_the_query_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            image = _png((230, 190, 110), bar=4)
            report = image_lane.propose_from_image(
                TYPED,
                index=index,
                embedder=TileStatisticsEmbedder(),
                images=[image],
                policy=image_lane.AbstentionPolicy(top_k=3),
            )
            self.assertEqual(report["status"], image_lane.STATUS_ACCEPTED)
            runs = Path(tmp) / "runs"
            persisted = workflow.persist_image_proposal(report, runs, [image])
            self.assertEqual(persisted["query_images"], ["query_01.png"])
            self.assertTrue(
                (runs / persisted["job_id"] / "query" / "query_01.png").is_file()
            )

            review = workflow.review_index(runs)
            entry = review["runs"][0]
            self.assertEqual(entry["lane"], "image")
            self.assertTrue(entry["query_images"])
            self.assertIsNotNone(entry["image_lane"]["top_k"])
            self.assertNotIn(
                "metal_color_family", entry["image_lane"]["attribute_sources"]
            )
            # Typed metal and its quote are what /review shows for metal.
            self.assertEqual(entry["typed_inputs"]["metal"]["alloy"], "18k_white")
            self.assertEqual(entry["quote"]["status"], "pending_geometry")


class EvalHarnessTests(unittest.TestCase):
    def test_gate_verdict_is_withheld_on_a_non_reference_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = _build_index(Path(tmp))
            index.save(Path(tmp) / "idx")
            report = eval_image_to_config.evaluate(
                index_dir=Path(tmp) / "idx",
                render_manifest=None,
                holdout=None,
                backend="fallback",
                policy=image_lane.AbstentionPolicy(),
                sweep=False,
            )
            verdicts = {gate["gate"]: gate["verdict"] for gate in report["gates"]}
            self.assertEqual(verdicts["G-B"], "not_measurable")
            self.assertEqual(verdicts["G-C"], "not_measurable")
            self.assertEqual(verdicts["G-E"], "not_measurable")
            self.assertEqual(verdicts["G-A"], "recorded")
            self.assertFalse(report["reference_paradigm"])
            self.assertEqual(report["status"], "incomplete")

    def test_metal_is_not_a_gate_metric(self):
        metrics = eval_image_to_config.score([], kind="synthetic")
        self.assertNotIn("metal_color_top1", metrics["attributes"])
        self.assertIn("band_thickness_ratio_mae", metrics["attributes"])
        self.assertNotIn("metal_color_family", metrics["scored_axes"])
        self.assertIn("metal_color_family", metrics["unscored_typed_inputs"])
        for gate in eval_image_to_config.GATE_BARS.values():
            self.assertNotIn("metal_color_top1", gate)

    def test_retrieval_correctness_ignores_the_rendered_metal(self):
        """A right mounting in the wrong metal is still a right retrieval."""
        self.assertTrue(
            eval_image_to_config._attributes_match(
                {"center_setting": "prong", "prong_count": 6, "metal_color_family": "rose"},
                {"center_setting": "prong", "prong_count": 6},
            )
        )

    def test_typed_metal_passthrough_is_measured_on_accepted_configs(self):
        outcomes = [
            {
                "supported": True,
                "labels": {},
                "truth_config_id": None,
                "report": {
                    "status": image_lane.STATUS_ACCEPTED,
                    "top_k": [],
                    "attributes": {},
                    "typed_inputs": {"metal": {"alloy": "18k_white"}},
                    "flat_parameters": {"metal_alloy": "18k_white"},
                    "validation": {"schema_accepted": True, "constraint_accepted": True},
                },
            },
            {
                "supported": True,
                "labels": {},
                "truth_config_id": None,
                "report": {
                    "status": image_lane.STATUS_ACCEPTED,
                    "top_k": [],
                    "attributes": {},
                    "typed_inputs": {"metal": {"alloy": "18k_white"}},
                    "flat_parameters": {"metal_alloy": "14k_yellow"},
                    "validation": {"schema_accepted": True, "constraint_accepted": True},
                },
            },
        ]
        metrics = eval_image_to_config.score(outcomes, kind="synthetic")
        self.assertEqual(metrics["validity"]["typed_metal_passthrough"], 0.5)

    def test_scoring_counts_abstentions_as_uncovered_not_wrong(self):
        outcomes = [
            {
                "supported": True,
                "labels": {"center_setting": "prong", "prong_count": 6},
                "truth_config_id": "a",
                "prong_count_visible": True,
                "report": {
                    "status": image_lane.STATUS_ACCEPTED,
                    "top_k": [{"config_id": "a", "mounting": {}}],
                    "attributes": {
                        "center_setting": {"value": "prong", "agreement": 1.0},
                        "prong_count": {"value": 6, "agreement": 1.0},
                    },
                    "validation": {"schema_accepted": True, "constraint_accepted": True},
                },
            },
            {
                "supported": True,
                "labels": {"center_setting": "prong", "prong_count": 6},
                "truth_config_id": "b",
                "prong_count_visible": True,
                "report": {
                    "status": image_lane.STATUS_ABSTAINED,
                    "top_k": [{"config_id": "zz", "mounting": {}}],
                    "attributes": {
                        "center_setting": {"value": "bezel", "agreement": 0.4},
                    },
                },
            },
        ]
        metrics = eval_image_to_config.score(outcomes, kind="synthetic")
        self.assertEqual(metrics["abstention"]["coverage"], 0.5)
        # The wrong answer was abstained on, so accepted-precision stays perfect.
        self.assertEqual(metrics["abstention"]["accepted_setting_precision"], 1.0)
        self.assertEqual(metrics["attributes"]["setting_top1"], 0.5)
        self.assertEqual(metrics["validity"]["gate_pass_rate"], 1.0)

    def test_ood_rejection_counts_non_accepted_distractors(self):
        outcomes = [
            {"supported": False, "labels": {}, "report": {"status": image_lane.STATUS_ABSTAINED,
                                                          "top_k": [], "attributes": {}}},
            {"supported": False, "labels": {}, "report": {"status": image_lane.STATUS_ACCEPTED,
                                                          "top_k": [], "attributes": {}}},
        ]
        metrics = eval_image_to_config.score(outcomes, kind="real")
        self.assertEqual(metrics["abstention"]["ood_rejection"], 0.5)


class HoldoutCurationTests(unittest.TestCase):
    def _write(self, tmp: Path, items: list[dict]) -> Path:
        image = tmp / "ring.png"
        image.write_bytes(_png((200, 200, 200), size=256))
        manifest = tmp / "holdout_manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")
        return manifest

    def _item(self, **overrides) -> dict:
        item = {
            "item_id": "vendor-1",
            "image_path": "ring.png",
            "archetype": "solitaire",
            # Metal lives here, with the stone spec and the ring size.
            "typed": dict(TYPED),
            "labels": {
                "center_setting": "prong",
                "prong_count": 6,
            },
            "prong_count_visible": True,
            "sources": [
                {
                    "url": "https://vendor-a.example/p/1",
                    "retrieved_utc": "2026-07-25T00:00:00Z",
                    "fields": ["center_setting", "prong_count", "archetype"],
                },
                {
                    "url": "https://vendor-b.example/p/1",
                    "retrieved_utc": "2026-07-25T00:00:00Z",
                    "fields": ["center_setting", "prong_count", "archetype"],
                },
            ],
        }
        item.update(overrides)
        return item

    def test_two_source_labels_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write(Path(tmp), [self._item()])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["counts"]["supported_solitaire"], 1)
            self.assertEqual(report["items"][0]["typed"]["metal_alloy"], "18k_white")

    def test_metal_as_a_mounting_label_is_a_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item()
            item["labels"]["metal_color_family"] = "white"
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(
                any("typed input" in defect for defect in report["items"][0]["defects"])
            )

    def test_missing_typed_metal_is_a_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            typed = {
                key: value
                for key, value in TYPED.items()
                if not key.startswith("metal_")
            }
            manifest = self._write(Path(tmp), [self._item(typed=typed)])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(
                any("metal" in defect for defect in report["items"][0]["defects"])
            )

    def test_metal_needs_no_two_source_backing(self):
        """Metal is typed, so it is never held to the label evidence bar."""
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item()
            item["sources"][1]["fields"] = ["center_setting", "prong_count", "archetype"]
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "pass")

    def test_a_certificate_is_accepted_as_the_typed_stone_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item(
                typed={
                    "ring_size_us": 6.5,
                    "metal_color_family": "white",
                    "metal_karat": "18k",
                    "certificate": {
                        "lab": "IGI",
                        "report_number": "LG12345678",
                        "shape": "oval",
                        "measurements_mm": "8.01 x 5.98 x 3.92 mm",
                        "carat": 1.5,
                    },
                }
            )
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "pass")
            typed = report["items"][0]["typed"]
            self.assertEqual(typed["stone_spec_source"], "certificate:igi")
            self.assertEqual(typed["stone_length_mm"], 8.01)

    def test_single_source_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item()
            item["sources"] = item["sources"][:1]
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(
                any("independent source" in defect for defect in report["items"][0]["defects"])
            )

    def test_same_origin_twice_is_not_two_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item()
            item["sources"][1]["url"] = "https://www.vendor-a.example/p/1-alt"
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")

    def test_distractor_must_not_carry_mounting_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = self._item(item_id="halo-1", archetype="halo")
            manifest = self._write(Path(tmp), [item])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(
                any("must not carry" in defect for defect in report["items"][0]["defects"])
            )

    def test_under_strength_holdout_warns_and_is_not_gate_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write(Path(tmp), [self._item()])
            report = curate_real_holdout.curate(manifest)
            self.assertFalse(report["gate_ready"])
            self.assertTrue(report["warnings"])

    def test_missing_image_is_a_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write(Path(tmp), [self._item(image_path="nope.png")])
            report = curate_real_holdout.curate(manifest)
            self.assertEqual(report["status"], "fail")


class OperatorGeometryBatchTests(unittest.TestCase):
    def _batch(self, tmp: Path, responses: dict[str, int] | None = None):
        responses = responses or {}
        calls: list[list[str]] = []

        def runner(args, **kwargs):
            calls.append(list(args))
            label = " ".join(args[:3])
            code = responses.get(label, 0)
            stdout = "VM deallocated" if "show" in args else ""
            return CommandResult(args=args, returncode=code, stdout=stdout)

        config_dir = tmp / "configs" / "cfg_a"
        config_dir.mkdir(parents=True)
        (config_dir / "parameters.json").write_text(
            json.dumps(
                {
                    "config_id": "cfg_a",
                    "archetype": "solitaire",
                    "stone_shape": "round",
                    "stone_length_mm": 6.5,
                    "stone_width_mm": 6.5,
                    "stone_depth_mm": 3.9,
                    "center_setting": "prong",
                    "prong_count": 6,
                    "metal_type": "gold",
                    "metal_alloy": "14k_yellow",
                    "band_width_mm": 2.0,
                    "band_thickness_mm": 1.6,
                    "ring_size_us": 6.5,
                }
            ),
            encoding="utf-8",
        )
        batch = OperatorGeometryBatch(
            config_dir=tmp / "configs", work_dir=tmp / "work", runner=runner
        )
        return batch, calls

    def test_successful_batch_deallocates_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, calls = self._batch(Path(tmp))
            report = batch.run(
                storage_account="tmpacct", container="exports", sas_url="https://blob/x?sig=y"
            )
            self.assertEqual(report["status"], "solved")
            self.assertTrue(report["teardown"]["confirmed_deallocated"])
            self.assertTrue(any("deallocate" in " ".join(call) for call in calls))

    def test_failure_still_deallocates(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, calls = self._batch(Path(tmp), responses={"az vm start": 1})
            report = batch.run(
                storage_account="tmpacct", container="exports", sas_url="https://blob/x?sig=y"
            )
            self.assertEqual(report["status"], "fail")
            self.assertIn("az vm start failed", report["error"])
            self.assertTrue(report["teardown"]["confirmed_deallocated"])

    def test_credentials_never_appear_in_command_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, calls = self._batch(Path(tmp))
            batch.run(
                storage_account="tmpacct", container="exports", sas_url="https://blob/x?sig=y"
            )
            flattened = " ".join(" ".join(call) for call in calls)
            self.assertNotIn("RhinoComputeKey", flattened)
            # The key is read from the VM's machine environment, not passed in.
            self.assertIn("GetEnvironmentVariable('RHINO_COMPUTE_API_KEY','Machine')", flattened)

    def test_constraint_rejected_config_never_reaches_azure(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, calls = self._batch(Path(tmp))
            bad = Path(tmp) / "configs" / "cfg_bad"
            bad.mkdir(parents=True)
            (bad / "parameters.json").write_text(
                json.dumps(
                    {
                        "config_id": "cfg_bad",
                        "archetype": "solitaire",
                        "stone_shape": "round",
                        "stone_length_mm": 6.5,
                        "stone_width_mm": 6.5,
                        "stone_depth_mm": 3.9,
                        "center_setting": "bezel",
                        "prong_count": 6,
                        "metal_type": "gold",
                        "metal_alloy": "14k_yellow",
                        "band_width_mm": 2.0,
                        "band_thickness_mm": 1.6,
                        "ring_size_us": 6.5,
                    }
                ),
                encoding="utf-8",
            )
            configs = batch.load_configs()
            self.assertEqual([config.config_id for config in configs], ["cfg_a"])
            self.assertEqual(batch.ledger["cfg_bad"]["status"], "failed")
            self.assertEqual(batch.ledger["cfg_bad"]["stage"], "constraint")

    def test_checkpoint_makes_a_completed_config_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, _ = self._batch(Path(tmp))
            batch.record("cfg_a", "completed")
            report = batch.run(
                storage_account="tmpacct", container="exports", sas_url="https://blob/x?sig=y"
            )
            self.assertEqual(report["status"], "noop")

    def test_differential_gate_cannot_pass_with_one_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, _ = self._batch(Path(tmp))
            result = batch.run_differential([Path(tmp)])
            self.assertEqual(result["status"], "not_measurable")


if __name__ == "__main__":
    unittest.main()
