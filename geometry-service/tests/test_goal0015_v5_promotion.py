"""Goal 0015 — promotion of the parametric V5 master into the product.

These tests hold the promotion honest in both directions. The V5 master really is
parametric, so it may back more than one configuration; but only three
configurations were actually solved on Azure, so only three may be served, and
nothing between them may be approximated from a neighbouring solve.
"""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import unittest
from pathlib import Path

from fast_mvp import manual_configurator
from fast_mvp.preview_resolution import (
    MANUFACTURING_STATE,
    PreviewRegistryError,
    geometry_signature,
    load_registry,
    resolve_preview,
    validate_registry,
)
from scripts.promotion_gate import evaluate

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "fast_mvp" / "preview_assets" / "registry_v0_1.json"
V5_DEFINITION = ROOT / "manual" / "rhino_compute_bridge" / "goal0014_parametric_master_v5.ghx"
V5_ASSET_DIR = ROOT / "fast_mvp" / "static" / "assets" / "goal0014_v5"

V5_HEAD_ID = "head_v5_parametric_petal_4_prong"
V5_BAND_ID = "band_v5_parametric_split_solitaire"
V5_TEMPLATE_ID = "goal0014-parametric-round-solitaire-master-v5"
V4_ASSET_ID = "goal0011-v4-full-ring"

#: (configuration_id, length, width, depth, ring size) — the solved batch.
SOLVED = (
    ("us4_round5", 5.0, 5.0, 3.0, 4.0),
    ("us6p5_round6p5", 6.5, 6.5, 3.9, 6.5),
    ("us9_round8", 8.0, 8.0, 4.8, 9.0),
)

#: Configurations inside the master's envelope that were never solved. Each is a
#: plausible customer request the product must refuse rather than approximate.
UNSOLVED = (
    ("one ring size below a solve", 5.0, 5.0, 3.0, 3.5),
    ("between two solves", 6.5, 6.5, 3.9, 7.0),
    ("solved size, unsolved stone", 6.0, 6.0, 3.6, 6.5),
    ("solved stone, unsolved size", 8.0, 8.0, 4.8, 8.5),
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


def asset_by_id(registry, asset_id):
    return next(item for item in registry["assets"] if item["asset_id"] == asset_id)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V5RegistryPromotionTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(REGISTRY_PATH, production=True)

    def test_production_registry_promotes_three_v5_assets_beside_v4(self):
        ids = {item["asset_id"] for item in self.registry["assets"]}
        self.assertEqual(
            ids,
            {V4_ASSET_ID}
            | {f"goal0014-v5-{configuration_id}" for configuration_id, *_ in SOLVED},
        )

    @unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
    def test_every_v5_asset_declares_a_passing_hashed_config_differential(self):
        for configuration_id, *_ in SOLVED:
            with self.subTest(configuration=configuration_id):
                asset = asset_by_id(self.registry, f"goal0014-v5-{configuration_id}")
                self.assertEqual(
                    asset["provenance"]["definition_kind"], "parametric_master"
                )
                differential = asset["approval"]["config_differential"]
                self.assertEqual(differential["status"], "passed")
                self.assertGreaterEqual(differential["config_count"], 3)
                report = ROOT / differential["report_path"]
                self.assertTrue(report.is_file())
                self.assertEqual(
                    sha256_file(report), differential["report_sha256"]
                )
                self.assertEqual(
                    json.loads(report.read_text(encoding="utf-8"))["status"], "pass"
                )

    def test_registry_definition_hash_matches_the_definition_on_disk(self):
        for configuration_id, *_ in SOLVED:
            with self.subTest(configuration=configuration_id):
                provenance = asset_by_id(
                    self.registry, f"goal0014-v5-{configuration_id}"
                )["provenance"]
                self.assertEqual(
                    provenance["definition_path"],
                    "manual/rhino_compute_bridge/goal0014_parametric_master_v5.ghx",
                )
                self.assertEqual(
                    provenance["definition_sha256"], sha256_file(V5_DEFINITION)
                )
                self.assertEqual(provenance["template_id"], V5_TEMPLATE_ID)

    @unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
    def test_the_promotion_gate_passes_for_every_promoted_v5_asset(self):
        for configuration_id, *_ in SOLVED:
            with self.subTest(configuration=configuration_id):
                verdict = evaluate(
                    V5_DEFINITION,
                    asset_by_id(self.registry, f"goal0014-v5-{configuration_id}"),
                )
                self.assertEqual(verdict["status"], "pass")
                self.assertEqual(
                    verdict["checks"]["parameter_derivation"]["status"], "pass"
                )
                self.assertEqual(
                    verdict["checks"]["config_differential"]["status"], "pass"
                )

    def test_each_v5_asset_ships_its_own_distinct_receipt_backed_geometry(self):
        receipts, contents, signatures = set(), set(), set()
        for configuration_id, *_ in SOLVED:
            asset = asset_by_id(self.registry, f"goal0014-v5-{configuration_id}")
            receipts.add(asset["provenance"]["source_receipt_sha256"])
            contents.add(asset["delivery"]["content"]["sha256"])
            signatures.add(asset["match"]["geometry_signature"])
        # A frozen master would collapse all three of these to one value; that is
        # exactly the Goal 0014 defect this promotion is allowed to exist because
        # it no longer has.
        self.assertEqual(len(receipts), 3)
        self.assertEqual(len(contents), 3)
        self.assertEqual(len(signatures), 3)

    def test_no_production_asset_approximates_anything(self):
        for asset in self.registry["assets"]:
            with self.subTest(asset=asset["asset_id"]):
                self.assertEqual(asset["classification"], "exact")
                self.assertEqual(asset["match"]["policy"], "discrete_signature")
                self.assertEqual(asset["approximated_parameters"], [])
                self.assertEqual(asset["match"]["approved_ranges"], [])
                self.assertEqual(asset["unsupported_parameters"], [])

    def test_promotion_certifies_nothing_about_manufacturing(self):
        for asset in self.registry["assets"]:
            with self.subTest(asset=asset["asset_id"]):
                self.assertFalse(asset["manufacturing_authority"])
                self.assertEqual(asset["manufacturing_state"], MANUFACTURING_STATE)


class V5StudioResolutionTests(unittest.TestCase):
    def test_every_solved_configuration_resolves_to_its_own_v5_asset(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        for configuration_id, length, width, depth, ring_size in SOLVED:
            with self.subTest(configuration=configuration_id):
                report = v5_report(length, width, depth, ring_size)
                self.assertEqual(report["status"], "accepted")
                manifest = report["preview_manifest"]
                self.assertEqual(manifest["preview_status"], "exact")
                self.assertEqual(manifest["reason_code"], "approved_exact_asset")
                selected = manifest["selected_asset"]
                self.assertEqual(
                    selected["asset_id"], f"goal0014-v5-{configuration_id}"
                )
                self.assertEqual(selected["template_id"], V5_TEMPLATE_ID)
                self.assertEqual(selected["part_count"], 20)
                self.assertEqual(manifest["approximated_parameters"], [])
                self.assertFalse(manifest["manufacturing_authority"])
                self.assertEqual(manifest["manufacturing_state"], MANUFACTURING_STATE)
                expected = asset_by_id(
                    registry, f"goal0014-v5-{configuration_id}"
                )["match"]["geometry_signature"]
                self.assertEqual(manifest["geometry"]["signature"], expected)

    def test_registry_signatures_are_the_signatures_the_studio_actually_produces(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        for configuration_id, length, width, depth, ring_size in SOLVED:
            with self.subTest(configuration=configuration_id):
                report = v5_report(length, width, depth, ring_size)
                projection, signature = geometry_signature(
                    report["canonical_spec"], report["flat_parameters"]
                )
                asset = asset_by_id(registry, f"goal0014-v5-{configuration_id}")
                self.assertEqual(asset["match"]["geometry_projection"], projection)
                self.assertEqual(asset["match"]["geometry_signature"], signature)

    def test_unsolved_configurations_are_offered_made_to_order_never_substituted(self):
        # Goal 0015 refused these outright; Goal 0016 deliberately moved the
        # boundary to the master's own envelope. What must not change is the
        # claim: an unsolved configuration still resolves to no preview asset at
        # all. It is now *expressible and fulfillable* rather than lost.
        for label, length, width, depth, ring_size in UNSOLVED:
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
                report = v5_report(length, width, depth, ring_size)
                self.assertEqual(report["status"], "accepted")
                self.assertEqual(report["preview_manifest"]["preview_status"], "unavailable")
                self.assertIsNone(report["preview_manifest"]["selected_asset"])
                self.assertEqual(
                    report["fulfillment"]["availability"],
                    manual_configurator.MADE_TO_ORDER,
                )
                self.assertEqual(
                    report["fulfillment"]["geometry_request"]["solve_status"],
                    "not_solved",
                )

    def test_an_unsolved_configuration_forced_past_the_catalog_is_unavailable(self):
        # Bypass the catalog entirely: take a solved run's accepted output and
        # move only the ring size. The resolver must not stretch the neighbouring
        # solve over the gap.
        report = v5_report(6.5, 6.5, 3.9, 6.5)
        canonical = copy.deepcopy(report["canonical_spec"])
        canonical["ring"]["size"] = 7.0
        manifest = resolve_preview(canonical, report["flat_parameters"])
        self.assertEqual(manifest["preview_status"], "unavailable")
        self.assertEqual(manifest["reason_code"], "no_approved_preview_asset")
        self.assertIsNone(manifest["selected_asset"])

    def test_the_v5_pair_cannot_be_split_across_other_catalog_styles(self):
        base = payload(6.5, 6.5, 3.9, 6.5)
        with self.assertRaisesRegex(ValueError, "not compatible"):
            manual_configurator.configure(
                {**base, "head_style_id": V5_HEAD_ID, "band_style_id": "band_classic_solitaire"}
            )
        with self.assertRaisesRegex(ValueError, "not compatible"):
            manual_configurator.configure(
                {
                    **base,
                    "head_style_id": "head_classic_4_prong",
                    "band_style_id": V5_BAND_ID,
                }
            )

    def test_catalog_receipt_hashes_match_the_promoted_registry_assets(self):
        registry = load_registry(REGISTRY_PATH, production=True)
        solved_set = manual_configurator.solved_configurations(V5_TEMPLATE_ID)
        self.assertEqual(len(solved_set), 3)
        for solved in solved_set:
            with self.subTest(configuration=solved.configuration_id):
                asset = asset_by_id(registry, f"goal0014-v5-{solved.configuration_id}")
                self.assertEqual(
                    asset["provenance"]["source_receipt_sha256"], solved.receipt_sha256
                )


class V5PromotionPolicyAdversarialTests(unittest.TestCase):
    def mutated(self, mutate):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        mutate(asset_by_id(registry, "goal0014-v5-us6p5_round6p5"))
        return registry

    def test_a_v5_asset_cannot_be_widened_into_a_bounded_projection(self):
        def mutate(asset):
            asset["classification"] = "estimated"
            asset["match"]["policy"] = "bounded_projection"
            asset["match"]["approved_ranges"] = [
                {"path": "/ring/size", "minimum": 4.0, "maximum": 9.0}
            ]
            asset["approximated_parameters"] = [
                {"path": "/ring/size", "reason": "interpolated between solved sizes"}
            ]
            asset["represented_parameters"] = [
                path for path in asset["represented_parameters"] if path != "/ring/size"
            ]

        registry = self.mutated(mutate)
        # The Goal 0014 policy deliberately *permits* a parametric master to back
        # a bounded projection, so this stays structurally well-formed off
        # production — the policy is not weakened here. What blocks it is the
        # product rule: production serves no estimated preview at all.
        validate_registry(registry, production=False)
        with self.assertRaisesRegex(PreviewRegistryError, "zero estimated assets"):
            validate_registry(registry, production=True)

    def test_a_v5_asset_cannot_be_promoted_with_a_failing_differential(self):
        for status in ("absent", "passed"):
            with self.subTest(status=status):
                def mutate(asset, status=status):
                    asset["approval"]["config_differential"]["status"] = status
                    if status == "passed":
                        asset["approval"]["config_differential"]["config_count"] = 2

                with self.assertRaisesRegex(
                    PreviewRegistryError, "config differential"
                ):
                    validate_registry(self.mutated(mutate), production=True)

    def test_a_v5_asset_cannot_shed_its_hashed_differential_report(self):
        def mutate(asset):
            asset["approval"]["config_differential"].pop("report_sha256")

        with self.assertRaisesRegex(PreviewRegistryError, "hashed report"):
            validate_registry(self.mutated(mutate), production=True)

    def test_a_v5_asset_cannot_be_relabelled_a_single_configuration_master(self):
        def mutate(asset):
            asset["provenance"]["definition_kind"] = "single_configuration_master"

        # A single-configuration master must record an absent differential; this
        # one carries a passing record, so the relabel is rejected rather than
        # silently accepted as the weaker claim.
        with self.assertRaisesRegex(
            PreviewRegistryError, "cannot claim a config differential"
        ):
            validate_registry(self.mutated(mutate), production=True)

    def test_a_drifted_v5_delivery_file_fails_closed(self):
        def mutate(asset):
            asset["delivery"]["content"]["byte_size"] += 1

        with self.assertRaisesRegex(PreviewRegistryError, "byte size drifted"):
            validate_registry(self.mutated(mutate), production=True)

    def test_promotion_does_not_disturb_the_goal_0011_v4_record(self):
        asset = asset_by_id(load_registry(REGISTRY_PATH, production=True), V4_ASSET_ID)
        self.assertEqual(
            asset["provenance"]["definition_kind"], "single_configuration_master"
        )
        self.assertEqual(asset["approval"]["config_differential"]["status"], "absent")
        self.assertEqual(asset["match"]["policy"], "discrete_signature")
        self.assertEqual(
            asset["provenance"]["source_receipt_sha256"],
            "1cb1c04a8994f7710ca17d567f32e7fc14f5169352a4ffa06bedac3031ea43a5",
        )
        self.assertEqual(
            asset["match"]["geometry_signature"],
            "sha256:e4b84e9fb4797d7b0910d9a9ed810ba6dffe410961f255c43c434e3c58d2042c",
        )
        self.assertEqual(asset["geometry"]["triangle_count"], 149064)
        self.assertEqual(asset["geometry"]["vertex_count"], 78281)


class V5BrowserAssetTests(unittest.TestCase):
    def test_every_delivery_manifest_names_its_own_solved_configuration(self):
        seen = set()
        for configuration_id, length, width, depth, ring_size in SOLVED:
            with self.subTest(configuration=configuration_id):
                path = (
                    V5_ASSET_DIR
                    / f"solitaire_parametric_master_v5_{configuration_id}.browser_mesh.json"
                )
                manifest = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["template_id"], V5_TEMPLATE_ID)
                self.assertEqual(manifest["configuration_id"], configuration_id)
                self.assertEqual(
                    manifest["manufacturing_gates"],
                    "pending_not_run_needs_calibration",
                )
                self.assertFalse(manifest["manufacturing_authority"])
                configuration = manifest["configuration"]
                self.assertEqual(configuration["ring"]["size"], ring_size)
                self.assertEqual(configuration["center_stone"]["length_mm"], length)
                self.assertEqual(configuration["center_stone"]["width_mm"], width)
                self.assertEqual(configuration["center_stone"]["depth_mm"], depth)
                self.assertEqual(len(manifest["parts"]), 20)
                seen.add(manifest["source_receipt_sha256"])
        self.assertEqual(len(seen), 3)

    def test_delivery_manifests_use_the_container_format_the_client_parses(self):
        ring3d = (ROOT / "fast_mvp" / "static" / "ring3d.js").read_text(encoding="utf-8")
        for configuration_id, *_ in SOLVED:
            with self.subTest(configuration=configuration_id):
                manifest = json.loads(
                    (
                        V5_ASSET_DIR
                        / f"solitaire_parametric_master_v5_{configuration_id}.browser_mesh.json"
                    ).read_text(encoding="utf-8")
                )
                # The schema string names the *container format* the pinned r166
                # client parses, not the goal that produced the geometry.
                self.assertIn(f"'{manifest['schema']}'", ring3d)
                self.assertEqual(manifest["binary"]["header_bytes"], 16)
                self.assertEqual(
                    manifest["index_count"], manifest["triangle_count"] * 3
                )

    def test_every_meshbin_satisfies_the_client_binary_contract(self):
        # Mirrors ring3d.js verifyMeshBinary against the real shipped bytes: a
        # binary that fails any of these is rejected in the browser, so an asset
        # that ships broken would surface as an unexplained blank preview.
        registry = load_registry(REGISTRY_PATH, production=True)
        for configuration_id, *_ in SOLVED:
            with self.subTest(configuration=configuration_id):
                asset = asset_by_id(registry, f"goal0014-v5-{configuration_id}")
                blob = (
                    V5_ASSET_DIR
                    / Path(asset["delivery"]["content"]["url"]).name
                ).read_bytes()
                magic, vertex_count, index_count = struct.unpack_from("<8sII", blob, 0)
                self.assertEqual(magic, b"MYJV4B1\0")
                self.assertEqual(vertex_count, asset["geometry"]["vertex_count"])
                self.assertEqual(index_count, asset["geometry"]["index_count"])
                self.assertEqual(asset["geometry"]["triangle_count"] * 3, index_count)
                index_offset = 16 + vertex_count * 3 * 4 * 2
                self.assertEqual(index_offset + index_count * 4, len(blob))
                indices = struct.unpack_from(f"<{index_count}I", blob, index_offset)
                self.assertLess(max(indices), vertex_count)


if __name__ == "__main__":
    unittest.main()
