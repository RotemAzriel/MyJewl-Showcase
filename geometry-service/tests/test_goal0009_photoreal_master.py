import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manual" / "tools" / "build_goal0009_preview_master_ghx.py"
CLIENT = ROOT / "scripts" / "goal0009_compute_client.py"
AZURE_RUNNER = ROOT / "scripts" / "goal0009_azure_master_run.py"
BLENDER = ROOT / "scripts" / "blender_goal0009_reference.py"
PLAN = ROOT / "docs" / "goal_0009_photoreal_solitaire_plan_2026-07-29.md"


class Goal0009MasterSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.client = CLIENT.read_text(encoding="utf-8")
        cls.azure_runner = AZURE_RUNNER.read_text(encoding="utf-8")
        cls.blender = BLENDER.read_text(encoding="utf-8")
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_source_is_valid_python_and_explicitly_non_certifying(self):
        ast.parse(self.source)
        spec = importlib.util.spec_from_file_location("goal0009_builder", SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ast.parse(module.SCRIPT)
        self.assertIn('"manufacturing_authority": False', self.source)
        self.assertIn('"authority": "preview_only_not_manufacturing"', self.source)
        self.assertIn('"manufacturing_gates": "pending_not_run_needs_calibration"', self.source)
        self.assertIn("Azure Rhino.Compute 8 /grasshopper", self.source)
        self.assertNotIn("RunPod", self.source)

    def test_reference_template_contains_professional_form_controls(self):
        for field in (
            "shank_width", "shank_radial_thickness", "shoulder_root_radius",
            "shoulder_neck_radius", "shoulder_split_half_gap_root",
            "shoulder_split_half_gap_neck", "shoulder_rise", "basket_lower_radius",
            "basket_upper_radius", "prong_root_radius", "prong_seat_radius",
            "prong_tip_radius", "pavilion_clearance", "surface_continuity",
        ):
            self.assertIn('"{}"'.format(field), self.source)
        self.assertIn('"reference_band_type": "split_ring_solitaire"', self.source)
        self.assertIn('"reference_head_type": "classic_basket"', self.source)
        self.assertIn('"goal0009-split-ring-classic-basket-v2"', self.source)

    def test_master_has_named_parts_and_round_brilliant_regions(self):
        for marker in (
            '"shank"', '"basket_lower_rail"', '"stone_seat_rail"',
            '"basket_petal_', '"prong_', '"prong_tip_',
            '"center_stone_round_brilliant"', '"table"', '"star"',
            '"girdle_top"', '"girdle_bottom"', '"pavilion_break"',
            'rings["girdle_bottom"][right]',
        ):
            self.assertIn(marker, self.source)
        self.assertIn('add_ring("girdle_top", 32', self.source)
        self.assertIn('add_ring("pavilion_break", 16', self.source)
        self.assertNotIn("scaled_sphere", self.source)
        self.assertNotIn("cylinder_prong", self.source)

    def test_plan_is_single_product_and_requires_explicit_review(self):
        self.assertIn("single-product quality gate", self.plan)
        self.assertIn("explicit visual review keeps Goal 0009 active", self.plan)
        self.assertIn("operator explicitly approved Azure Rhino Core", self.plan)
        self.assertIn("2048 px", self.plan)

    def test_compute_client_keeps_key_in_vm_environment(self):
        self.assertIn('os.environ.get("RHINO_COMPUTE_API_KEY", "")', self.client)
        self.assertNotIn("--api-key", self.client)
        self.assertIn('!= "response_json"', self.client)
        self.assertIn("for _ in range(12)", self.client)

    def test_azure_runner_enforces_approval_cleanup_and_deallocation(self):
        self.assertIn('parser.add_argument("--operator-approved"', self.azure_runner)
        self.assertIn('"--reuse-existing"', self.azure_runner)
        self.assertIn("finally:", self.azure_runner)
        self.assertIn('"vm", "deallocate"', self.azure_runner)
        self.assertIn("confirmed_deallocated", self.azure_runner)
        self.assertIn('"network", "nsg", "rule", "delete"', self.azure_runner)
        self.assertNotIn("--api-key", self.azure_runner)
        # PowerShell blocks inside Python ``str.format`` templates must keep
        # doubled braces or fail before the Compute call.
        self.assertIn(")) {{ throw 'VM API key unavailable' }};", self.azure_runner)
        self.assertIn(
            "if (!(Test-Path $source)) {{ throw 'output directory missing' }};",
            self.azure_runner,
        )

    def test_cycles_lane_imports_rhino_master_and_exports_named_glb_lods(self):
        self.assertIn('bpy.ops.wm.obj_import', self.blender)
        self.assertIn('forward_axis="NEGATIVE_Y", up_axis="Z"', self.blender)
        self.assertIn('expected 20 named Rhino parts', self.blender)
        self.assertIn('solitaire_reference_master_v2_lod0.glb', self.blender)
        self.assertIn('solitaire_reference_master_v2_lod1.glb', self.blender)
        self.assertIn('export_tangents=True', self.blender)
        self.assertIn('canonicalize_glb_tangents(path)', self.blender)
        self.assertIn('primitive.get("attributes", {}).get("TANGENT")', self.blender)
        self.assertIn('scene.cycles.transmission_bounces = 16', self.blender)
        self.assertIn('scene.cycles.seed = 9', self.blender)
        self.assertIn('scene.cycles.use_animated_seed = False', self.blender)
        self.assertIn('scene.cycles.caustics_refractive = True', self.blender)
        self.assertIn('glass.inputs["IOR"].default_value = 2.417', self.blender)
        self.assertIn('camera.data.clip_start = 0.001', self.blender)
        self.assertIn('add_reflection_card("Left contrast flag"', self.blender)
        self.assertNotIn('primitive_torus_add', self.blender)


if __name__ == "__main__":
    unittest.main()
