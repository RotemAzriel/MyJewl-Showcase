import json
import shutil
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
import trimesh

from scripts.build_geometry_bundle import build_bundle_documents
from scripts.collect_rhino_exports import collect_exports
from scripts.geometry_bundle import (
    BundleValidationError,
    canonical_triangle_hash,
    inspect_mesh,
    receipt_file_entry,
    validate_bundle,
)
from scripts.geometry_bundle_smoke import run_geometry_bundle_smoke


CONFIG_ID = "config_solitaire_round_1.00ct_prong4_14ky_2.0mm_us65"


def _flat() -> dict:
    return {
        "config_id": CONFIG_ID,
        "archetype": "solitaire",
        "stone_shape": "round",
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "stone_carat_eq": 1.0,
        "center_setting": "prong",
        "prong_count": 4,
        "metal_type": "gold",
        "metal_alloy": "14k_yellow",
        "band_width_mm": 2.0,
        "band_thickness_mm": 1.6,
        "ring_size_us": 6.5,
    }


class GeometryBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.params = self.root / "parameters.json"
        self.params.write_text(json.dumps(_flat()), encoding="utf-8")
        self._write_meshes()
        self._write_receipt()
        build_bundle_documents(self.root, self.params)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_meshes(self):
        band = trimesh.creation.box(extents=(8.0, 2.0, 2.0))
        stone = trimesh.creation.icosphere(subdivisions=1, radius=1.5)
        stone.apply_translation((0.0, 0.0, 4.0))
        prongs = trimesh.util.concatenate(
            [
                trimesh.creation.box(extents=(0.4, 0.4, 3.0)).apply_translation(
                    (x, 0.0, 2.5)
                )
                for x in (-2.0, 2.0)
            ]
        )
        band.export(self.root / "band.stl")
        stone.export(self.root / "stone.stl")
        prongs.export(self.root / "prongs.stl")
        trimesh.util.concatenate([band, stone, prongs]).export(self.root / "model.stl")

    def _write_receipt(self):
        export_id = str(uuid.uuid4())
        files = {}
        for name in ("model.stl", "band.stl", "stone.stl", "prongs.stl"):
            files[name] = receipt_file_entry(inspect_mesh(self.root / name))
        receipt = {
            "schema_version": 1,
            "export_id": export_id,
            "job_id": CONFIG_ID,
            "gh_definition_sha256": "a" * 64,
            "gh_definition_version": "test",
            "exported_at_utc": "2026-06-24T12:00:00Z",
            "files": files,
        }
        (self.root / "export_receipt.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    def _refresh_receipt(self):
        receipt = json.loads((self.root / "export_receipt.json").read_text())
        for name in receipt["files"]:
            receipt["files"][name] = receipt_file_entry(inspect_mesh(self.root / name))
        (self.root / "export_receipt.json").write_text(json.dumps(receipt))

    def test_valid_bundle_and_adapter_generated_canonical(self):
        report = validate_bundle(self.root)
        self.assertEqual(report["status"], "valid")
        self.assertFalse(report["official_geometry_gate_state_modified"])
        canonical = json.loads((self.root / "canonical_spec.json").read_text())
        self.assertEqual(canonical["job"]["job_id"], CONFIG_ID)
        self.assertEqual(canonical["spec_version"], "0.1")

    def test_missing_file_fails(self):
        (self.root / "stone.stl").unlink()
        with self.assertRaisesRegex(BundleValidationError, "missing"):
            validate_bundle(self.root)

    def test_duplicate_role_fails_but_repeated_class_one_is_allowed(self):
        manifest_path = self.root / "geometry_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["parts"].append(
            {
                "role": "setting",
                "path": "setting.stl",
                "material_role": "metal",
                "mask_class": 1,
            }
        )
        (self.root / "band.stl").replace(self.root / "setting.stl")
        trimesh.creation.box(extents=(8.0, 2.0, 2.0)).export(self.root / "band.stl")
        manifest["parts"].append(dict(manifest["parts"][0]))
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(BundleValidationError, "Duplicate manifest role"):
            validate_bundle(self.root)

    def test_invalid_setting_class_four_fails(self):
        manifest_path = self.root / "geometry_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["parts"][0]["mask_class"] = 4
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(BundleValidationError, "must use material/class"):
            validate_bundle(self.root)

    def test_receipt_hash_mismatch_fails(self):
        receipt_path = self.root / "export_receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["files"]["band.stl"]["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(BundleValidationError, "SHA-256 mismatch"):
            validate_bundle(self.root)

    def test_unreadable_mesh_fails(self):
        (self.root / "band.stl").write_bytes(b"not an stl" * 20)
        with self.assertRaises(BundleValidationError):
            validate_bundle(self.root)

    def test_non_finite_mesh_fails(self):
        path = self.root / "band.stl"
        with path.open("wb") as handle:
            handle.write(b"\0" * 80)
            handle.write(struct.pack("<I", 1))
            handle.write(struct.pack("<3f", 0.0, 0.0, 1.0))
            handle.write(struct.pack("<9f", np.nan, 0, 0, 1, 0, 0, 0, 1, 0))
            handle.write(struct.pack("<H", 0))
        with self.assertRaisesRegex(BundleValidationError, "non-finite"):
            validate_bundle(self.root)

    def test_same_bounds_different_triangles_fails(self):
        stone = trimesh.load(self.root / "stone.stl", force="mesh", process=False)
        original_bounds = stone.bounds.copy()
        vertices = stone.vertices.copy()
        centerish = np.argmin(np.linalg.norm(vertices, axis=1))
        vertices[centerish] += (0.05, 0.0, 0.0)
        changed = trimesh.Trimesh(vertices=vertices, faces=stone.faces, process=False)
        self.assertTrue(np.allclose(changed.bounds, original_bounds))
        changed.export(self.root / "stone.stl")
        self._refresh_receipt()
        with self.assertRaisesRegex(BundleValidationError, "triangle multiset"):
            validate_bundle(self.root)

    def test_quantized_hash_tolerates_sub_grid_noise(self):
        facts = inspect_mesh(self.root / "band.stl")
        tiny = facts.triangles.copy()
        tiny += 1e-7
        count_a, hash_a = canonical_triangle_hash([facts.triangles], 1e-5)
        count_b, hash_b = canonical_triangle_hash([tiny], 1e-5)
        self.assertEqual(count_a, count_b)
        self.assertEqual(hash_a, hash_b)

    def test_local_smoke_report_wires_gates_and_render_input(self):
        report = run_geometry_bundle_smoke(self.root)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["gate_1_closedness"]["status"], "pass")
        self.assertEqual(
            [item["role"] for item in report["gate_1_closedness"]["meshes"]],
            ["combined", "band", "center_stone", "prongs"],
        )
        self.assertEqual(
            [part["role"] for part in report["render_input"]["parts"]],
            ["band", "center_stone", "prongs"],
        )
        self.assertEqual(report["render_input"]["expected_mask_values"], [0, 1, 2, 3])
        self.assertFalse(report["official_geometry_gate_state_modified"])
        self.assertIn(
            report["collision_gates"]["stone_metal_collision"]["status"],
            {"pass", "needs_calibration"},
        )

    def test_collect_rhino_exports_builds_local_bundle_documents(self):
        source_root = self.root / "vm_exports"
        export_dir = source_root / CONFIG_ID / "export-001"
        export_dir.mkdir(parents=True)
        dest_root = self.root / "collected"
        config_dir = self.root / "configs"
        config_bundle = config_dir / CONFIG_ID
        config_bundle.mkdir(parents=True)
        shutil_copy_params = config_bundle / "parameters.json"
        shutil_copy_params.write_text(
            self.params.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        for name in ("model.stl", "band.stl", "stone.stl", "prongs.stl", "export_receipt.json"):
            shutil.copy2(self.root / name, export_dir / name)

        report = collect_exports(source_root, config_dir, dest_root)

        bundle_dir = dest_root / CONFIG_ID
        self.assertEqual(report["collected"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertTrue((bundle_dir / "parameters.json").is_file())
        self.assertTrue((bundle_dir / "canonical_spec.json").is_file())
        self.assertTrue((bundle_dir / "geometry_manifest.json").is_file())
        self.assertEqual(validate_bundle(bundle_dir)["status"], "valid")


if __name__ == "__main__":
    unittest.main()
