import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "fast_mvp" / "static"
VENDOR = STATIC / "vendor" / "three-r166"


def node_json(source: str) -> object:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class PreviewProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile_uri = (STATIC / "preview_profiles_v1.js").as_uri()
        cls.ring = (STATIC / "ring3d.js").read_text(encoding="utf-8")

    def test_profiles_are_explicit_versioned_and_dimensionless(self):
        payload = node_json(
            f"""
            import {{ PREVIEW_PROFILE_VERSION, FACET_PROFILES }} from {self.profile_uri!r};
            console.log(JSON.stringify({{version: PREVIEW_PROFILE_VERSION, profiles: FACET_PROFILES}}));
            """
        )
        self.assertEqual(payload["version"], "myjewl-preview-facets-v1")
        self.assertEqual(set(payload["profiles"]), {"round", "oval", "pear"})
        for shape, profile in payload["profiles"].items():
            self.assertIn(shape, profile["id"])
            self.assertGreaterEqual(profile["outlineSegments"], 16)
            self.assertAlmostEqual(
                profile["crownHeightRatio"]
                + profile["girdleThicknessRatio"]
                + profile["pavilionDepthRatio"],
                1.0,
            )
            self.assertGreater(profile["tableRatio"], profile["culetRatio"])
        self.assertNotIn("length_mm", json.dumps(payload))
        self.assertNotIn("carat", json.dumps(payload))

    def test_closed_faceted_mesh_declares_every_required_region(self):
        for marker in (
            "capRing(vertices, table, crownZ, true)",
            "connectRings(vertices, table, crownZ, star, starZ)",
            "connectRings(vertices, outline, girdleTopZ, outline, girdleBottomZ)",
            "connectRings(vertices, pavilionBreak, pavilionBreakZ, culet, culetZ)",
            "capRing(vertices, culet, culetZ, false)",
            "regions: ['table', 'crown', 'girdle', 'pavilion', 'culet']",
            "closed: true",
            "dimensionsSource: 'accepted_preview_contract'",
        ):
            self.assertIn(marker, self.ring)
        self.assertNotIn("vertexColors", self.ring)
        self.assertNotIn("ExtrudeGeometry", self.ring)

    def test_geometry_signature_excludes_metal_and_finish(self):
        payload = node_json(
            f"""
            import {{ previewGeometrySignature, previewMaterialSignature }} from {self.profile_uri!r};
            const base = {{architecture:'solitaire',stone_shape:'round',stone_dimensions_mm:{{length:6.5,width:6.5,depth:3.9}},ring_size_us:6.5,band_width_mm:2,band_thickness_mm:1.6,metal_family:'yellow',metal_finish:'polished'}};
            const overlay = {{...base,metal_family:'rose',metal_finish:'brushed'}};
            const changed = {{...base,band_width_mm:2.4}};
            console.log(JSON.stringify({{sameGeometry:previewGeometrySignature(base)===previewGeometrySignature(overlay),differentGeometry:previewGeometrySignature(base)!==previewGeometrySignature(changed),differentMaterial:previewMaterialSignature(base)!==previewMaterialSignature(overlay)}}));
            """
        )
        self.assertEqual(
            payload,
            {"sameGeometry": True, "differentGeometry": True, "differentMaterial": True},
        )

    def test_materials_are_bounded_physical_visual_presets(self):
        payload = node_json(
            f"""
            import {{ METAL_PRESETS, FINISH_PRESETS }} from {self.profile_uri!r};
            console.log(JSON.stringify({{metals:METAL_PRESETS,finishes:FINISH_PRESETS}}));
            """
        )
        self.assertEqual(set(payload["metals"]), {"yellow", "white", "rose"})
        self.assertEqual(set(payload["finishes"]), {"polished", "matte", "brushed"})
        self.assertEqual(len({item["color"] for item in payload["metals"].values()}), 3)
        self.assertGreater(
            payload["finishes"]["matte"]["roughness"],
            payload["finishes"]["polished"]["roughness"],
        )
        self.assertGreater(payload["finishes"]["brushed"]["anisotropy"], 0.5)
        self.assertIn("ior: 2.417", self.ring)
        self.assertIn("dispersion: 0.012", self.ring)
        self.assertIn("diamond-internal-return-v1", self.ring)


class PreviewAssetAndFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ring = (STATIC / "ring3d.js").read_text(encoding="utf-8")
        cls.fixture = (STATIC / "catalog_fixture.js").read_text(encoding="utf-8")

    def test_renderer_assets_are_local_and_licensed(self):
        expected = (
            "three.module.min.js",
            "postprocessing/EffectComposer.js",
            "postprocessing/RenderPass.js",
            "postprocessing/UnrealBloomPass.js",
            "postprocessing/BokehPass.js",
            "postprocessing/Pass.js",
            "postprocessing/ShaderPass.js",
            "postprocessing/MaskPass.js",
            "shaders/CopyShader.js",
            "shaders/LuminosityHighPassShader.js",
            "shaders/BokehShader.js",
            "LICENSE",
        )
        for relative in expected:
            self.assertTrue((VENDOR / relative).is_file(), relative)
        self.assertIn("MIT License", (VENDOR / "LICENSE").read_text(encoding="utf-8"))

    def test_local_vendor_import_graph_is_complete(self):
        import_pattern = re.compile(r"(?:from\s+|import\s*)['\"]([^'\"]+)['\"]")
        for module in VENDOR.rglob("*.js"):
            source = module.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"(?:from\s+|import\s*\()['\"]https?://")
            for specifier in import_pattern.findall(source):
                if specifier == "three":
                    continue
                self.assertTrue(specifier.startswith("."), (module, specifier))
                self.assertTrue((module.parent / specifier).resolve().is_file(), (module, specifier))

    def test_core_frontends_have_no_remote_runtime_dependency(self):
        for name in (
            "ring3d.js", "preview_profiles_v1.js", "index.html", "studio.html",
            "manual.html", "catalog_fixture.html",
        ):
            source = (STATIC / name).read_text(encoding="utf-8")
            # A local base is used only so Node can parse relative, same-origin
            # asset URLs in non-browser tests; it is never fetched.
            source = source.replace("'http://localhost/'", "")
            self.assertNotRegex(source, r"https?://", name)
        for html in ("index.html", "studio.html", "manual.html", "catalog_fixture.html"):
            source = (STATIC / html).read_text(encoding="utf-8")
            self.assertIn('/static/vendor/three-r166/three.module.min.js', source)
            self.assertIn('/static/favicon.svg', source)
        self.assertTrue((STATIC / "favicon.svg").is_file())

    def test_hybrid_quality_and_fallback_are_explicit(self):
        for marker in (
            "const SETTLE_DELAY_MS = 900",
            "applyQuality('interactive')",
            "applyQuality('settled')",
            "quality === 'settled' && composer",
            "createFallbackApi",
            "forced_for_verification",
            "fallback-2d",
            "myjewl-preview-quality",
            "const PURPOSE_OPERATIONAL_AID = 'operational_aid'",
            "const PURPOSE_PRODUCTION_PREVIEW = 'production_preview'",
            "createProductionUnavailableApi",
            "verifiedAssetPayloadPromises.delete(cacheKey)",
            "if (token !== rebuildToken) return",
            "return productionPreview ? rebuildProduction(true) : Promise.resolve(false)",
            "let webglContextAvailable = true",
            "renderer.getContext?.().isContextLost?.() === true",
            "reasonCode !== 'webgl_context_lost'",
            "canvas.addEventListener('webglcontextlost'",
            "canvas.removeEventListener('webglcontextlost'",
            "awaitingFirstProductionFrame && ringGroup",
        ):
            self.assertIn(marker, self.ring)
        self.assertIn("loadVerifiedAssetPayload", self.ring)
        self.assertIn("fetch(sameOriginAssetUrl(fileReference.url)", self.ring)
        self.assertIn("clearRingGroup()", self.ring)
        self.assertIn("displayedPreviewStatus = 'unavailable'", self.ring)
        self.assertNotIn("procedural_fallback", self.ring)
        self.assertNotIn("productionPreview\n      ? createFallbackApi", self.ring)
        self.assertNotRegex(self.ring, r"fetch\([^)]*https?://")
        self.assertNotIn("/api/request-geometry", self.ring)
        self.assertNotIn("RhinoCompute", self.ring)

    def test_fixture_exposes_only_exact_bounded_trilogy_and_quality_flags(self):
        self.assertIn("params.get('reference') === 'trilogy'", self.fixture)
        self.assertIn("quantity: 2", self.fixture)
        self.assertIn("shape: 'pear'", self.fixture)
        self.assertIn("quantity: 14", self.fixture)
        self.assertIn("head_geometry: 'petal_prong'", self.fixture)
        self.assertIn("band_profile: 'knife_edge'", self.fixture)
        self.assertIn("forceFallback: params.get('fallback') === '1'", self.fixture)

    def test_renderer_diagnostics_cover_performance_and_memory(self):
        for marker in (
            "interactiveFps", "loadMs", "settledMs", "renderer.info.memory",
            "previewStoneCounts", "previewGeometryKey", "previewMaterial",
        ):
            self.assertIn(marker, self.ring)

    def test_cycles_reference_exports_the_actual_preview_scene_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(
                ["node", "scripts/export_preview_reference.mjs", temporary],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads(
                (Path(temporary) / "preview_reference_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(manifest["comparison_only"])
            self.assertFalse(manifest["manufacturing_authority"])
            self.assertEqual(
                [case["stone_counts"] for case in manifest["cases"]],
                [
                    {"center": 1, "side": 0, "accent": 0},
                    {"center": 1, "side": 2, "accent": 14},
                ],
            )
            for case in manifest["cases"]:
                obj = (Path(temporary) / case["obj"]).read_text(encoding="utf-8")
                self.assertEqual(obj.count("\no "), case["object_count"])
                self.assertEqual(obj.count("\nf "), case["triangle_count"])
                self.assertEqual(case["authority"], "preview_only_not_rhino")

    def test_cycles_tooling_imports_preview_triangles_without_product_generation(self):
        exporter = (ROOT / "scripts" / "export_preview_reference.mjs").read_text(
            encoding="utf-8"
        )
        blender = (ROOT / "scripts" / "blender_preview_reference.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("buildPreviewReference", exporter)
        self.assertIn("bpy.ops.wm.obj_import", blender)
        self.assertIn('scene.render.engine = "CYCLES"', blender)
        self.assertIn('"comparison_only": True', blender)
        self.assertNotIn("primitive_torus_add", blender)
        self.assertNotIn("request-geometry", exporter)


if __name__ == "__main__":
    unittest.main()
