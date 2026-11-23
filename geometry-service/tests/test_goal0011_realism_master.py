import importlib.util
import copy
import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "manual" / "tools" / "build_goal0011_realism_master_ghx.py"
V2_BUILDER = ROOT / "manual" / "tools" / "build_goal0011_realism_master_v2_ghx.py"
V3_BUILDER = ROOT / "manual" / "tools" / "build_goal0011_realism_master_v3_ghx.py"
V4_BUILDER = ROOT / "manual" / "tools" / "build_goal0011_realism_master_v4_ghx.py"
AUDITOR = ROOT / "scripts" / "goal0011_master_audit.py"
CLIENT = ROOT / "scripts" / "goal0011_compute_client.py"
RUNNER = ROOT / "scripts" / "goal0011_azure_master_run.py"
RENDERER = ROOT / "scripts" / "blender_goal0011_realism_master.py"
INTENT_RENDERER = ROOT / "scripts" / "blender_goal0011_source_intent.py"
RENDER_QUALITY = ROOT / "scripts" / "goal0011_render_quality.py"
DIFFERENTIAL = ROOT / "scripts" / "goal0011_differential.py"
UI_PROMOTION = ROOT / "scripts" / "goal0011_ui_promotion.py"
BROWSER_ASSET_BUILDER = ROOT / "scripts" / "build_goal0011_browser_asset.py"
RING3D = ROOT / "fast_mvp" / "static" / "ring3d.js"
V4_BROWSER_ASSET_DIR = ROOT / "fast_mvp" / "static" / "assets" / "goal0011_v4"
PLAN = ROOT / "docs" / "goal_0011_realism_first_rhino_master_plan_2026-07-30.md"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Goal0011RealismMasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load(BUILDER, "goal0011_builder")
        cls.v2_builder = load(V2_BUILDER, "goal0011_v2_builder")
        cls.v3_builder = load(V3_BUILDER, "goal0011_v3_builder")
        cls.v4_builder = load(V4_BUILDER, "goal0011_v4_builder")
        cls.auditor = load(AUDITOR, "goal0011_auditor")
        cls.differential = load(DIFFERENTIAL, "goal0011_differential")
        cls.ui_promotion = load(UI_PROMOTION, "goal0011_ui_promotion")
        cls.browser_asset_builder = load(
            BROWSER_ASSET_BUILDER,
            "goal0011_browser_asset_builder",
        )
        cls.builder_text = BUILDER.read_text(encoding="utf-8")
        cls.client_text = CLIENT.read_text(encoding="utf-8")
        cls.runner_text = RUNNER.read_text(encoding="utf-8")
        cls.renderer_text = RENDERER.read_text(encoding="utf-8")
        cls.intent_text = INTENT_RENDERER.read_text(encoding="utf-8")
        cls.plan_text = PLAN.read_text(encoding="utf-8")

    def make_receipt(self, directory: Path, revision: str = "v1"):
        parts = []
        for name in sorted(self.auditor.EXPECTED_PARTS):
            path = directory / (name + ".stl")
            path.write_bytes(("goal0011:" + name).encode("utf-8"))
            parts.append({
                "name": name,
                "role": "stone" if name == "center_stone_round_brilliant" else "metal",
                "closed": True,
                "file": path.name,
                "sha256": self.auditor._sha256(path),
            })
        receipt = {
            "authority": "rhino_master_candidate_not_manufacturing",
            "manufacturing_gates": "pending_not_run_needs_calibration",
            "template": {
                "template_id": self.auditor.TEMPLATE_IDS[revision],
                "source_ownership": self.auditor.SOURCE_OWNERSHIP,
            },
            "parts": parts,
        }
        receipt_path = directory / ("solitaire_realism_master_{}_receipt.json".format(revision))
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt, receipt_path

    def make_render_evidence(self, directory: Path, receipt_path: Path):
        views = {}
        for name in sorted(self.auditor.REQUIRED_VIEWS):
            path = directory / (name + "_2048.png")
            path.write_bytes(("render:" + name).encode("utf-8"))
            views[name] = {
                "file": path.name,
                "sha256": self.auditor._sha256(path),
            }
        evidence = {
            "authority": "comparison_only_not_manufacturing",
            "resolution": [2048, 2048],
            "source_receipt_sha256": self.auditor._sha256(receipt_path),
            "views": views,
        }
        path = directory / "cycles_fixed_views_evidence.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return path

    def test_owned_source_removes_the_long_midline_shoulders(self):
        self.assertEqual(self.builder.TEMPLATE_ID, "goal0011-realism-first-round-solitaire-master-v1")
        self.assertEqual(self.builder.SHOULDER_PATH[0][2], 7.72)
        self.assertGreater(min(point[2] for point in self.builder.SHOULDER_PATH), 7.5)
        self.assertIn('"shoulder_contact_z": 7.72', self.builder.SCRIPT)
        self.assertIn("side * 4.82", self.builder.SCRIPT)
        self.assertNotIn("side * 4.86", self.builder.SCRIPT)
        self.assertNotIn("rg.Point3d(side * 4.78, -0.34, 1.72)", self.builder.SCRIPT)

    def test_prong_path_turns_inward_over_the_crown(self):
        self.assertGreater(self.builder.PRONG_RADII[-2], self.builder.PRONG_RADII[-1])
        self.assertEqual(self.builder.PRONG_RADII[-1], 2.68)
        self.assertEqual(self.builder.PRONG_Z[-1], 12.54)
        self.assertIn("radial.X * 2.68", self.builder.SCRIPT)
        self.assertIn("inward_seated_prongs", self.builder.SCRIPT)

    def test_control_paths_overlap_at_every_authored_interface(self):
        metrics = self.builder.connection_metrics()
        self.assertTrue(metrics["source_preflight_ready"])
        self.assertLess(metrics["shoulder_root_shank_ellipse_value"], 1.0)
        self.assertLess(metrics["lower_rail_shank_ellipse_value"], 1.0)
        self.assertAlmostEqual(metrics["shoulder_end_seat_centerline_error_mm"], 0.0, places=4)
        self.assertGreater(metrics["petal_upper_rail_overlap_mm"], 0.0)
        self.assertGreater(metrics["prong_root_lower_rail_overlap_mm"], 0.0)
        self.assertGreater(metrics["prong_seat_girdle_overlap_mm"], 0.0)
        self.assertGreater(metrics["prong_tip_crown_overlap_mm"], 0.0)

    def test_v2_resolves_split_shoulders_into_a_lower_gallery_collar(self):
        v1 = self.builder
        v2 = self.v2_builder
        metrics = v2.connection_metrics()
        self.assertTrue(metrics["source_preflight_ready"])
        self.assertLess(v2.BASKET_UPPER_Z, v1.BASKET_UPPER_Z)
        self.assertLess(v2.BASKET_UPPER_RADIUS, v1.BASKET_UPPER_RADIUS)
        self.assertLess(v2.SHOULDER_PATH[-1][2], v1.SHOULDER_PATH[-1][2])
        self.assertLess(v2.SHOULDER_PATH[0][2], v2.SHOULDER_PATH[1][2])
        self.assertLess(v2.SHOULDER_PATH[-1][1], v2.SHOULDER_PATH[-2][1])
        self.assertLess(v2.SHOULDER_PATH[-1][1], 0.5)
        self.assertAlmostEqual(metrics["shoulder_end_collar_centerline_error_mm"], 0.0, places=3)
        self.assertGreater(metrics["shoulder_vertical_clearance_below_girdle_mm"], 0.75)
        self.assertGreater(metrics["petal_upper_collar_overlap_mm"], 0.0)
        self.assertGreater(metrics["prong_collar_overlap_mm"], 0.0)
        self.assertGreater(metrics["prong_seat_girdle_overlap_mm"], 0.0)
        self.assertIn("split_shoulders_into_lower_gallery_collar", v2.SCRIPT)
        self.assertIn(v2.TEMPLATE_ID, v2.SCRIPT)
        self.assertNotIn("solitaire_realism_master_v1.obj", v2.SCRIPT)

    def test_v2_builder_preserves_the_stable_compute_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            built = self.v2_builder.build(Path(directory) / "goal0011_v2.ghx")
            data = built.read_bytes()
        self.assertIn(b"RH_IN:json_string", data)
        self.assertIn(b"RH_IN:job_id", data)
        self.assertIn(b"RH_OUT:response_json", data)

    def test_v3_makes_the_shoulder_basket_separation_visually_material(self):
        v2 = self.v2_builder
        v3 = self.v3_builder
        metrics = v3.connection_metrics()
        self.assertTrue(metrics["source_preflight_ready"])
        self.assertEqual(v3.SHOULDER_CONNECTION_RAIL, "lower")
        self.assertAlmostEqual(v3.SHOULDER_PATH[-1][2], v3.BASKET_LOWER_Z)
        self.assertLess(v3.SHOULDER_PATH[-1][2], v2.SHOULDER_PATH[-1][2] - 1.0)
        self.assertGreater(metrics["shoulder_vertical_clearance_below_girdle_mm"], 2.0)
        self.assertAlmostEqual(metrics["shoulder_end_lower_rail_centerline_error_mm"], 0.0, places=3)
        self.assertIn("split_shoulders_into_lower_basket_rail", v3.SCRIPT)
        self.assertNotIn("solitaire_realism_master_v2.obj", v3.SCRIPT)

    def test_v4_joins_shoulders_to_mid_basket_petals(self):
        v3 = self.v3_builder
        v4 = self.v4_builder
        metrics = v4.connection_metrics()
        self.assertTrue(metrics["source_preflight_ready"])
        self.assertEqual(v4.SHOULDER_CONNECTION_RAIL, "petals")
        self.assertGreater(v4.SHOULDER_PATH[-1][2], v3.SHOULDER_PATH[-1][2])
        self.assertLess(v4.SHOULDER_PATH[-1][2], v4.BASKET_UPPER_Z)
        self.assertAlmostEqual(metrics["shoulder_end_petal_centerline_error_mm"], 0.0, places=4)
        self.assertGreater(metrics["shoulder_petal_overlap_mm"], 0.45)
        self.assertGreater(metrics["shoulder_vertical_clearance_below_girdle_mm"], 1.0)
        self.assertIn("split_shoulders_into_mid_basket_petals", v4.SCRIPT)
        self.assertNotIn("solitaire_realism_master_v3.obj", v4.SCRIPT)

    def test_v2_receipt_requires_the_explicit_v2_audit_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt, receipt_path = self.make_receipt(directory, revision="v2")
            report = self.auditor.audit_master(
                receipt,
                receipt_path=receipt_path,
                master_dir=directory,
                expected_template_id=self.auditor.TEMPLATE_IDS["v2"],
            )
        self.assertTrue(report["structural_identity_ready"])
        self.assertEqual(report["expected_template_id"], self.v2_builder.TEMPLATE_ID)

    def test_v4_receipt_requires_the_explicit_v4_audit_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt, receipt_path = self.make_receipt(directory, revision="v4")
            report = self.auditor.audit_master(
                receipt,
                receipt_path=receipt_path,
                master_dir=directory,
                expected_template_id=self.auditor.TEMPLATE_IDS["v4"],
            )
        self.assertTrue(report["structural_identity_ready"])
        self.assertEqual(report["expected_template_id"], self.v4_builder.TEMPLATE_ID)

    def test_builder_exposes_the_stable_compute_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            built = self.builder.build(Path(directory) / "goal0011.ghx")
            data = built.read_bytes()
        self.assertIn(b"RH_IN:json_string", data)
        self.assertIn(b"RH_IN:job_id", data)
        self.assertIn(b"RH_OUT:response_json", data)
        self.assertIn(self.builder.TEMPLATE_ID, self.builder.SCRIPT)

    def test_receipt_can_be_structurally_and_hash_ready_without_visual_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt, receipt_path = self.make_receipt(directory)
            report = self.auditor.audit_master(
                receipt,
                receipt_path=receipt_path,
                master_dir=directory,
            )
        self.assertTrue(report["structural_identity_ready"])
        self.assertTrue(report["artifact_files"]["ready"])
        self.assertTrue(report["source_ownership_ready"])
        self.assertFalse(report["master_visual_ready"])
        self.assertEqual(report["visual_review"]["decision"], "not_run")
        self.assertEqual(report["manufacturing_gates"], "pending_not_run_needs_calibration")

    def test_explicit_human_review_closes_only_the_visual_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt, receipt_path = self.make_receipt(directory)
            evidence_path = self.make_render_evidence(directory, receipt_path)
            review = {
                "decision": "approved",
                "reviewer": "fixture operator",
                "source_receipt_sha256": self.auditor._sha256(receipt_path),
                "render_evidence_sha256": self.auditor._sha256(evidence_path),
                "checks": {name: True for name in self.auditor.REVIEW_CHECKS},
            }
            review_path = directory / "visual_review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            report = self.auditor.audit_master(
                receipt,
                receipt_path=receipt_path,
                master_dir=directory,
                render_evidence=evidence_path,
                visual_review=review_path,
            )
        self.assertTrue(report["render_provenance"]["ready"])
        self.assertTrue(report["visual_review"]["ready"])
        self.assertTrue(report["master_visual_ready"])
        self.assertEqual(
            report["differential_geometry_proof"]["status"],
            "not_run_until_visual_approval",
        )
        self.assertNotEqual(report["manufacturing_gates"], "pass")

    def test_receipt_differential_changes_only_shoulders_and_petals(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            before_dir = directory / "before"
            after_dir = directory / "after"
            before_dir.mkdir()
            after_dir.mkdir()
            before, before_path = self.make_receipt(before_dir, revision="v2")
            after, after_path = self.make_receipt(after_dir, revision="v4")
            before_hashes = {item["name"]: item["sha256"] for item in before["parts"]}
            for item in after["parts"]:
                if item["name"] in self.differential.EXPECTED_CHANGED_PARTS:
                    item["sha256"] = ("a" if item["name"] < "prong" else "b") * 64
                else:
                    item["sha256"] = before_hashes[item["name"]]
            after_path.write_text(json.dumps(after), encoding="utf-8")
            review = {
                "decision": "approved",
                "source_receipt_sha256": self.differential._sha256(after_path),
                "checks": {name: True for name in self.differential.REVIEW_CHECKS},
            }
            review_path = directory / "visual_review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            report = self.differential.compare(
                before,
                after,
                before_path=before_path,
                after_path=after_path,
                visual_review_path=review_path,
            )
            changed_stone = copy.deepcopy(after)
            for item in changed_stone["parts"]:
                if item["name"] == "center_stone_round_brilliant":
                    item["sha256"] = "c" * 64
            regression = self.differential.compare(
                before,
                changed_stone,
                before_path=before_path,
                after_path=after_path,
                visual_review_path=review_path,
            )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(
            set(report["changed_parts"]),
            self.differential.EXPECTED_CHANGED_PARTS,
        )
        self.assertEqual(
            set(report["unchanged_parts"]),
            self.differential.EXPECTED_UNCHANGED_PARTS,
        )
        self.assertEqual(regression["status"], "not_ready")

    def test_approved_v4_reference_is_exactly_bounded_in_catalog(self):
        from fast_mvp import manual_configurator

        payload = dict(self.ui_promotion.BOUNDED_PAYLOAD)
        options = manual_configurator.compatible_options(payload)
        heads = {item["head_style_id"]: item for item in options["head_styles"]}
        self.assertEqual(options["catalog_revision"], "modular-rings-v5")
        self.assertIn(self.ui_promotion.V4_HEAD_ID, heads)
        self.assertEqual(
            heads[self.ui_promotion.V4_HEAD_ID]["visual_reference"]["status"],
            "approved",
        )
        self.assertEqual(
            [
                item["band_style_id"]
                for item in options["band_styles_by_head"][self.ui_promotion.V4_HEAD_ID]
            ],
            [self.ui_promotion.V4_BAND_ID],
        )
        off_reference = manual_configurator.compatible_options({
            **payload,
            "stone_length_mm": 6.6,
        })
        self.assertNotIn(
            self.ui_promotion.V4_HEAD_ID,
            {item["head_style_id"] for item in off_reference["head_styles"]},
        )

    def test_browser_preview_builds_the_approved_v4_junction_as_visual_only(self):
        source = f"""
          import {{ buildPreviewReference }} from {RING3D.as_uri()!r};
          const preview = {{
            architecture:'solitaire', stone_shape:'round',
            stone_dimensions_mm:{{length:6.5,width:6.5,depth:3.9}},
            ring_size_us:6.5, center_setting:'prong', prong_count:4,
            head_family:'petal', head_geometry:'petal_prong',
            gallery_structure:'petal_open', band_width_mm:2.05,
            band_thickness_mm:1.65, band_profile:'comfort_fit',
            shoulder_style:'tapered_cathedral',
            head_style_id:'head_v4_mid_petal_4_prong',
            reference_geometry_id:'goal0011-realism-first-round-solitaire-master-v4'
          }};
          const group = await buildPreviewReference(preview);
          const counts = {{}};
          group.traverse(object => {{ counts[object.name] = (counts[object.name] || 0) + 1; }});
          console.log(JSON.stringify({{authority:group.userData.geometryAuthority,counts}}));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", source],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(
            report["authority"],
            "operational_out_of_preview_design_aid",
        )
        self.assertEqual(report["counts"]["v4-mid-petal-split-shoulder"], 4)
        self.assertEqual(report["counts"]["v4-petal-gallery-support"], 4)
        self.assertEqual(report["counts"]["v4-rounded-prong-tip"], 4)

    def test_receipt_backed_v4_browser_asset_contract_is_exact_and_hash_pinned(self):
        manifest_path = V4_BROWSER_ASSET_DIR / self.browser_asset_builder.MANIFEST_NAME
        binary_path = V4_BROWSER_ASSET_DIR / self.browser_asset_builder.BINARY_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        binary = binary_path.read_bytes()
        magic, vertex_count, index_count = struct.unpack("<8sII", binary[:16])
        self.assertEqual(magic, self.browser_asset_builder.MAGIC)
        self.assertEqual(vertex_count, manifest["vertex_count"])
        self.assertEqual(index_count, manifest["index_count"])
        self.assertEqual(hashlib.sha256(binary).hexdigest(), manifest["binary"]["sha256"])
        self.assertEqual(len(binary), manifest["binary"]["byte_size"])
        self.assertEqual(manifest["authority"], "receipt_backed_rhino_derived_browser_preview")
        self.assertFalse(manifest["manufacturing_authority"])
        self.assertEqual(
            manifest["source_receipt_sha256"],
            "1cb1c04a8994f7710ca17d567f32e7fc14f5169352a4ffa06bedac3031ea43a5",
        )
        self.assertEqual(
            manifest["source_obj_sha256"],
            "7ca79804c96985999fa783c4ee97cdb9f833f375ad29acb3538c92ac57fbc5e2",
        )
        self.assertEqual(len(manifest["parts"]), 20)
        self.assertEqual(manifest["vertex_count"], 78281)
        self.assertEqual(manifest["triangle_count"], 149064)
        self.assertEqual(sum(part["index_count"] for part in manifest["parts"]), 447192)

    def test_browser_asset_builder_preserves_receipt_named_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            names = sorted(self.auditor.EXPECTED_PARTS)
            vertex_lines = []
            normal_lines = []
            object_lines = []
            parts = []
            for index, name in enumerate(names):
                base = index * 3 + 1
                vertex_lines.extend([
                    f"v {index}.0 0.0 0.0",
                    f"v {index}.0 1.0 0.0",
                    f"v {index}.0 0.0 1.0",
                ])
                normal_lines.extend(["vn 1.0 0.0 0.0"] * 3)
                object_lines.extend([
                    f"o {name}",
                    f"f {base}//{base} {base + 1}//{base + 1} {base + 2}//{base + 2}",
                ])
                parts.append({
                    "name": name,
                    "role": "stone" if name == "center_stone_round_brilliant" else "metal",
                    "triangles": 1,
                })
            obj_path = directory / "fixture.obj"
            obj_path.write_text(
                "\n".join(vertex_lines + normal_lines + object_lines) + "\n",
                encoding="utf-8",
            )
            receipt_path = directory / "fixture_receipt.json"
            receipt_path.write_text(json.dumps({
                "schema": "myjewl.goal0011.realism_master_receipt.v4",
                "template": {"template_id": self.v4_builder.TEMPLATE_ID},
                "manufacturing_gates": "pending_not_run_needs_calibration",
                "parts": parts,
            }), encoding="utf-8")
            _, _, manifest = self.browser_asset_builder.write_asset(
                obj_path,
                receipt_path,
                directory / "built",
            )
        self.assertEqual([part["name"] for part in manifest["parts"]], names)
        self.assertEqual(manifest["triangle_count"], 20)
        self.assertEqual(manifest["index_count"], 60)

    def test_v4_webgl_path_loads_the_receipt_backed_asset_and_fails_closed(self):
        source = RING3D.read_text(encoding="utf-8")
        for marker in (
            "loadVerifiedAssetPayload",
            "buildVerifiedAssetGroup",
            "receipt_backed_rhino_derived_browser_preview",
            "referenceAssetStatus",
            "clearRingGroup",
            "displayedPreviewStatus = 'unavailable'",
            "sourceReceiptSha256",
            "referenceTriangleCount",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("procedural_fallback", source)

    def test_azure_lane_is_operator_gated_and_secret_hygienic(self):
        runner = load(RUNNER, "goal0011_runner")
        self.assertEqual(runner.main([]), 2)
        self.assertIn('parser.add_argument("--operator-approved"', self.runner_text)
        self.assertIn('"vm", "deallocate"', self.runner_text)
        self.assertIn("confirmed_deallocated", self.runner_text)
        self.assertEqual(runner.REVISION_CONFIG["v2"]["template_id"], self.v2_builder.TEMPLATE_ID)
        self.assertEqual(runner.REVISION_CONFIG["v2"]["master_stem"], self.v2_builder.MASTER_FILE_STEM)
        self.assertEqual(runner.REVISION_CONFIG["v4"]["template_id"], self.v4_builder.TEMPLATE_ID)
        self.assertEqual(runner.REVISION_CONFIG["v4"]["master_stem"], self.v4_builder.MASTER_FILE_STEM)
        self.assertIn('parser.add_argument("--template-id", required=True)', self.client_text)
        self.assertNotIn("--api-key", self.runner_text)
        self.assertNotIn("--api-key", self.client_text)
        self.assertIn('os.environ.get("RHINO_COMPUTE_API_KEY", "")', self.client_text)

    def test_azure_wrapper_messages_are_parsed_before_solve_status_check(self):
        runner = load(RUNNER, "goal0011_runner_messages")
        stdout = json.dumps({"value": [{"message": json.dumps({"execution_status": "completed"})}]})
        self.assertEqual(
            runner.powershell_messages(stdout),
            [json.dumps({"execution_status": "completed"})],
        )
        self.assertIn('parser.add_argument(\n        "--reuse-existing"', self.runner_text)
        self.assertIn(r"{work}\transfer.pid", self.runner_text)
        self.assertIn("Get-NetTCPConnection -LocalPort {port}", self.runner_text)

    def test_renderer_consumes_rhino_obj_and_requires_three_fixed_views(self):
        self.assertIn('MASTER_STEMS[args.revision]', self.renderer_text)
        self.assertIn('master_stem + ".obj"', self.renderer_text)
        self.assertIn('"source": "Azure Rhino.Compute named-part OBJ"', self.renderer_text)
        self.assertIn('"comparison_only_not_manufacturing"', self.renderer_text)
        for view in self.auditor.REQUIRED_VIEWS:
            self.assertIn('"{}"'.format(view), self.renderer_text)
        self.assertIn("global_scale=0.001", self.renderer_text)
        self.assertIn("generated_preview_only_not_rhino_geometry", self.intent_text)
        self.assertIn('choices=("v1", "v2", "v3", "v4")', self.intent_text)
        self.assertIn('parser.add_argument("--head-closeups"', self.intent_text)
        self.assertIn('"head_low_three_quarter"', self.intent_text)
        self.assertIn("disabled_for_structural_review", self.intent_text)
        self.assertIn("boolean_union_for_legibility_only", self.intent_text)

    def test_plan_preserves_realism_first_and_non_certifying_boundaries(self):
        self.assertIn("Produce one visually credible MyJewl-owned solitaire master", self.plan_text)
        self.assertIn("front three-quarter, side, and top/stone-seating", self.plan_text)
        self.assertIn("Only after visual approval", self.plan_text)
        self.assertIn("No Azure/RunPod execution without fresh operator approval", self.plan_text)
        self.assertIn("needs_calibration", self.plan_text)

    def test_render_quality_report_measures_clipping_without_approving_visuals(self):
        module = load(RENDER_QUALITY, "goal0011_render_quality")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            baseline = directory / "baseline.png"
            Image.new("RGB", (16, 16), (255, 255, 255)).save(baseline)
            views = {}
            for name in sorted(module.REQUIRED_VIEWS):
                path = directory / (name + ".png")
                Image.new("RGB", (16, 16), (96, 102, 112)).save(path)
                views[name] = {"file": path.name, "sha256": module.sha256(path)}
            evidence = directory / "evidence.json"
            evidence.write_text(json.dumps({
                "source_receipt_sha256": "a" * 64,
                "views": views,
            }), encoding="utf-8")
            report = module.build_report(baseline, evidence)
        self.assertTrue(report["hashes_ready"])
        self.assertTrue(report["controlled_highlights_presentation_check"])
        self.assertEqual(report["human_visual_review"], "not_run")
        self.assertNotIn("pass", report["manufacturing_gates"])

    def test_the_ui_promotion_audit_still_reports_ready(self):
        """The V4 UI promotion audit must keep passing as the codebase moves.

        It silently went ``not_ready`` on 2026-08-01 and stayed that way for five
        days: Goal 0012A renamed the procedural fallback's declared authority, the
        audit still pinned the retired string, and nothing asserted its verdict —
        so the drift was invisible while the Goal 0011 entry in `SESSION_GOALS.md`
        still recorded it as ``ready``.
        Asserting the verdict here is what makes the next such rename fail loudly.
        """
        receipt = (
            ROOT
            / "output"
            / "goal_0011_realism_master_20260730"
            / "azure_v4"
            / "received"
            / "solitaire_realism_master_v4_receipt.json"
        )
        differential = (
            ROOT
            / "output"
            / "goal_0011_realism_master_20260730"
            / "azure_v4"
            / "differential_v2_to_v4.json"
        )
        if not receipt.is_file() or not differential.is_file():
            self.skipTest("Goal 0011 V4 receipt evidence is not present locally")
        report = self.ui_promotion.build_report(receipt, differential)
        failed = sorted(
            key for key, value in report.items()
            if key.endswith("_ready") and value is not True
        )
        self.assertEqual(failed, [], f"audit checks regressed: {failed}")
        self.assertEqual(report["status"], "ready")
        # Repairing the audit must never turn it into a manufacturing claim.
        self.assertEqual(
            report["manufacturing_gates"], "pending_not_run_needs_calibration"
        )


if __name__ == "__main__":
    unittest.main()
