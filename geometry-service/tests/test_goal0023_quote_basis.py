"""Goal 0023 — the quote's volume is measured, exact, and never substituted.

Three things must hold and are each adversarially tested here:

1. The basis is the measured mesh **union**. The naive parts sum over-counts by
   ~6.75%, outside the agreed +/-3% volume tolerance and in the customer's
   disfavour, so it must be structurally unreachable as a basis.
2. A quote appears for the promoted configurations and **only** those. Every other
   configuration stays ``pending_geometry`` with no volume borrowed from a
   neighbour and none accepted from the request.
3. Quoting certifies nothing. A price is a commercial estimate from measured
   geometry; no geometry gate moves.
"""

from __future__ import annotations

import copy
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fast_mvp import manual_configurator as mc
from fast_mvp import quote_volumes, typed_inputs
from fast_mvp.preview_resolution import load_registry
from jewelry_gaas_foundation.geometry_metrics import METRICS_VERSION
from jewelry_gaas_foundation.manufacturing_assumptions import CONVERGENCE_TOLERANCES

ROOT = pathlib.Path(__file__).resolve().parents[1]

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


def _configure(overrides: dict) -> dict:
    payload = {
        "stone_shape": "round",
        "metal_color_family": "yellow",
        "metal_purity": "18k",
        **overrides,
    }
    return mc.configure(payload)


class VolumeTableTests(unittest.TestCase):
    """The table itself, before anything consumes it."""

    @classmethod
    def setUpClass(cls):
        cls.table = quote_volumes.load_table()

    def test_table_exists_and_validates(self):
        self.assertFalse(self.table.get("missing"))
        self.assertEqual(self.table["schema"], quote_volumes.TABLE_SCHEMA)
        self.assertEqual(self.table["metrics_version"], METRICS_VERSION)
        quote_volumes.validate_table(self.table)

    def test_basis_is_the_measured_union_and_nothing_else(self):
        self.assertEqual(
            self.table["quote_volume_basis"], quote_volumes.QUOTE_VOLUME_BASIS
        )
        self.assertEqual(quote_volumes.QUOTE_VOLUME_BASIS, "measured_mesh_union")
        self.assertEqual(
            set(quote_volumes._BASIS_FIELDS.values()),
            {"union_mm3"},
            "parts_sum_mm3 must not be reachable as a quoting basis",
        )

    def test_a_table_naming_the_parts_sum_as_basis_is_refused(self):
        bad = copy.deepcopy(self.table)
        bad["quote_volume_basis"] = "parts_sum"
        with self.assertRaises(quote_volumes.QuoteVolumeError):
            quote_volumes.validate_table(bad)

    def test_a_union_that_removed_nothing_is_refused(self):
        """A boolean that silently no-ops leaves the over-counted number behind."""
        bad = copy.deepcopy(self.table)
        bad["records"][0]["union_mm3"] = bad["records"][0]["parts_sum_mm3"]
        with self.assertRaises(quote_volumes.QuoteVolumeError):
            quote_volumes.validate_table(bad)

    def test_duplicate_signatures_are_refused(self):
        bad = copy.deepcopy(self.table)
        bad["records"].append(copy.deepcopy(bad["records"][0]))
        with self.assertRaises(quote_volumes.QuoteVolumeError):
            quote_volumes.validate_table(bad)

    def test_every_record_covers_exactly_the_promoted_exact_assets(self):
        registry = load_registry()
        promoted = {
            asset["asset_id"]
            for asset in registry["assets"]
            if asset["enabled"]
            and asset["approval"]["status"] == "approved"
            and asset["approval"]["visual_review"] == "approved"
            and asset["classification"] == "exact"
        }
        recorded = {record["asset_id"] for record in self.table["records"]}
        self.assertEqual(recorded, promoted)
        self.assertEqual(recorded, set(PROMOTED))

    def test_every_record_matches_its_registry_signature_and_receipt(self):
        registry = {a["asset_id"]: a for a in load_registry()["assets"]}
        for record in self.table["records"]:
            with self.subTest(asset=record["asset_id"]):
                asset = registry[record["asset_id"]]
                self.assertEqual(
                    record["geometry_signature"],
                    asset["match"]["geometry_signature"],
                )
                self.assertEqual(
                    record["source_receipt_sha256"],
                    asset["provenance"]["source_receipt_sha256"],
                )
                self.assertEqual(
                    record["definition_sha256"],
                    asset["provenance"]["definition_sha256"],
                )

    def test_every_record_measured_the_bytes_the_receipt_vouches_for(self):
        for record in self.table["records"]:
            with self.subTest(asset=record["asset_id"]):
                self.assertEqual(
                    record["part_bytes_verified"], record["metal_part_count"]
                )
                self.assertTrue(record["union_watertight"])
                self.assertEqual(record["union_body_count"], 1)

    def test_the_naive_sum_would_be_out_of_the_agreed_volume_tolerance(self):
        """The reason the basis choice matters, pinned to the agreed tolerance."""
        tolerance = next(
            t for t in CONVERGENCE_TOLERANCES if t.key == "metal_volume_mm3"
        )
        self.assertEqual(tolerance.unit, "fraction")
        self.assertTrue(tolerance.agreed)
        for record in self.table["records"]:
            with self.subTest(asset=record["asset_id"]):
                self.assertGreater(
                    record["overcount_fraction"],
                    tolerance.tolerance,
                    "if the parts sum were inside tolerance this choice would be "
                    "cosmetic; it is not",
                )

    def test_rhino_cross_check_agrees_where_a_nurbs_union_exists(self):
        checked = [
            r for r in self.table["records"] if r["rhino_vs_mesh_fraction"] is not None
        ]
        for record in checked:
            with self.subTest(asset=record["asset_id"]):
                self.assertLess(abs(record["rhino_vs_mesh_fraction"]), 0.01)
        # V4/V5 carry no NURBS union at all -- that absence is finding F2, and is
        # exactly why the mesh union is the declared basis.
        self.assertEqual(checked, [])


class QuoteResolutionTests(unittest.TestCase):
    """The join from a resolved preview to a volume, and its refusals."""

    def test_each_promoted_configuration_quotes_its_own_measured_volume(self):
        table = {r["asset_id"]: r for r in quote_volumes.load_table()["records"]}
        for asset_id, overrides in PROMOTED.items():
            with self.subTest(asset=asset_id):
                report = _configure(overrides)
                quote = report["quote"]
                self.assertEqual(report["preview_manifest"]["preview_status"], "exact")
                self.assertEqual(quote["status"], "indicative")
                self.assertEqual(quote["volume_basis"], "measured_mesh_union")
                self.assertAlmostEqual(
                    quote["ring_volume_mm3"], table[asset_id]["union_mm3"], places=3
                )
                self.assertEqual(quote["volume_evidence"]["asset_id"], asset_id)

    def test_the_quoted_volume_is_never_the_naive_parts_sum(self):
        for asset_id, overrides in PROMOTED.items():
            with self.subTest(asset=asset_id):
                quote = _configure(overrides)["quote"]
                evidence = quote["volume_evidence"]
                self.assertAlmostEqual(
                    quote["ring_volume_mm3"], evidence["union_mm3"], places=3
                )
                self.assertNotAlmostEqual(
                    quote["ring_volume_mm3"], evidence["parts_sum_mm3"], places=1
                )
                self.assertLess(evidence["union_mm3"], evidence["parts_sum_mm3"])
                self.assertEqual(
                    evidence["parts_sum_role"], "evidence_only_never_the_basis"
                )

    def test_weight_is_volume_times_the_typed_alloy_density(self):
        overrides = PROMOTED["goal0014-v5-us6p5_round6p5"]
        for family, purity, density in (
            ("yellow", "14k", 13.1),
            ("yellow", "18k", 15.5),
            ("white", "pt950", 20.7),
        ):
            with self.subTest(alloy=f"{purity}_{family}"):
                report = _configure(
                    {
                        **overrides,
                        "metal_color_family": family,
                        "metal_purity": purity,
                    }
                )
                quote = report["quote"]
                self.assertAlmostEqual(
                    quote["metal_weight_g"],
                    round(quote["ring_volume_mm3"] * density / 1000.0, 3),
                    places=3,
                )
                # Metal never moved the geometry: same volume for every alloy.
                self.assertAlmostEqual(quote["ring_volume_mm3"], 162.234, places=3)

    def test_an_unsolved_in_envelope_size_is_never_quoted(self):
        """The failure class Goal 0014 closed, applied to price.

        US 5 sits between two solved sizes and inside the master's envelope. It
        must not be quoted from either neighbour.
        """
        report = _configure(
            {
                "ring_size_us": 5,
                "stone_length_mm": 5.5,
                "stone_width_mm": 5.5,
                "stone_depth_mm": 3.3,
                "head_style_id": "head_v5_parametric_petal_4_prong",
                "band_style_id": "band_v5_parametric_split_solitaire",
            }
        )
        self.assertEqual(report["preview_manifest"]["preview_status"], "unavailable")
        quote = report["quote"]
        self.assertEqual(quote["status"], "pending_geometry")
        self.assertIsNone(quote["ring_volume_mm3"])
        self.assertIsNone(quote["metal_weight_g"])
        self.assertIsNone(quote["volume_evidence"])

    def test_a_configuration_with_no_receipt_backed_master_is_never_quoted(self):
        report = _configure(
            {
                "ring_size_us": 6.5,
                "stone_length_mm": 6.5,
                "stone_width_mm": 6.5,
                "stone_depth_mm": 3.9,
                "head_style_id": "head_classic_4_prong",
                "band_style_id": "band_classic_solitaire",
            }
        )
        self.assertEqual(report["quote"]["status"], "pending_geometry")
        self.assertIsNone(report["quote"]["ring_volume_mm3"])

    def test_an_estimated_preview_would_not_be_quoted(self):
        """Approximating a picture is not licence to approximate a price."""
        record = quote_volumes.load_table()["records"][0]
        manifest = {
            "preview_status": "estimated",
            "geometry": {"signature": record["geometry_signature"]},
            "selected_asset": {
                "asset_id": record["asset_id"],
                "asset_version": record["asset_version"],
            },
        }
        self.assertIsNone(quote_volumes.volume_for_manifest(manifest))

    def test_a_table_that_drifted_from_the_registry_quotes_nothing(self):
        record = quote_volumes.load_table()["records"][0]
        manifest = {
            "preview_status": "exact",
            "geometry": {"signature": record["geometry_signature"]},
            "selected_asset": {
                "asset_id": "some-other-asset",
                "asset_version": record["asset_version"],
            },
        }
        self.assertIsNone(quote_volumes.volume_for_manifest(manifest))

        stale_version = {
            "preview_status": "exact",
            "geometry": {"signature": record["geometry_signature"]},
            "selected_asset": {
                "asset_id": record["asset_id"],
                "asset_version": "v999.0.0",
            },
        }
        self.assertIsNone(quote_volumes.volume_for_manifest(stale_version))

    def test_an_unknown_signature_quotes_nothing(self):
        manifest = {
            "preview_status": "exact",
            "geometry": {"signature": "sha256:" + "0" * 64},
            "selected_asset": {"asset_id": "x", "asset_version": "1"},
        }
        self.assertIsNone(quote_volumes.volume_for_manifest(manifest))

    def test_a_missing_table_quotes_nothing_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = pathlib.Path(tmp) / "not_there.json"
            table = quote_volumes.load_table(absent)
            self.assertTrue(table["missing"])
            self.assertEqual(table["records"], [])
            manifest = {
                "preview_status": "exact",
                "geometry": {"signature": "sha256:" + "1" * 64},
                "selected_asset": {"asset_id": "x", "asset_version": "1"},
            }
            self.assertIsNone(
                quote_volumes.volume_for_manifest(manifest, table_path=absent)
            )


class QuoteContractTests(unittest.TestCase):
    """What a quote may and may not say."""

    def test_a_quote_never_certifies_manufacturing(self):
        for asset_id, overrides in PROMOTED.items():
            with self.subTest(asset=asset_id):
                report = _configure(overrides)
                quote = report["quote"]
                self.assertEqual(
                    quote["manufacturing_state"], "pending_not_run_needs_calibration"
                )
                self.assertIn("not a manufacturing certification", quote["non_certifying_note"])
                self.assertEqual(
                    quote["volume_evidence"]["authority"],
                    "measurement_only_not_manufacturing_certification",
                )
                self.assertIs(
                    report["preview_manifest"]["manufacturing_authority"], False
                )
                self.assertEqual(
                    report["preview_manifest"]["manufacturing_state"],
                    "pending_not_run_needs_calibration",
                )

    def test_a_priced_quote_still_excludes_everything_but_metal(self):
        report = _configure(PROMOTED["goal0014-v5-us6p5_round6p5"])
        for excluded in ("stone", "labor", "finishing", "setting", "margin"):
            self.assertIn(excluded, report["quote"]["excludes"])

    def test_no_spot_price_means_no_cost_and_says_so(self):
        quote = _configure(PROMOTED["goal0014-v5-us6p5_round6p5"])["quote"]
        self.assertIsNone(quote["metal_content_cost"])
        self.assertIsNone(quote["metal_price_per_gram"])
        self.assertIn("No price was invented", quote["reason"])

    def test_a_supplied_spot_price_prices_the_measured_weight(self):
        spec = typed_inputs.parse_metal_spec({"metal_alloy": "18k_yellow"})
        record = next(
            r
            for r in quote_volumes.load_table()["records"]
            if r["asset_id"] == "goal0014-v5-us6p5_round6p5"
        )
        volume = quote_volumes._records(str(quote_volumes.DEFAULT_TABLE_PATH))[
            record["geometry_signature"]
        ]
        quote = typed_inputs.quote_basis(
            spec, measured_volume=volume.evidence(), metal_price_per_gram=80.0
        )
        self.assertEqual(quote["status"], "indicative")
        self.assertAlmostEqual(quote["metal_weight_g"], 2.515, places=3)
        # Cost comes from the unrounded weight: 162.234 mm³ × 15.5 / 1000 × 80.
        self.assertAlmostEqual(quote["metal_content_cost"], 201.17, places=2)

    def test_a_bare_caller_volume_is_labelled_as_carrying_no_provenance(self):
        spec = typed_inputs.parse_metal_spec({"metal_alloy": "pt950"})
        quote = typed_inputs.quote_basis(spec, ring_volume_mm3=100.0)
        self.assertEqual(quote["volume_basis"], "caller_supplied_volume_no_provenance")
        self.assertIsNone(quote["volume_evidence"])

    def test_typed_metal_round_trips_so_the_server_can_re_derive_a_quote(self):
        for alloy in ("14k_rose", "18k_white", "pt950"):
            with self.subTest(alloy=alloy):
                spec = typed_inputs.parse_metal_spec({"metal_alloy": alloy})
                self.assertEqual(typed_inputs.MetalSpec.from_json(spec.to_json()), spec)

    def test_a_non_typed_metal_payload_is_refused(self):
        with self.assertRaises(typed_inputs.TypedInputError):
            typed_inputs.MetalSpec.from_json({"color_family": "yellow"})


class PersistenceBoundaryTests(unittest.TestCase):
    """The server re-derives the quote it is about to store."""

    def test_a_caller_supplied_quote_is_discarded_at_persistence(self):
        from fast_mvp.workflow import persist_proposal

        report = _configure(PROMOTED["goal0014-v5-us6p5_round6p5"])
        truthful = copy.deepcopy(report["quote"])

        forged = copy.deepcopy(report)
        forged["quote"] = {
            **truthful,
            "ring_volume_mm3": 999.0,
            "metal_weight_g": 99.9,
            "metal_content_cost": 9999.0,
            "volume_basis": "wishful_thinking",
            "metal_price_per_gram": 55.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            persist_proposal(forged, pathlib.Path(tmp))
        self.assertEqual(forged["quote"]["ring_volume_mm3"], truthful["ring_volume_mm3"])
        self.assertEqual(forged["quote"]["volume_basis"], "measured_mesh_union")
        # The spot price is the caller's to give, so it survives and is applied.
        self.assertEqual(forged["quote"]["metal_price_per_gram"], 55.0)
        density = truthful["metal"]["density_g_cm3"]
        self.assertAlmostEqual(
            forged["quote"]["metal_content_cost"],
            round(truthful["ring_volume_mm3"] * density / 1000.0 * 55.0, 2),
            places=2,
        )

    def test_a_quote_whose_metal_will_not_parse_is_emptied_not_trusted(self):
        """It cannot be re-derived, so it cannot be vouched for."""
        report = _configure(PROMOTED["goal0014-v5-us6p5_round6p5"])
        report["quote"]["metal"] = {"color_family": "yellow"}
        report["quote"]["ring_volume_mm3"] = 999.0
        report["quote"]["metal_weight_g"] = 99.9
        quote_volumes.reconcile_quote(report)
        self.assertEqual(report["quote"]["status"], "pending_geometry")
        self.assertIsNone(report["quote"]["ring_volume_mm3"])
        self.assertIsNone(report["quote"]["metal_weight_g"])
        self.assertIsNone(report["quote"]["volume_evidence"])
        self.assertIn("could not be re-derived", report["quote"]["reason"])

    def test_a_report_with_no_quote_at_all_is_left_alone(self):
        report = {"preview_manifest": {}, "status": "accepted"}
        quote_volumes.reconcile_quote(report)
        self.assertNotIn("quote", report)

    def test_persistence_cannot_invent_a_quote_for_an_unsolved_ring(self):
        from fast_mvp.workflow import persist_proposal

        report = _configure(
            {
                "ring_size_us": 5,
                "stone_length_mm": 5.5,
                "stone_width_mm": 5.5,
                "stone_depth_mm": 3.3,
                "head_style_id": "head_v5_parametric_petal_4_prong",
                "band_style_id": "band_v5_parametric_split_solitaire",
            }
        )
        report["quote"]["ring_volume_mm3"] = 160.0
        report["quote"]["metal_weight_g"] = 2.5
        with tempfile.TemporaryDirectory() as tmp:
            persist_proposal(report, pathlib.Path(tmp))
        self.assertEqual(report["quote"]["status"], "pending_geometry")
        self.assertIsNone(report["quote"]["ring_volume_mm3"])


class RegenerationTests(unittest.TestCase):
    """The committed table must still be what measuring the exports produces."""

    def test_the_committed_table_matches_a_fresh_measurement(self):
        from scripts.goal0023_measure_quote_volumes import build_table

        committed = quote_volumes.load_table()
        missing = [
            record["bundle"]
            for record in committed["records"]
            if not (ROOT / record["bundle"]).is_dir()
        ]
        if missing:
            self.skipTest(f"export bundles not present: {missing}")

        fresh, skipped = build_table(measured_on=committed["measured_on"])
        self.assertEqual(skipped, [])
        self.assertEqual(len(fresh["records"]), len(committed["records"]))
        for new, old in zip(fresh["records"], committed["records"]):
            with self.subTest(asset=old["asset_id"]):
                self.assertEqual(new["asset_id"], old["asset_id"])
                # A boolean-engine update may move the last digits; anything
                # larger than 0.05 mm³ (~0.03%) is real drift, not rounding.
                self.assertAlmostEqual(
                    new["union_mm3"], old["union_mm3"], delta=0.05
                )
                self.assertAlmostEqual(
                    new["parts_sum_mm3"], old["parts_sum_mm3"], delta=0.05
                )


if __name__ == "__main__":
    unittest.main()
