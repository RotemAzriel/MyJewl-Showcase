import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from extract_params import main


class ExtractParamsCliTests(unittest.TestCase):
    def test_cli_writes_flat_parameters_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / "ring.png"
            output_path = root / "parameters.json"
            Image.new("RGB", (128, 128), (230, 205, 150)).save(image_path)

            code = main(
                [
                    str(image_path),
                    "--stone-shape",
                    "round",
                    "--stone-length-mm",
                    "6.5",
                    "--stone-width-mm",
                    "6.5",
                    "--stone-depth-mm",
                    "3.9",
                    "--ring-size-us",
                    "6.5",
                    "--setting",
                    "prong",
                    "--band-width-mm",
                    "2.0",
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(code, 0)
            payload = json.loads(output_path.read_text())
            self.assertEqual(payload["stone_shape"], "round")
            self.assertEqual(payload["stone_length_mm"], 6.5)
            self.assertGreaterEqual(payload["band_width_mm"], 1.8)
            self.assertGreaterEqual(payload["band_thickness_mm"], 1.5)


if __name__ == "__main__":
    unittest.main()
