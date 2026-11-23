"""Goal 0025 — MyJewl delivers the geometry it can prove, or refuses and says why.

These tests are adversarial on purpose. The failure this guards against is not a
crash: it is a customer receiving a file that looks manufacturable and is not --
the procedural draft, a neighbouring size, a hand-edited table's nomination, or
inside-out parts handed to a shop that never said it accepts them.

The shipped state is a refusal, and that is asserted as firmly as the success.
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp import geometry_delivery as gd
from fast_mvp import manual_configurator as mc
from fast_mvp import quote_volumes
from fast_mvp.preview_resolution import load_registry
from jewelry_gaas_foundation import shop_profile as sp

#: The four promoted configurations, as a customer would type them.
PROMOTED = {
    "goal0011-v4-full-ring": {
        "ring_size_us": 6.5,
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "head_style_id": "head_v4_mid_petal_4_prong",
        "band_style_id": "band_v4_split_solitaire",
    },
    "goal0014-v5-us4_round5": {
        "ring_size_us": 4,
        "stone_length_mm": 5.0,
        "stone_width_mm": 5.0,
        "stone_depth_mm": 3.0,
        "head_style_id": "head_v5_parametric_petal_4_prong",
        "band_style_id": "band_v5_parametric_split_solitaire",
    },
    "goal0014-v5-us6p5_round6p5": {
        "ring_size_us": 6.5,
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "head_style_id": "head_v5_parametric_petal_4_prong",
        "band_style_id": "band_v5_parametric_split_solitaire",
    },
    "goal0014-v5-us9_round8": {
        "ring_size_us": 9,
        "stone_length_mm": 8.0,
        "stone_width_mm": 8.0,
        "stone_depth_mm": 4.8,
        "head_style_id": "head_v5_parametric_petal_4_prong",
        "band_style_id": "band_v5_parametric_split_solitaire",
    },
}

CONTROL = "goal0014-v5-us6p5_round6p5"

#: A shop that accepts what we actually have: unfused, inside-out STLs in mm.
PERMISSIVE_MESH = {
    "preferred_format": "stl",
    "max_chord_deviation_mm": None,
    "max_triangle_count": None,
    "single_solid_required": False,
    "intersecting_solids_accepted": True,
    "outward_normals_required": False,
    "inverted_normals_are_silently_repaired": False,
    "watertight_required": True,
    "units_assumed": "mm",
}


def _configure(overrides: dict) -> dict:
    payload = {
        "stone_shape": "round",
        "metal_color_family": "yellow",
        "metal_purity": "18k",
        **overrides,
    }
    return mc.configure(payload)


def _profile(mesh: dict | None = None, **overrides) -> sp.ShopProfile:
    document = json.loads(
        (ROOT / "profiles" / "shop_profile_uncalibrated_template_v0_1.json").read_text(
            encoding="utf-8"
        )
    )
    document["profile_id"] = "test-shop"
    document["status"] = "calibrated"
    document["validity"] = {
        "valid_from": "2026-08-01",
        "expires_on": "2099-01-01",
        "revalidation_triggers": ["test"],
    }
    document["mesh"] = dict(mesh if mesh is not None else PERMISSIVE_MESH)
    document.update(overrides)
    return sp.ShopProfile(document)


class ProfileTestCase(unittest.TestCase):
    """Every test starts from the shipped state: no profile, nothing declared."""

    def setUp(self) -> None:
        sp.clear_active_profile()
        gd.clear_cache()
        quote_volumes.clear_cache()
        self.addCleanup(sp.clear_active_profile)
        self.addCleanup(gd.clear_cache)
        self.addCleanup(quote_volumes.clear_cache)

    def decide(self, asset_id: str = CONTROL, **kwargs):
        report = _configure(PROMOTED[asset_id])
        return gd.decide(
            report["preview_manifest"], quote=report.get("quote"), **kwargs
        )


class TableTests(ProfileTestCase):
    """The committed table describes bytes, and only bytes a receipt vouches for."""

    def test_shipped_table_is_valid_and_pins_its_source(self):
        table = gd.load_table()
        gd.validate_table(table)
        self.assertEqual(table["delivery_source"], gd.DELIVERY_SOURCE)
        self.assertEqual(
            table["authority"], "measurement_only_not_manufacturing_certification"
        )
        self.assertEqual(
            table["manufacturing_gates"], "pending_not_run_needs_calibration"
        )

    def test_table_covers_exactly_the_promoted_approved_exact_assets(self):
        """Promoting without regenerating must turn the suite red, not ship a gap."""
        registry = load_registry()
        promoted = {
            asset["asset_id"]
            for asset in registry["assets"]
            if asset["enabled"]
            and asset["approval"]["status"] == "approved"
            and asset["approval"]["visual_review"] == "approved"
            and asset["classification"] == "exact"
        }
        recorded = {record["asset_id"] for record in gd.load_table()["records"]}
        self.assertEqual(promoted, recorded)

    def test_table_lists_no_file_the_receipt_does_not_hash(self):
        """model.stl, the .3dm and the .obj are absent by design, not omission."""
        for record in gd.load_table()["records"]:
            names = {item["file"] for item in record["files"]}
            self.assertNotIn("model.stl", names, record["asset_id"])
            self.assertTrue(
                all(name.endswith(".stl") for name in names), record["asset_id"]
            )
            self.assertFalse(
                [name for name in names if name.endswith((".obj", ".3dm"))],
                record["asset_id"],
            )

    def test_every_shipped_record_still_carries_f1_and_f2(self):
        """The state this repository is in, asserted so a silent fix is visible."""
        for record in gd.load_table()["records"]:
            self.assertEqual(record["inverted_part_count"], 12, record["asset_id"])
            self.assertFalse(record["ships_castable_solid"], record["asset_id"])
            self.assertEqual(record["metal_part_count"], 19, record["asset_id"])
            codes = {finding["code"] for finding in record["findings"]}
            self.assertEqual(codes, {"INVERTED_NORMALS", "PARTS_NOT_UNIONED"})

    def test_a_table_nominating_another_source_is_refused(self):
        table = copy.deepcopy(gd.load_table())
        table["delivery_source"] = "locally_computed_mesh_union"
        with self.assertRaises(gd.DeliveryError):
            gd.validate_table(table)

    def test_inverted_count_must_agree_with_the_measured_per_file_flags(self):
        table = copy.deepcopy(gd.load_table())
        table["records"][0]["inverted_part_count"] = 0
        table["records"][0]["inverted_parts"] = []
        with self.assertRaises(gd.DeliveryError) as caught:
            gd.validate_table(table)
        self.assertIn("orientation flags", str(caught.exception))

    def test_metal_count_must_agree_with_the_file_list(self):
        table = copy.deepcopy(gd.load_table())
        table["records"][0]["metal_part_count"] = 18
        with self.assertRaises(gd.DeliveryError):
            gd.validate_table(table)

    def test_duplicate_signature_is_refused(self):
        table = copy.deepcopy(gd.load_table())
        table["records"].append(copy.deepcopy(table["records"][0]))
        table["records"][-1]["asset_id"] = "other"
        with self.assertRaises(gd.DeliveryError):
            gd.validate_table(table)

    def test_a_missing_table_delivers_nothing_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = gd.load_table(pathlib.Path(tmp) / "absent.json")
        self.assertEqual(table["records"], [])
        self.assertTrue(table["missing"])


class JoinTests(ProfileTestCase):
    """A delivery joins to one exact configuration or to nothing."""

    def test_an_unsolved_in_envelope_size_is_refused(self):
        decision = gd.decide(
            _configure(
                {
                    "ring_size_us": 5,
                    "stone_length_mm": 5.0,
                    "stone_width_mm": 5.0,
                    "stone_depth_mm": 3.0,
                    "head_style_id": "head_v5_parametric_petal_4_prong",
                    "band_style_id": "band_v5_parametric_split_solitaire",
                }
            )["preview_manifest"],
            quote={"metal_weight_by_alloy": [{"alloy": "18k_yellow"}]},
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "preview_not_exact")

    def test_an_estimated_preview_is_refused(self):
        report = _configure(PROMOTED[CONTROL])
        manifest = copy.deepcopy(report["preview_manifest"])
        manifest["preview_status"] = "estimated"
        decision = gd.decide(manifest, quote=report["quote"])
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "preview_not_exact")

    def test_a_table_that_drifted_behind_the_registry_delivers_nothing(self):
        report = _configure(PROMOTED[CONTROL])
        manifest = copy.deepcopy(report["preview_manifest"])
        manifest["selected_asset"]["asset_version"] = "v99.0.0"
        decision = gd.decide(manifest, quote=report["quote"])
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "no_deliverable_record")

    def test_an_unknown_signature_gets_nothing_adjacent(self):
        report = _configure(PROMOTED[CONTROL])
        manifest = copy.deepcopy(report["preview_manifest"])
        manifest["geometry"]["signature"] = "sha256:" + "0" * 64
        decision = gd.decide(manifest, quote=report["quote"])
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "no_deliverable_record")

    def test_no_preview_manifest_at_all_is_refused(self):
        self.assertEqual(gd.decide(None).reason, "no_preview_manifest")

    def test_geometry_never_travels_without_its_weight(self):
        sp.set_active_profile(_profile())
        report = _configure(PROMOTED[CONTROL])
        decision = gd.decide(report["preview_manifest"], quote={"status": "pending"})
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "no_metal_weight_table")


class ShippedRefusalTests(ProfileTestCase):
    """The state this repository ships in: every delivery refuses, and says why."""

    def test_every_promoted_configuration_refuses_today(self):
        for asset_id in PROMOTED:
            with self.subTest(asset=asset_id):
                decision = self.decide(asset_id)
                self.assertFalse(decision.deliverable)
                self.assertEqual(decision.reason, "mesh_policy_undeclared")

    def test_the_refusal_names_f1_f2_and_the_undeclared_mesh_block(self):
        decision = self.decide()
        self.assertIn("12 of 19 metal parts", decision.detail)
        self.assertIn("F1", decision.detail)
        self.assertIn("F2", decision.detail)
        self.assertIn("property of the receiving shop", decision.detail)
        codes = {finding["code"] for finding in decision.findings}
        self.assertEqual(codes, {"INVERTED_NORMALS", "PARTS_NOT_UNIONED"})

    def test_a_refusal_exposes_no_files_and_cannot_be_built(self):
        decision = self.decide()
        payload = decision.to_json()
        self.assertNotIn("files", payload)
        self.assertFalse(payload["certifies_manufacturing"])
        with self.assertRaises(gd.DeliveryError):
            gd.build_archive(decision)

    def test_the_shipped_uncalibrated_template_declares_nothing(self):
        sp.set_active_profile(sp.load_profile(sp.UNCALIBRATED_TEMPLATE_PATH))
        policy = sp.active_mesh_policy()
        self.assertFalse(policy.declared)
        self.assertEqual(policy.reason, "profile_uncalibrated")
        self.assertEqual(self.decide().reason, "mesh_policy_undeclared")

    def test_health_state_reports_the_refusal_rather_than_looking_empty(self):
        state = gd.delivery_state()
        self.assertEqual(state["status"], "no_active_profile")
        self.assertEqual(state["deliverable_assets_recorded"], 4)
        self.assertFalse(state["mesh_policy"]["declared"])
        self.assertFalse(state["certifies_manufacturing"])


class MeshPolicyTests(ProfileTestCase):
    """A shop's export policy is read, never invented."""

    def test_an_expired_profile_is_not_reused(self):
        sp.set_active_profile(
            _profile(
                validity={
                    "valid_from": "2026-01-01",
                    "expires_on": "2026-01-02",
                    "revalidation_triggers": ["test"],
                }
            )
        )
        policy = sp.active_mesh_policy()
        self.assertFalse(policy.declared)
        self.assertEqual(policy.reason, "profile_expired")

    def test_a_half_filled_mesh_block_is_not_a_policy(self):
        mesh = dict(PERMISSIVE_MESH, intersecting_solids_accepted=None)
        sp.set_active_profile(_profile(mesh))
        policy = sp.active_mesh_policy()
        self.assertFalse(policy.declared)
        self.assertEqual(policy.reason, "mesh_fields_undeclared")
        self.assertIn("intersecting_solids_accepted", policy.missing_fields)

    def test_unknown_reads_as_undeclared_not_as_a_value(self):
        mesh = dict(PERMISSIVE_MESH, units_assumed="unknown")
        sp.set_active_profile(_profile(mesh))
        self.assertIn("units_assumed", sp.active_mesh_policy().missing_fields)

    @unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
    def test_a_declared_policy_can_never_flip_a_manufacturing_gate(self):
        """Goal 0013's invariant, restated where a delivery could erode it."""
        sp.set_active_profile(_profile())
        decision = self.decide()
        payload = decision.to_json()
        self.assertTrue(payload["deliverable"])
        self.assertFalse(payload["certifies_manufacturing"])
        self.assertEqual(
            payload["manufacturing_gates"], "pending_not_run_needs_calibration"
        )
        self.assertFalse(decision.policy.to_json()["certifies_manufacturing"])


class PolicyRefusalTests(ProfileTestCase):
    """What we have, checked against what each shop said it accepts."""

    def _decide_with(self, **mesh_overrides):
        sp.set_active_profile(_profile(dict(PERMISSIVE_MESH, **mesh_overrides)))
        return self.decide()

    def test_a_shop_requiring_outward_normals_is_refused_on_f1(self):
        decision = self._decide_with(outward_normals_required=True)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "shop_requires_outward_normals")
        self.assertIn("12 of 19", decision.detail)

    def test_silent_repair_does_not_authorise_shipping_a_known_defect(self):
        decision = self._decide_with(
            outward_normals_required=True,
            inverted_normals_are_silently_repaired=True,
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "shop_requires_outward_normals")
        self.assertIn("not a reason to keep not noticing", decision.detail)

    def test_a_shop_requiring_one_solid_is_refused_on_f2(self):
        decision = self._decide_with(single_solid_required=True)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "single_solid_required_but_absent")

    def test_a_shop_rejecting_intersecting_solids_is_refused_on_f2(self):
        decision = self._decide_with(intersecting_solids_accepted=False)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "shop_rejects_intersecting_solids")
        self.assertIn("no boolean union at all (F2)", decision.detail)

    def test_a_union_in_the_bundle_does_not_hide_the_parts_beside_it(self):
        """A future V6 bundle still delivers the named parts, so this stays refused.

        The delivered set is the whole receipt-hashed list. Sending only the fused
        solid is a coherent behaviour and deliberately unimplemented: no promoted
        asset emits a union, so it cannot be tested against real bytes.
        """
        import dataclasses

        record = next(
            item
            for item in gd.load_table()["records"]
            if item["asset_id"] == CONTROL
        )
        asset = gd._loaded(gd.DEFAULT_TABLE_PATH)[1][record["geometry_signature"]]
        pretend_v6 = dataclasses.replace(
            asset, ships_castable_solid=True, castable_solid_file="castable_solid.stl"
        )
        policy = _profile(
            dict(PERMISSIVE_MESH, intersecting_solids_accepted=False)
        ).mesh_policy()
        refusal = gd._policy_refusals(pretend_v6, policy)
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal[0], "shop_rejects_intersecting_solids")
        self.assertIn("castable_solid.stl", refusal[1])

    def test_a_shop_reading_inches_is_refused(self):
        decision = self._decide_with(units_assumed="inch")
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "units_mismatch")

    def test_a_shop_wanting_a_format_the_receipt_does_not_hash_is_refused(self):
        decision = self._decide_with(preferred_format="3dm")
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "preferred_format_unavailable")
        self.assertIn("no receipt vouches for", decision.detail)

    def test_a_triangle_budget_is_enforced(self):
        decision = self._decide_with(max_triangle_count=1000)
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "triangle_budget_exceeded")

    @unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
    def test_an_unverifiable_chord_tolerance_warns_rather_than_claiming_compliance(self):
        decision = self._decide_with(max_chord_deviation_mm=0.005)
        self.assertTrue(decision.deliverable)
        self.assertTrue(
            any("not verified" in warning for warning in decision.warnings),
            decision.warnings,
        )


@unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
class DeliverableTests(ProfileTestCase):
    """The success path, and everything that travels with it."""

    def setUp(self) -> None:
        super().setUp()
        sp.set_active_profile(_profile())
        self.report = _configure(PROMOTED[CONTROL])
        self.decision = gd.decide(
            self.report["preview_manifest"], quote=self.report["quote"]
        )
        self.assertTrue(self.decision.deliverable, self.decision.detail)

    def test_the_known_defects_travel_with_the_accepted_delivery(self):
        warnings = " ".join(self.decision.warnings)
        self.assertIn("F1 travels with this delivery", warnings)
        self.assertIn("F2 travels with this delivery", warnings)
        self.assertIn("reference_only_not_for_casting", warnings)

    def test_the_archive_is_deterministic(self):
        first, first_hash = gd.build_archive(self.decision, quote=self.report["quote"])
        second, second_hash = gd.build_archive(self.decision, quote=self.report["quote"])
        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(hashlib.sha256(first).hexdigest(), first_hash)

    def test_every_archived_file_hash_matches_the_receipt(self):
        import io
        import zipfile

        blob, _ = gd.build_archive(self.decision, quote=self.report["quote"])
        archive = zipfile.ZipFile(io.BytesIO(blob))
        self.assertIsNone(archive.testzip())
        manifest = json.loads(archive.read("delivery_manifest.json"))
        for item in manifest["files"]:
            payload = archive.read(f"{gd._ARCHIVE_DIRS[item['role']]}/{item['file']}")
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), item["sha256"], item["file"]
            )

    def test_todays_archive_has_no_fused_solid_because_none_exists(self):
        """The empty `fused/` directory is finding F2, expressed as a file layout."""
        import io
        import zipfile

        blob, _ = gd.build_archive(self.decision, quote=self.report["quote"])
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        self.assertFalse([name for name in names if name.startswith("fused/")], names)
        self.assertEqual(
            len([name for name in names if name.startswith("geometry/")]), 19
        )

    def test_the_archive_carries_the_receipt_so_nobody_has_to_trust_us(self):
        import io
        import zipfile

        blob, _ = gd.build_archive(self.decision, quote=self.report["quote"])
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        self.assertTrue(
            any(name.startswith("provenance/") for name in names), names
        )
        self.assertIn("delivery_manifest.json", names)

    def test_the_manifest_carries_the_weight_in_every_alloy(self):
        manifest = gd.delivery_manifest(self.decision, quote=self.report["quote"])
        rows = manifest["metal_weight_by_alloy"]
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertIn("metal_weight_g", row)
            self.assertIn("metal_weight_dwt", row)
            self.assertIn("spg", row)
        self.assertEqual(
            [row["display_name"] for row in rows if row.get("selected")],
            ["18k yellow gold"],
        )

    def test_the_manifest_carries_the_measured_volume_and_names_what_is_not_the_basis(self):
        manifest = gd.delivery_manifest(self.decision, quote=self.report["quote"])
        volume = manifest["measured_volume"]
        self.assertEqual(volume["basis"], "measured_mesh_union")
        self.assertAlmostEqual(volume["union_mm3"], 162.234, places=3)
        self.assertEqual(volume["parts_sum_role"], "evidence_only_never_the_basis")

    def test_the_manifest_says_it_is_not_rhino_geometry(self):
        manifest = gd.delivery_manifest(self.decision, quote=self.report["quote"])
        self.assertIn("not_rhino_geometry", manifest["document_authority"])
        self.assertFalse(manifest["certifies_manufacturing"])
        self.assertEqual(
            manifest["manufacturing_gates"], "pending_not_run_needs_calibration"
        )
        codes = {f["code"] for f in manifest["geometry_state"]["findings"]}
        self.assertEqual(codes, {"INVERTED_NORMALS", "PARTS_NOT_UNIONED"})


@unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
class ByteVerificationTests(ProfileTestCase):
    """The receipt is the authority. A table alone nominates nothing."""

    def setUp(self) -> None:
        super().setUp()
        sp.set_active_profile(_profile())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = copy.deepcopy(
            next(
                record
                for record in gd.load_table()["records"]
                if record["asset_id"] == CONTROL
            )
        )
        self.report = _configure(PROMOTED[CONTROL])

    def _table_with(self, record: dict) -> pathlib.Path:
        table = copy.deepcopy(gd.load_table())
        table["records"] = [record]
        path = pathlib.Path(self.tmp.name) / "table.json"
        path.write_text(json.dumps(table, indent=2, sort_keys=True), encoding="utf-8")
        gd.clear_cache()
        return path

    def _sandbox_bundle(self) -> pathlib.Path:
        """A writable copy of the real bundle, inside the repository root."""
        holding = pathlib.Path(
            tempfile.mkdtemp(prefix="goal0025_bundle_", dir=str(ROOT / "output"))
        )
        self.addCleanup(shutil.rmtree, holding, True)
        shutil.copytree(ROOT / self.record["bundle"], holding / "bundle")
        return holding / "bundle"

    def test_tampered_bytes_are_refused(self):
        bundle = self._sandbox_bundle()
        target = bundle / "shank.stl"
        target.write_bytes(target.read_bytes() + b"\x00")
        record = dict(self.record, bundle=str(bundle.relative_to(ROOT)))
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "bytes_do_not_match_receipt")
        self.assertIn("shank.stl", decision.detail)

    def test_a_missing_bundle_refuses_rather_than_delivering_nothing_quietly(self):
        record = dict(self.record, bundle="output/goal0025-not-a-real-bundle")
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "bundle_not_present")

    def test_a_doctored_table_cannot_nominate_a_file_the_receipt_omits(self):
        """The procedural draft's own true hash must not make it deliverable."""
        bundle = self._sandbox_bundle()
        draft = bundle / "draft_preview.stl"
        draft.write_bytes(b"solid draft\nendsolid draft\n")
        record = copy.deepcopy(self.record)
        record["bundle"] = str(bundle.relative_to(ROOT))
        record["files"].append(
            {
                "file": "draft_preview.stl",
                "role": "metal",
                "sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
                "byte_size": draft.stat().st_size,
                "triangles": 1,
                "vertices": 3,
                "watertight": True,
                "oriented_outward": True,
            }
        )
        record["metal_part_count"] = 20
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "bytes_do_not_match_receipt")
        self.assertIn("the receipt does not vouch for this file", decision.detail)

    def test_a_swapped_receipt_is_refused(self):
        bundle = self._sandbox_bundle()
        receipt = bundle / self.record["receipt_file"]
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["job_id"] = "someone-elses-job"
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        record = dict(self.record, bundle=str(bundle.relative_to(ROOT)))
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertIn("not the one this asset was promoted from", decision.detail)

    def test_skipping_verification_cannot_slip_bytes_past_the_archive(self):
        bundle = self._sandbox_bundle()
        target = bundle / "shank.stl"
        target.write_bytes(target.read_bytes() + b"\x00")
        record = dict(self.record, bundle=str(bundle.relative_to(ROOT)))
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
            verify=False,
        )
        # The listing surface says yes...
        self.assertTrue(decision.deliverable)
        self.assertEqual(decision.to_json()["byte_verification"], "deferred_to_download")
        # ...and the archive still refuses to contain a single unverified byte.
        with self.assertRaises(gd.DeliveryError):
            gd.build_archive(decision, quote=self.report["quote"])

    def test_a_table_that_dropped_a_metal_part_delivers_nothing(self):
        """An incomplete ring delivered silently is worse than a refusal."""
        record = copy.deepcopy(self.record)
        record["files"] = [
            item for item in record["files"] if item["file"] != "shank.stl"
        ]
        record["metal_part_count"] = 18
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "bytes_do_not_match_receipt")
        self.assertIn("shank.stl", decision.detail)
        self.assertIn("absent from the delivery table", decision.detail)

    def test_regenerating_the_table_takes_effect_without_a_restart(self):
        """A cached record must not outlive the file it was read from."""
        path = self._table_with(copy.deepcopy(self.record))
        self.assertIsNotNone(
            gd.record_for_manifest(
                self.report["preview_manifest"], table_path=path
            )
        )
        emptied = copy.deepcopy(gd.load_table())
        emptied["records"] = []
        # Written without clear_cache(), exactly as an operator re-running
        # goal0025_measure_deliverables against a live server would.
        path.write_text(json.dumps(emptied, indent=2, sort_keys=True), encoding="utf-8")
        self.assertIsNone(
            gd.record_for_manifest(
                self.report["preview_manifest"], table_path=path
            )
        )

    def test_a_bundle_outside_the_repository_is_refused_by_name(self):
        record = dict(self.record, bundle="../../etc")
        decision = gd.decide(
            self.report["preview_manifest"],
            quote=self.report["quote"],
            table_path=self._table_with(record),
        )
        self.assertFalse(decision.deliverable)
        self.assertEqual(decision.reason, "bundle_path_invalid")
        # A corrupted table must not leak the asset's file list either.
        self.assertIsNone(decision.asset)
        self.assertNotIn("files", decision.to_json())


class CastableSolidTests(ProfileTestCase):
    """Rhino's own union, measured from the real unpromoted V6 bundle.

    V6 fixes F1 and F2 at the source and is what Goal 0019 would promote. Nothing
    here promotes it — these measure the bytes on disk so that when promotion
    happens, the union is actually carried instead of being silently dropped.
    """

    BUNDLE = (
        "output/goal_0018_castable_master_20260812/azure_v6/received/"
        "goal0018-castable-master-control"
    )

    def setUp(self) -> None:
        super().setUp()
        if not (ROOT / self.BUNDLE).is_dir():
            self.skipTest("the V6 export bundle is not on this machine")
        from scripts import goal0025_measure_deliverables as builder

        self.record = builder._record(
            {
                "asset_id": "v6-not-promoted",
                "asset_version": "v6.0.0",
                "match": {"geometry_signature": "sha256:" + "a" * 64},
                "provenance": {
                    "source_receipt_sha256": "0" * 64,
                    "definition_path": "manual/rhino_compute_bridge/v6.ghx",
                    "definition_sha256": "0" * 64,
                    "definition_kind": "parametric_master",
                    "template_id": "goal0018-castable-master-v6",
                },
            },
            ROOT / self.BUNDLE,
        )

    def test_the_union_rhino_emits_is_carried_not_dropped(self):
        self.assertTrue(self.record["ships_castable_solid"])
        self.assertEqual(self.record["castable_solid_file"], "castable_solid.stl")
        self.assertEqual(
            len([f for f in self.record["files"] if f["role"] == "castable"]), 1
        )

    def test_the_union_is_excluded_from_the_metal_count(self):
        """Counting the union as a metal part would count the whole ring twice."""
        self.assertEqual(self.record["metal_part_count"], 19)
        self.assertEqual(len(self.record["files"]), 21)

    def test_v6_reports_f1_and_f2_as_fixed(self):
        self.assertEqual(self.record["inverted_part_count"], 0)
        codes = {finding["code"] for finding in self.record["findings"]}
        self.assertEqual(codes, {"PARTS_OVERLAP_BY_DESIGN"})
        self.assertNotIn("PARTS_NOT_UNIONED", codes)

    def test_a_shop_requiring_one_solid_would_be_satisfied_by_v6(self):
        """The claim `docs/geometry_delivery_2026-08-17.md` §5 makes, asserted."""
        import dataclasses

        table = copy.deepcopy(gd.load_table())
        table["records"] = [self.record]
        gd.validate_table(table)
        asset = dataclasses.replace(
            gd._loaded(gd.DEFAULT_TABLE_PATH)[1][
                next(
                    item["geometry_signature"]
                    for item in gd.load_table()["records"]
                    if item["asset_id"] == CONTROL
                )
            ],
            ships_castable_solid=True,
            castable_solid_file="castable_solid.stl",
            inverted_part_count=0,
            inverted_parts=(),
        )
        policy = _profile(
            dict(PERMISSIVE_MESH, single_solid_required=True, outward_normals_required=True)
        ).mesh_policy()
        self.assertIsNone(gd._policy_refusals(asset, policy))

    def test_an_invalid_union_is_not_a_union(self):
        table = copy.deepcopy(gd.load_table())
        record = copy.deepcopy(self.record)
        for item in record["files"]:
            if item["role"] == "castable":
                item["oriented_outward"] = False
        table["records"] = [record]
        with self.assertRaises(gd.DeliveryError) as caught:
            gd.validate_table(table)
        self.assertIn("not a union", str(caught.exception))


class ProceduralDraftTests(ProfileTestCase):
    """The trimesh draft is an operational aid and can never be a deliverable."""

    def test_the_draft_endpoint_labels_itself_as_not_deliverable(self):
        from fast_mvp.server import _asset_urls

        payload = _asset_urls({"job_id": "j", "draft_stl_path": "x"})
        self.assertFalse(payload["deliverable"])
        self.assertEqual(
            payload["authority"], "procedural_operational_aid_not_manufacturable"
        )
        self.assertIn("/api/deliver-geometry", payload["delivery_endpoint"])

    def test_no_promoted_bundle_lives_where_drafts_are_written(self):
        runs = (ROOT / "fast_mvp" / "runs").resolve()
        for record in gd.load_table()["records"]:
            bundle = (ROOT / record["bundle"]).resolve()
            self.assertNotEqual(bundle, runs)
            self.assertNotIn(runs, bundle.parents)

    def test_the_delivery_module_never_writes_geometry(self):
        """A structural check: nothing here can create a mesh to hand over.

        Delivery reads bytes and refuses. It imports no mesh library at all, so
        "build the union and ship that" cannot be reached from here without an
        import somebody would have to add on purpose.
        """
        source = (ROOT / "fast_mvp" / "geometry_delivery.py").read_text(
            encoding="utf-8"
        )
        imports = [
            line.strip()
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        for forbidden in ("trimesh", "manifold3d", "fast_mvp.mesh", "numpy"):
            self.assertFalse(
                [line for line in imports if forbidden in line],
                f"{forbidden} must not be importable from the delivery path",
            )


class WorkflowIntegrationTests(ProfileTestCase):
    """The server boundary re-derives everything; a caller nominates nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = pathlib.Path(self.tmp.name)
        from fast_mvp import workflow

        self.workflow = workflow
        report = _configure(PROMOTED[CONTROL])
        self.job_id = workflow.persist_proposal(report, self.runs)["job_id"]

    def test_a_persisted_run_refuses_in_the_shipped_state(self):
        payload = self.workflow.deliver_geometry(self.job_id, self.runs)
        self.assertFalse(payload["deliverable"])
        self.assertEqual(payload["reason"], "mesh_policy_undeclared")
        self.assertEqual(payload["job_id"], self.job_id)
        self.assertNotIn("archive_url", payload)

    @unittest.skipUnless((ROOT / "output/goal_0014_parametric_master_20260804/azure_v5/received").is_dir(), 'Requires private generated Rhino export evidence, not distributed in the public snapshot')
    def test_a_declared_shop_gets_the_archive_through_the_workflow(self):
        sp.set_active_profile(_profile())
        payload = self.workflow.deliver_geometry(self.job_id, self.runs)
        self.assertTrue(payload["deliverable"])
        self.assertEqual(payload["archive_url"], f"/api/deliverable/{self.job_id}.zip")
        name, blob, digest = self.workflow.deliver_geometry_archive(
            self.job_id, self.runs
        )
        self.assertTrue(name.endswith(".zip"))
        self.assertEqual(hashlib.sha256(blob).hexdigest(), digest)

    def test_the_archive_route_refuses_rather_than_serving_something_else(self):
        with self.assertRaises(self.workflow.GeometryDeliveryRefused) as caught:
            self.workflow.deliver_geometry_archive(self.job_id, self.runs)
        self.assertEqual(caught.exception.decision.reason, "mesh_policy_undeclared")

    def test_a_tampered_persisted_manifest_cannot_promote_itself(self):
        """The caller owns the run directory in this test; the join still refuses."""
        sp.set_active_profile(_profile())
        manifest_path = self.runs / self.job_id / "preview_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["selected_asset"]["asset_id"] = "goal0014-v5-us9_round8"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        payload = self.workflow.deliver_geometry(self.job_id, self.runs)
        self.assertFalse(payload["deliverable"])
        self.assertEqual(payload["reason"], "no_deliverable_record")

    def test_unknown_job_is_not_found(self):
        with self.assertRaises(self.workflow.RunNotFoundError):
            self.workflow.deliver_geometry("no-such-job", self.runs)

    def test_review_and_the_endpoint_never_disagree(self):
        """Both re-derive the quote, so a run persisted before the volume table
        moved forward reports the same answer in the listing and at the endpoint."""
        stale = json.loads(
            (self.runs / self.job_id / "proposal_report.json").read_text(
                encoding="utf-8"
            )
        )
        stale["quote"] = {
            **stale["quote"],
            "status": "pending_geometry",
            "metal_weight_by_alloy": None,
            "metal_weight_g": None,
        }
        (self.runs / self.job_id / "proposal_report.json").write_text(
            json.dumps(stale), encoding="utf-8"
        )
        for profile in (None, _profile()):
            with self.subTest(profile="declared" if profile else "none"):
                sp.set_active_profile(profile)
                listed = next(
                    run
                    for run in self.workflow.review_index(self.runs)["runs"]
                    if run["job_id"] == self.job_id
                )["geometry_delivery"]
                endpoint = self.workflow.deliver_geometry(self.job_id, self.runs)
                self.assertEqual(listed["reason"], endpoint["reason"])
                self.assertEqual(listed["deliverable"], endpoint["deliverable"])
                self.assertNotEqual(listed["reason"], "no_metal_weight_table")

    def test_review_reports_the_delivery_state_for_every_run(self):
        index = self.workflow.review_index(self.runs)
        entry = next(run for run in index["runs"] if run["job_id"] == self.job_id)
        delivery = entry["geometry_delivery"]
        self.assertFalse(delivery["deliverable"])
        self.assertEqual(delivery["reason"], "mesh_policy_undeclared")
        self.assertEqual(delivery["byte_verification"], "deferred_to_download")
        self.assertFalse(delivery["certifies_manufacturing"])


class LayeringTests(unittest.TestCase):
    """Delivery is an app concern. The foundation package must not depend on it."""

    def test_the_foundation_package_never_imports_the_delivery_module(self):
        """Prose may reference the decision doc; code may not reach into the app.

        The same layering that keeps a price away from geometry (settled decision
        6) keeps a shop's delivery policy away from it: ``shop_profile`` answers
        what a shop accepts, and only the app decides what that means for a file.
        """
        for path in (ROOT / "jewelry_gaas_foundation").glob("*.py"):
            imports = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith(("import ", "from "))
            ]
            self.assertFalse(
                [line for line in imports if "fast_mvp" in line], path.name
            )


if __name__ == "__main__":
    unittest.main()
