import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.verify_mask_png import verify_mask


class VerifyMaskPngTests(unittest.TestCase):
    def test_exact_classes_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            image = Image.new("L", (4, 1))
            image.putdata([0, 1, 2, 3])
            image.save(path)
            report = verify_mask(path, {0, 1, 2, 3}, required_visible={1, 2, 3})
        self.assertEqual(report["observed_values"], [0, 1, 2, 3])
        self.assertEqual(report["required_visible_values"], [1, 2, 3])

    def test_intermediate_value_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            image = Image.new("L", (2, 1))
            image.putdata([0, 127])
            image.save(path)
            with self.assertRaisesRegex(ValueError, "intermediate"):
                verify_mask(path, {0, 1, 2, 3})

    def test_missing_required_visible_class_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mask.png"
            image = Image.new("L", (3, 1))
            image.putdata([0, 1, 2])
            image.save(path)
            with self.assertRaisesRegex(ValueError, "missing required visible"):
                verify_mask(path, {0, 1, 2, 3}, required_visible={1, 2, 3})


if __name__ == "__main__":
    unittest.main()
