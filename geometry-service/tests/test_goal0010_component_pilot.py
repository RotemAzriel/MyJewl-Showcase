import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "goal0010_component_pilot.py"
PLAN = ROOT / "docs" / "goal_0010_component_pilot_plan_2026-07-29.md"
SOURCE = ROOT / "manual" / "tools" / "build_goal0010_component_pilot_ghx.py"
CLIENT = ROOT / "scripts" / "goal0010_compute_client.py"
RUNNER = ROOT / "scripts" / "goal0010_azure_component_pilot_run.py"
BLENDER = ROOT / "scripts" / "blender_goal0010_component_pilot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("goal0010_component_pilot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Goal0010ComponentPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.plan = PLAN.read_text(encoding="utf-8")
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.client = CLIENT.read_text(encoding="utf-8")
        cls.runner = RUNNER.read_text(encoding="utf-8")
        cls.blender = BLENDER.read_text(encoding="utf-8")

    def make_receipt(self, *, mutate=None):
        parts = [
            {"name": name, "role": "stone" if name == "center_stone_round_brilliant" else "metal", "closed": True}
            for name in sorted(self.module.EXPECTED_PARTS)
        ]
        receipt = {
            "authority": "preview_only_not_manufacturing",
            "generated_by": "Azure Rhino.Compute 8 /grasshopper",
            "job_id": "goal0009-reference-v2",
            "manufacturing_gates": "pending_not_run_needs_calibration",
            "parts": parts,
        }
        if mutate:
            mutate(receipt)
        return receipt

    def test_bounded_receipt_is_structurally_ready_but_not_visually_approved(self):
        report = self.module.audit_receipt(self.make_receipt(), receipt_path=Path("fixture.json"))
        self.assertTrue(report["structural_identity_ready"])
        self.assertEqual(report["part_count"], 20)
        self.assertEqual(report["roles"], ["metal", "stone"])
        self.assertEqual(report["visual_gate"], "not_run")
        self.assertEqual(report["manufacturing_gates"], "pending_not_run_needs_calibration")
        self.assertEqual(report["external_authoring_source"], "missing")
        self.assertFalse(report["baseline_comparison_ready"])

    def test_artifact_hashes_and_render_receipt_provenance_can_be_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt = self.make_receipt()
            for item in receipt["parts"]:
                item["file"] = item["name"] + ".stl"
                path = directory / item["file"]
                path.write_bytes(item["name"].encode("utf-8"))
                item["sha256"] = self.module._sha256(path)
            receipt_path = directory / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            render = directory / "render.json"
            render.write_text(json.dumps({
                "authority": "comparison_only_not_manufacturing",
                "schema": "myjewl.goal0010.test-render.v1",
                "source_receipt_sha256": self.module._sha256(receipt_path),
            }), encoding="utf-8")
            report = self.module.audit_receipt(
                receipt,
                receipt_path=receipt_path,
                master_dir=directory,
                render_evidence=render,
            )
            self.assertTrue(report["artifact_files"]["ready"])
            self.assertTrue(report["render_provenance"]["ready"])
            self.assertTrue(report["baseline_comparison_ready"])

    def test_hash_mismatch_keeps_baseline_unready(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt = self.make_receipt()
            item = receipt["parts"][0]
            item["file"] = "part.stl"
            (directory / "part.stl").write_bytes(b"changed")
            item["sha256"] = "0" * 64
            receipt_path = directory / "receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            report = self.module.audit_receipt(receipt, receipt_path=receipt_path, master_dir=directory)
            self.assertFalse(report["artifact_files"]["ready"])
            self.assertFalse(report["baseline_comparison_ready"])

    def test_missing_part_fails_identity_without_certifying_manufacturing(self):
        def remove_prong(receipt):
            receipt["parts"] = [item for item in receipt["parts"] if item["name"] != "prong_04"]

        report = self.module.audit_receipt(self.make_receipt(mutate=remove_prong), receipt_path=Path("fixture.json"))
        self.assertFalse(report["structural_identity_ready"])
        self.assertEqual(report["missing_parts"], ["prong_04"])
        self.assertNotEqual(report["manufacturing_gates"], "pass")

    def test_cli_writes_json_report(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            receipt = directory / "receipt.json"
            report_path = directory / "report.json"
            receipt.write_text(json.dumps(self.make_receipt()), encoding="utf-8")
            self.assertEqual(self.module.main([str(receipt), "--report-json", str(report_path)]), 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "myjewl.goal0010.component_pilot.v2")
            self.assertEqual(report["next_action"], "provide_approved_professional_component_master")

    def test_plan_preserves_scope_and_separate_geometry_boundary(self):
        self.assertIn("solitaire configurator and premium 3D product preview", self.plan)
        self.assertIn("visual_gate: not_run", self.plan)
        self.assertIn("**Status:** complete", self.plan)
        self.assertIn("50 selectable UI pairs", self.plan)
        self.assertIn("Remaining separate geometry decision", self.plan)
        self.assertIn("Goal 0011", self.plan)
        self.assertIn("must not", self.plan)

    def test_local_rhino_candidate_source_is_explicitly_bounded_and_non_certifying(self):
        source_module = importlib.util.spec_from_file_location("goal0010_source", SOURCE)
        loaded = importlib.util.module_from_spec(source_module)
        assert source_module.loader is not None
        source_module.loader.exec_module(loaded)
        self.assertIn('"template_id": "goal0010-split-ring-classic-basket-candidate-v1"', self.source)
        self.assertIn('"authoring_status": "local_rhinocommon_candidate_pending_visual_review"', self.source)
        self.assertIn('"manufacturing_authority": False', self.source)
        self.assertIn("integrated_shoulder_sweeps_and_open_basket", self.source)
        self.assertIn("prong_tip_height", self.source)
        self.assertNotIn("RunPod", self.source)
        self.assertNotIn("primitive_torus_add", self.source)
        self.assertEqual(loaded.SCRIPT.count('"prong_{:02d}"'), 1)

    def test_local_candidate_ghx_builder_exposes_stable_contract(self):
        source_module = importlib.util.spec_from_file_location("goal0010_source_contract", SOURCE)
        loaded = importlib.util.module_from_spec(source_module)
        assert source_module.loader is not None
        source_module.loader.exec_module(loaded)
        with tempfile.TemporaryDirectory() as directory:
            ghx = loaded.build(Path(directory) / "candidate.ghx")
            data = Path(ghx).read_bytes()
        self.assertIn(b"RH_IN:json_string", data)
        self.assertIn(b"RH_IN:job_id", data)
        self.assertIn(b"RH_OUT:response_json", data)

    def test_compute_client_extracts_doubly_encoded_nested_response(self):
        client_module = importlib.util.spec_from_file_location("goal0010_compute_client", CLIENT)
        loaded = importlib.util.module_from_spec(client_module)
        assert client_module.loader is not None
        client_module.loader.exec_module(loaded)
        payload = {"execution_status": "completed", "part_count": 20}
        nested = json.dumps(json.dumps(payload))
        body = {"values": [{"ParamName": "RH_OUT:response_json (response_json)", "InnerTree": {"{0;0}": [{"data": nested}]}}]}
        self.assertEqual(loaded.extract_response(body), payload)

    def test_azure_runner_is_operator_gated_and_secret_hygienic(self):
        self.assertIn('parser.add_argument("--operator-approved"', self.runner)
        self.assertIn('parser.add_argument("--reuse-existing"', self.runner)
        self.assertIn('"vm", "deallocate"', self.runner)
        self.assertIn("confirmed_deallocated", self.runner)
        self.assertIn("goal0010-component-pilot-transfer", self.runner)
        self.assertNotIn("--api-key", self.runner)
        self.assertIn('os.environ.get("RHINO_COMPUTE_API_KEY", "")', self.client)
        self.assertNotIn("--api-key", self.client)
        self.assertIn("goal0010-split-ring-classic-basket-candidate-v1", self.client)

    def test_cycles_lane_uses_the_azure_candidate_obj_as_comparison_only(self):
        self.assertIn('solitaire_component_pilot_v1.obj', self.blender)
        self.assertIn('myjewl.goal0010.cycles_component_pilot.v1', self.blender)
        self.assertIn('comparison_only_not_manufacturing', self.blender)
        self.assertIn('source": "Azure Rhino.Compute named-part OBJ"', self.blender)
        self.assertIn('BASE.apply_cycles_diamond_glass(objects)', self.blender)


if __name__ == "__main__":
    unittest.main()
