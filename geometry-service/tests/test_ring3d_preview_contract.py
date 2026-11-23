import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Ring3DPreviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "fast_mvp/static/ring3d.js").read_text(encoding="utf-8")

    def test_head_is_rotated_as_one_tangent_assembly(self):
        self.assertIn("headGroup.rotation.x = -Math.PI / 2", self.js)
        self.assertIn("headGroup.add(centerStone)", self.js)
        self.assertIn("addCylinderBetween(headGroup", self.js)
        self.assertIn("group.add(headGroup)", self.js)

    def test_preview_has_connected_shoulders_and_restrained_product_lighting(self):
        self.assertIn("addShoulder(group", self.js)
        self.assertIn("new THREE.CatmullRomCurve3(points)", self.js)
        self.assertIn("const SETTLE_DELAY_MS = 900", self.js)
        self.assertIn("quality === 'settled' && composer", self.js)
        self.assertNotIn("emissiveIntensity", self.js)


if __name__ == "__main__":
    unittest.main()
