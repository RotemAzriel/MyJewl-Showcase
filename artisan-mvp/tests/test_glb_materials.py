"""Tests for restyling an Artisan-style GLB (metal + gem meshes, one shared material)."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from jewelry_gaas_foundation.glb_materials import DIAMOND_IOR, metal_base_color, style_glb


def _artisan_like_glb(path: Path) -> None:
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.annulus(r_min=8, r_max=9.6, height=2), geom_name="Metal 01")
    scene.add_geometry(trimesh.creation.icosphere(radius=3), geom_name="Gem 01")
    path.write_bytes(scene.export(file_type="glb"))


def _json_chunk(path: Path) -> dict:
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + length])


class StyleGlbTest(unittest.TestCase):
    def test_materials_assigned_by_mesh_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ring.glb"
            _artisan_like_glb(path)
            before = trimesh.load(path, force="scene")
            counts = style_glb(path, "GOLD_ROSE_18")
            self.assertEqual(counts, {"metal": 1, "gem": 1})

            doc = _json_chunk(path)
            by_name = {m["name"]: m["primitives"][0]["material"] for m in doc["meshes"]}
            metal = doc["materials"][by_name["Metal 01"]]
            gem = doc["materials"][by_name["Gem 01"]]
            self.assertEqual(metal["pbrMetallicRoughness"]["metallicFactor"], 1.0)
            self.assertEqual(
                metal["pbrMetallicRoughness"]["baseColorFactor"][:3],
                list(metal_base_color("GOLD_ROSE_18")),
            )
            self.assertEqual(gem["extensions"]["KHR_materials_ior"]["ior"], DIAMOND_IOR)
            self.assertIn("KHR_materials_transmission", doc["extensionsUsed"])

            # geometry is untouched and the file still loads
            after = trimesh.load(path, force="scene")
            self.assertAlmostEqual(after.area, before.area, places=6)
            self.assertTrue(np.allclose(after.bounds, before.bounds))
            self.assertEqual(path.stat().st_size % 4, 0)

    def test_each_gem_mesh_gets_its_own_diamond_material(self):
        # Pave builds export the melee mesh BEFORE the center stone ("Gem 02" first).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pave.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.icosphere(radius=0.6), geom_name="Gem 02")
            scene.add_geometry(trimesh.creation.annulus(r_min=8, r_max=9.6, height=2),
                               geom_name="Metal 01")
            scene.add_geometry(trimesh.creation.icosphere(radius=3), geom_name="Gem 01")
            path.write_bytes(scene.export(file_type="glb"))
            self.assertEqual(style_glb(path, "GOLD_YELLOW_14"), {"metal": 1, "gem": 2})
            doc = _json_chunk(path)
            by_name = {m["name"]: m["primitives"][0]["material"] for m in doc["meshes"]}
            self.assertNotEqual(by_name["Gem 01"], by_name["Gem 02"])
            for gem in ("Gem 01", "Gem 02"):
                material = doc["materials"][by_name[gem]]
                self.assertEqual(material["extensions"]["KHR_materials_ior"]["ior"], DIAMOND_IOR)
            # restyling again is idempotent
            style_glb(path, "GOLD_YELLOW_14")
            self.assertEqual(len(_json_chunk(path)["materials"]), 3)

    def test_metal_families(self):
        self.assertEqual(metal_base_color("GOLD_YELLOW_14"), metal_base_color("GOLD_YELLOW_18"))
        self.assertNotEqual(metal_base_color("GOLD_YELLOW_14"), metal_base_color("PLATINUM_950"))
        self.assertEqual(len(metal_base_color("UNOBTAINIUM")), 3)

    def test_rejects_non_glb(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.glb"
            path.write_bytes(b"solid not-a-glb")
            with self.assertRaises(ValueError):
                style_glb(path, "PLATINUM")


if __name__ == "__main__":
    unittest.main()
