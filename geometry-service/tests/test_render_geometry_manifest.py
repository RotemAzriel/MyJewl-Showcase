import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_geometry_manifest import (
    build_parser,
    expected_mask_values,
    load_render_manifest,
    metal_alloy_from_params,
    required_visible_mask_values,
)


class RenderGeometryManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("band.stl", "stone.stl", "prongs.stl", "setting.stl"):
            (self.root / name).write_bytes(b"mesh")
        self.receipt = {
            "job_id": "cfg",
            "export_id": "export-1",
        }
        (self.root / "export_receipt.json").write_text(json.dumps(self.receipt))
        self.manifest = {
            "job_id": "cfg",
            "export_id": "export-1",
            "receipt": "export_receipt.json",
            "parts": [
                {
                    "role": "band",
                    "path": "band.stl",
                    "material_role": "metal",
                    "mask_class": 1,
                },
                {
                    "role": "center_stone",
                    "path": "stone.stl",
                    "material_role": "diamond",
                    "mask_class": 2,
                },
                {
                    "role": "prongs",
                    "path": "prongs.stl",
                    "material_role": "metal",
                    "mask_class": 3,
                },
                {
                    "role": "setting",
                    "path": "setting.stl",
                    "material_role": "metal",
                    "mask_class": 1,
                },
            ],
        }
        self.path = self.root / "geometry_manifest.json"
        self.path.write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.tmp.cleanup()

    def test_relative_paths_and_role_mapping(self):
        manifest = load_render_manifest(self.path)
        self.assertEqual(manifest.parts[0].path, self.root / "band.stl")
        self.assertEqual(manifest.parts[-1].mask_class, 1)
        self.assertEqual(
            [(part.role, part.object_name, part.mask_class) for part in manifest.parts[:3]],
            [
                ("band", "myjewl_band", 1),
                ("center_stone", "myjewl_center_stone", 2),
                ("prongs", "myjewl_prongs", 3),
            ],
        )
        self.assertEqual(expected_mask_values(manifest), {0, 1, 2, 3})
        self.assertEqual(required_visible_mask_values(manifest), {1, 2, 3})

    def test_metal_alloy_accepts_flat_and_canonical_params(self):
        self.assertEqual(metal_alloy_from_params({"metal_alloy": "18k_white"}), "18k_white")
        self.assertEqual(
            metal_alloy_from_params({"metal": {"alloy": "14k yellow gold"}}),
            "14k yellow gold",
        )
        self.assertEqual(metal_alloy_from_params({}), "14k_yellow")

    def test_invalid_setting_class_four_rejected(self):
        self.manifest["parts"][-1]["mask_class"] = 4
        self.path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "Invalid material/mask mapping"):
            load_render_manifest(self.path)

    def test_receipt_mismatch_rejected(self):
        self.receipt["export_id"] = "other"
        (self.root / "export_receipt.json").write_text(json.dumps(self.receipt))
        with self.assertRaisesRegex(ValueError, "disagree"):
            load_render_manifest(self.path)

    def test_cli_requires_exactly_one_geometry_source(self):
        parser = build_parser()
        shared = [
            "--config-name",
            "cfg",
            "--params",
            "params.json",
            "--hdri-dir",
            "hdri",
            "--output-dir",
            "output",
        ]
        args = parser.parse_args([*shared, "--stl", "model.stl"])
        self.assertEqual(args.stl, Path("model.stl"))
        self.assertEqual(args.resolution, 1024)
        self.assertFalse(args.studio_light)
        args = parser.parse_args([*shared, "--geometry-manifest", "geometry_manifest.json"])
        self.assertEqual(args.geometry_manifest, Path("geometry_manifest.json"))
        args = parser.parse_args(
            [
                *shared,
                "--geometry-manifest",
                "geometry_manifest.json",
                "--studio-light",
                "--resolution",
                "128",
            ]
        )
        self.assertTrue(args.studio_light)
        self.assertEqual(args.resolution, 128)
        with self.assertRaises(SystemExit):
            parser.parse_args(shared)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [*shared, "--stl", "model.stl", "--geometry-manifest", "manifest.json"]
            )


if __name__ == "__main__":
    unittest.main()
