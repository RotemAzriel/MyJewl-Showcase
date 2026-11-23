import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.blender_manifest_smoke import (
    build_blender_command,
    verify_render_artifacts,
)


class BlenderManifestSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("band.stl", "stone.stl", "prongs.stl"):
            (self.root / name).write_bytes(b"mesh")
        (self.root / "export_receipt.json").write_text(
            json.dumps(
                {
                    "job_id": "job_solitaire_001",
                    "export_id": "export-1",
                }
            ),
            encoding="utf-8",
        )
        (self.root / "canonical_spec.json").write_text(
            json.dumps({"metal": {"alloy": "14k yellow gold"}}),
            encoding="utf-8",
        )
        (self.root / "geometry_manifest.json").write_text(
            json.dumps(
                {
                    "job_id": "job_solitaire_001",
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
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.output = self.root / "blender_smoke"
        (self.output / "renders").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_artifacts(self, mask_values):
        renders = self.output / "renders"
        (renders / "view_0001.png").write_bytes(b"rgb")
        (renders / "depth_0001.exr").write_bytes(b"depth")
        (renders / "normal_0001.exr").write_bytes(b"normal")
        image = Image.new("L", (len(mask_values), 1))
        image.putdata(mask_values)
        image.save(renders / "mask_0001.png")

    def test_verify_render_artifacts_requires_all_class_ids(self):
        self._write_artifacts([0, 1, 2, 3])
        report = verify_render_artifacts(bundle_dir=self.root, output_dir=self.output)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["mask"]["observed_values"], [0, 1, 2, 3])
        self.assertEqual(report["mask"]["required_visible_values"], [1, 2, 3])

    def test_verify_render_artifacts_rejects_missing_prong_class(self):
        self._write_artifacts([0, 1, 2])
        with self.assertRaisesRegex(ValueError, "missing required visible"):
            verify_render_artifacts(bundle_dir=self.root, output_dir=self.output)

    def test_build_command_uses_canonical_spec_and_studio_light(self):
        command = build_blender_command(
            blender_bin="/bin/blender",
            bundle_dir=self.root,
            output_dir=self.output,
            samples=4,
            resolution=128,
            hdri_dir=None,
            studio_light=True,
        )
        self.assertIn("--geometry-manifest", command)
        self.assertIn(str(self.root / "geometry_manifest.json"), command)
        self.assertIn("--params", command)
        self.assertIn(str(self.root / "canonical_spec.json"), command)
        self.assertIn("--studio-light", command)
        self.assertIn("128", command)


if __name__ == "__main__":
    unittest.main()
