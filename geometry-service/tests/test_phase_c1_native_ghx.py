import base64
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from manual.tools.build_phase_c_native_ghx import build


class PhaseC1NativeGhxTests(unittest.TestCase):
    def test_definition_contains_stable_rh_in_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase_c1.ghx"
            build(path)
            root = ElementTree.fromstring(path.read_bytes())

        names = {item.text for item in root.iter("item") if item.text}
        self.assertTrue({
            "RH_IN:json_string",
            "RH_IN:job_id",
            "RH_OUT:response_json",
        }.issubset(names))

    def test_definition_uses_native_python3_script_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase_c1.ghx"
            build(path)
            root = ElementTree.fromstring(path.read_bytes())

        scripts = [chunk for chunk in root.iter("chunk") if chunk.attrib.get("name") == "Script"]
        self.assertEqual(len(scripts), 1)
        source_items = [item for item in scripts[0].iter("item") if item.attrib.get("name") == "Text"]
        self.assertEqual(len(source_items), 1)
        self.assertIn("def _run():", base64.b64decode(source_items[0].text or "").decode("utf-8"))
        item_names = {item.attrib.get("name") for item in scripts[0].iter("item")}
        self.assertIn("Text", item_names)
        self.assertNotIn("CodeInput", item_names)
        self.assertEqual(len([c for c in root.iter("chunk") if c.attrib.get("name") == "LanguageSpec"]), 1)
        self.assertEqual(
            {item.text for item in root.iter("item") if item.attrib.get("name") == "GUID"},
            {"719467e6-7cf5-4848-99b0-c5dd57e5442c", "3EDE854E-C753-40EB-84CB-B48008F14FD4", "c552a431-af5b-46a9-a8a4-0fcbc27ef596"},
        )


if __name__ == "__main__":
    unittest.main()
