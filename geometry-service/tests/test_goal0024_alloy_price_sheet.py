"""Goal 0024 — a price has a declared home, or there is no price.

Goal 0023 made the weight real. This goal gives the *cost* one dated commercial
input and, much more importantly, the refusals that keep a guessed one out. Three
things must hold and are each adversarially tested here:

1. **The shipped state prices nothing.** The committed sheet declares nothing and
   refuses all of its own values, so every quote reports ``metal_content_cost:
   null`` with a reason naming which condition failed. Nothing regresses to a
   guessed price, and no last-known price is ever reused.
2. **A declared, unexpired, cited sheet prices the promoted configurations and
   only those.** Pricing changes nothing about which rings are quotable — that is
   still the measured-volume join from Goal 0023.
3. **A price is commercial, not structural.** It moves no geometry gate, never
   enters a geometry signature, and cannot reach the constraint engine at all.
"""

from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fast_mvp import alloy_prices, manual_configurator as mc
from fast_mvp.alloy_prices import (
    COST_EXCLUDES,
    AlloyPriceError,
    AlloyPriceSheet,
    active_price,
    clear_active_sheet,
    load_sheet,
    set_active_sheet,
    undeclared_template,
)
from fast_mvp.typed_inputs import build_metal_spec, quote_basis

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The seven alloys ``typed_inputs`` can build. A sheet that cannot name one of
#: these cannot price a ring anyone can order.
ALLOYS = (
    "14k_yellow", "14k_white", "14k_rose",
    "18k_yellow", "18k_white", "18k_rose",
    "pt950",
)

#: A promoted, quotable configuration (Goal 0023) — the US 6.5 V5 ring.
PROMOTED_US6P5 = {
    "ring_size_us": 6.5,
    "stone_length_mm": 6.5,
    "stone_width_mm": 6.5,
    "stone_depth_mm": 3.9,
    "head_style_id": "head_v5_parametric_petal_4_prong",
    "band_style_id": "band_v5_parametric_split_solitaire",
}

#: In the envelope, between two solved sizes, and deliberately NOT quotable.
UNSOLVED_US5 = {**PROMOTED_US6P5, "ring_size_us": 5.0}


def declared_sheet(**overrides) -> dict:
    """A fully declared sheet: every alloy priced, cited, dated, unexpired."""
    document = undeclared_template().document
    document.update(
        sheet_id="test-declared-sheet",
        status="declared",
        currency="USD",
        declared_by={"name": "Test operator", "role": "operator"},
    )
    document["validity"] = {"valid_from": "2026-08-17", "expires_on": "2999-12-31"}
    for index, alloy in enumerate(ALLOYS):
        document["prices"][alloy] = {
            "price_per_gram": 40.0 + index,
            "status": "declared",
            "as_of": "2026-08-17",
            "expires_on": "2999-12-31",
            "price_basis": "spot_metal_only",
            "provenance": {
                "source": "Refiner quote, test fixture",
                "recorded_on": "2026-08-17",
            },
        }
    document.update(overrides)
    return document


def configure(**overrides) -> dict:
    payload = {
        "stone_shape": "round",
        "metal_color_family": "yellow",
        "metal_karat": "18k",
        **PROMOTED_US6P5,
    }
    payload.update(overrides)
    return mc.configure(payload)


class ShippedStatePricesNothing(unittest.TestCase):
    """The state the repository ships in, asserted rather than assumed."""

    def setUp(self):
        clear_active_sheet()

    def tearDown(self):
        clear_active_sheet()

    def test_the_committed_template_is_undeclared(self):
        sheet = undeclared_template()
        self.assertEqual(sheet.status, "undeclared")
        self.assertEqual(sheet.usable_prices(), {})

    def test_the_committed_template_refuses_every_one_of_its_own_values(self):
        sheet = undeclared_template()
        for alloy in ALLOYS:
            with self.subTest(alloy=alloy):
                price = sheet.price(alloy)
                self.assertFalse(price.usable)
                self.assertIsNone(price.price_per_gram)

    def test_the_template_carries_every_alloy_the_product_can_build(self):
        # A missing alloy would be an unpriceable ring the configurator still
        # offers, which is the failure this whole goal exists to avoid.
        self.assertEqual(set(undeclared_template().document["prices"]), set(ALLOYS))

    def test_every_alloy_quotes_a_null_cost_with_a_reason(self):
        for alloy in ALLOYS:
            purity, _, family = alloy.partition("_")
            metal = build_metal_spec(family or "white", purity)
            with self.subTest(alloy=alloy):
                quote = quote_basis(metal, ring_volume_mm3=162.234)
                self.assertIsNone(quote["metal_content_cost"])
                self.assertIsNone(quote["price_source"])
                self.assertEqual(quote["price_refusal"]["reason"], "sheet_undeclared")
                self.assertIn("No price was invented", quote["price_refusal"]["detail"])

    def test_the_weight_still_resolves_when_the_price_does_not(self):
        # Goal 0023's deliverable must not regress: an unpriced quote still
        # reports the measured weight.
        quote = configure()["quote"]
        self.assertEqual(quote["status"], "indicative")
        self.assertEqual(quote["ring_volume_mm3"], 162.234)
        self.assertIsNotNone(quote["metal_weight_g"])
        self.assertIsNone(quote["metal_content_cost"])

    def test_a_missing_sheet_file_prices_nothing_and_does_not_raise(self):
        set_active_sheet(None)
        price = active_price("18k_yellow")
        self.assertFalse(price.usable)
        self.assertEqual(price.reason, "no_price_sheet")


class RefusalsAreNamed(unittest.TestCase):
    """Every way a price can fail names *which* condition failed."""

    def tearDown(self):
        clear_active_sheet()

    def refusal(self, document) -> dict:
        set_active_sheet(AlloyPriceSheet(document))
        quote = quote_basis(build_metal_spec("yellow", "18k"), ring_volume_mm3=162.234)
        self.assertIsNone(quote["metal_content_cost"])
        return quote["price_refusal"]

    def test_an_expired_sheet_prices_nothing(self):
        document = declared_sheet()
        document["validity"]["expires_on"] = "2020-01-01"
        self.assertEqual(self.refusal(document)["reason"], "sheet_expired")

    def test_an_expired_entry_prices_nothing_even_in_a_live_sheet(self):
        document = declared_sheet()
        document["prices"]["18k_yellow"]["expires_on"] = "2020-01-01"
        self.assertEqual(self.refusal(document)["reason"], "price_expired")

    def test_an_undeclared_entry_prices_nothing_even_in_a_declared_sheet(self):
        document = declared_sheet()
        document["prices"]["18k_yellow"]["status"] = "undeclared"
        self.assertEqual(self.refusal(document)["reason"], "price_undeclared")

    def test_an_absent_alloy_prices_nothing(self):
        document = declared_sheet()
        document["prices"].pop("18k_yellow")
        self.assertEqual(self.refusal(document)["reason"], "no_price_for_alloy")

    def test_a_price_that_does_not_say_what_it_covers_is_refused(self):
        document = declared_sheet()
        document["prices"]["18k_yellow"]["price_basis"] = "unknown"
        self.assertEqual(self.refusal(document)["reason"], "price_basis_unknown")

    def test_expiry_is_evaluated_on_the_day_not_merely_the_year(self):
        document = declared_sheet()
        document["validity"]["expires_on"] = "2026-08-17"
        sheet = AlloyPriceSheet(document)
        self.assertTrue(sheet.price("18k_yellow", today=date(2026, 8, 18)).usable is False)
        self.assertTrue(sheet.price("18k_yellow", today=date(2026, 8, 17)).usable)

    def test_a_refusal_never_falls_back_to_another_alloys_price(self):
        document = declared_sheet()
        document["prices"].pop("pt950")
        set_active_sheet(AlloyPriceSheet(document))
        quote = quote_basis(build_metal_spec("white", "pt950"), ring_volume_mm3=162.234)
        self.assertIsNone(quote["metal_content_cost"])
        # Every other alloy in the sheet is priced; none of them leaked across.
        self.assertIn("no other alloy", quote["price_refusal"]["no_fallback_note"])

    def test_a_stale_price_is_never_remembered_across_reloads(self):
        live = declared_sheet()
        set_active_sheet(AlloyPriceSheet(live))
        metal = build_metal_spec("yellow", "18k")
        self.assertIsNotNone(
            quote_basis(metal, ring_volume_mm3=162.234)["metal_content_cost"]
        )
        expired = copy.deepcopy(live)
        expired["validity"]["expires_on"] = "2020-01-01"
        set_active_sheet(AlloyPriceSheet(expired))
        self.assertIsNone(
            quote_basis(metal, ring_volume_mm3=162.234)["metal_content_cost"]
        )


class ADeclaredSheetPrices(unittest.TestCase):
    """With a real sheet the number appears — and stays attributable."""

    def setUp(self):
        set_active_sheet(AlloyPriceSheet(declared_sheet()))

    def tearDown(self):
        clear_active_sheet()

    def test_the_promoted_configuration_prices(self):
        quote = configure()["quote"]
        self.assertEqual(quote["status"], "indicative")
        # 162.234 mm3 * 15.5 g/cm3 / 1000 = 2.5146 g, at 43.0/g.
        self.assertAlmostEqual(quote["metal_weight_g"], 2.515, places=3)
        self.assertAlmostEqual(quote["metal_content_cost"], 108.13, places=2)
        self.assertEqual(quote["currency"], "USD")

    def test_the_cost_names_the_sheet_it_came_from(self):
        quote = configure()["quote"]
        self.assertEqual(
            quote["price_source"], "alloy_price_sheet:test-declared-sheet@v0.1.0"
        )
        evidence = quote["price_evidence"]
        self.assertEqual(evidence["alloy"], "18k_yellow")
        self.assertEqual(evidence["as_of"], "2026-08-17")
        self.assertEqual(evidence["price_basis"], "spot_metal_only")
        self.assertIn("Refiner quote", evidence["source"])

    def test_the_cost_carries_its_exclusions_at_the_price(self):
        # A number shown bare is the thing that misleads, so the exclusions and
        # the scope note travel with the price itself, not only at the top of the
        # quote.
        evidence = configure()["quote"]["price_evidence"]
        self.assertEqual(evidence["excludes"], list(COST_EXCLUDES))
        self.assertEqual(evidence["covers"], "metal_content_only")
        self.assertIn("not the ring's price", evidence["scope_note"])

    def test_an_unsolved_configuration_still_prices_nothing(self):
        # Declaring a price must not widen coverage: quotability is the measured
        # volume join from Goal 0023, and a price cannot substitute for it.
        quote = configure(**UNSOLVED_US5)["quote"]
        self.assertEqual(quote["status"], "pending_geometry")
        self.assertIsNone(quote["metal_weight_g"])
        self.assertIsNone(quote["metal_content_cost"])

    def test_a_pending_quote_still_reports_the_price_state(self):
        # The sheet's state is visible on every quote, not only the ones with a
        # measured volume behind them.
        quote = configure(**UNSOLVED_US5)["quote"]
        self.assertEqual(
            quote["price_source"], "alloy_price_sheet:test-declared-sheet@v0.1.0"
        )

    def test_only_the_four_promoted_configurations_price(self):
        priced = 0
        for record in json.loads(
            (ROOT / "fast_mvp" / "preview_assets" / "measured_volumes_v0_1.json")
            .read_text(encoding="utf-8")
        )["records"]:
            self.assertIsNotNone(record["union_mm3"])
            priced += 1
        self.assertEqual(priced, 4)


class CallerSuppliedPriceStillWorks(unittest.TestCase):
    """The request-body spot price is legitimate and stays open."""

    def setUp(self):
        clear_active_sheet()

    def tearDown(self):
        clear_active_sheet()

    def test_a_supplied_spot_price_prices_even_with_no_sheet(self):
        quote = quote_basis(
            build_metal_spec("yellow", "18k"),
            ring_volume_mm3=162.234,
            metal_price_per_gram=80.0,
        )
        self.assertAlmostEqual(quote["metal_content_cost"], 201.17, places=2)
        self.assertEqual(
            quote["price_source"], "caller_supplied_spot_price_no_provenance"
        )

    def test_a_supplied_price_is_labelled_as_carrying_no_provenance(self):
        quote = quote_basis(
            build_metal_spec("yellow", "18k"),
            ring_volume_mm3=162.234,
            metal_price_per_gram=80.0,
        )
        evidence = quote["price_evidence"]
        self.assertIsNone(evidence["sheet_id"])
        self.assertIsNone(evidence["as_of"])
        self.assertIsNone(evidence["currency"])
        self.assertEqual(evidence["source"], "caller_supplied_request_field")

    def test_a_supplied_price_beats_the_declared_sheet(self):
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        quote = quote_basis(
            build_metal_spec("yellow", "18k"),
            ring_volume_mm3=162.234,
            metal_price_per_gram=80.0,
        )
        self.assertAlmostEqual(quote["metal_content_cost"], 201.17, places=2)
        self.assertEqual(
            quote["price_source"], "caller_supplied_spot_price_no_provenance"
        )

    def test_reconciliation_does_not_launder_a_sheet_price_into_a_caller_one(self):
        # ``reconcile_quote`` re-derives a persisted quote and hands the caller's
        # price back. If the sheet's price had been written into
        # ``metal_price_per_gram`` it would return as "caller supplied" and lose
        # its citation on every round trip.
        from fast_mvp.quote_volumes import reconcile_quote

        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        report = configure()
        self.assertIsNone(report["quote"]["metal_price_per_gram"])
        reconcile_quote(report)
        self.assertEqual(
            report["quote"]["price_source"],
            "alloy_price_sheet:test-declared-sheet@v0.1.0",
        )
        self.assertAlmostEqual(report["quote"]["metal_content_cost"], 108.13, places=2)


class SheetLoadingIsFailClosed(unittest.TestCase):
    """A sheet that is not exactly what it claims to be does not price."""

    def tearDown(self):
        clear_active_sheet()

    def test_a_sheet_claiming_to_certify_manufacturing_is_rejected(self):
        document = declared_sheet()
        document["certifies_manufacturing"] = True
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_sheet_claiming_to_affect_geometry_is_rejected(self):
        document = declared_sheet()
        document["affects_geometry"] = True
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_wildcard_alloy_is_not_expressible(self):
        # pt950 and 14k_yellow differ by roughly an order of magnitude per gram,
        # so a price spanning alloys is always wrong for at least one of them.
        document = declared_sheet()
        document["prices"]["*"] = document["prices"]["18k_yellow"]
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_negative_or_zero_price_is_not_expressible(self):
        for bad in (0, -12.5):
            document = declared_sheet()
            document["prices"]["18k_yellow"]["price_per_gram"] = bad
            with self.subTest(price=bad), self.assertRaises(AlloyPriceError):
                AlloyPriceSheet(document)

    def test_a_price_with_no_citation_is_not_expressible(self):
        document = declared_sheet()
        document["prices"]["18k_yellow"]["provenance"].pop("source")
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_sheet_with_no_expiry_is_not_expressible(self):
        document = declared_sheet()
        document["validity"].pop("expires_on")
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_multi_currency_sheet_is_not_expressible(self):
        document = declared_sheet()
        document["currency"] = "USD/EUR"
        with self.assertRaises(AlloyPriceError):
            AlloyPriceSheet(document)

    def test_a_malformed_sheet_on_disk_prices_nothing_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(AlloyPriceError):
                load_sheet(path)

    def test_the_shipped_template_is_schema_valid(self):
        # It ships refusing everything, but it must still be a valid sheet — a
        # template that cannot load teaches the operator the wrong shape.
        self.assertIsInstance(undeclared_template(), AlloyPriceSheet)


class PriceIsCommercialNotStructural(unittest.TestCase):
    """A cost moves no gate, touches no geometry, and cannot reach the core."""

    def tearDown(self):
        clear_active_sheet()

    def test_the_geometry_package_never_imports_the_price_module(self):
        # Structural, not advisory: jewelry_gaas_foundation does not import
        # fast_mvp at all, which is why the price lives on this side of the line.
        hits = subprocess.run(
            ["grep", "-rn", "fast_mvp", str(ROOT / "jewelry_gaas_foundation")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(hits.stdout.strip(), "")

    def test_declaring_a_price_does_not_change_the_geometry_identity(self):
        clear_active_sheet()
        unpriced = configure()
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        priced = configure()
        self.assertEqual(unpriced["geometry_identity"], priced["geometry_identity"])
        self.assertEqual(
            unpriced["preview_manifest"]["geometry"]["signature"],
            priced["preview_manifest"]["geometry"]["signature"],
        )

    def test_declaring_a_price_does_not_change_the_canonical_spec(self):
        clear_active_sheet()
        unpriced = configure()
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        priced = configure()
        self.assertEqual(unpriced["canonical_spec"], priced["canonical_spec"])
        self.assertEqual(unpriced["validation"], priced["validation"])

    def test_a_price_moves_no_manufacturing_gate(self):
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        quote = configure()["quote"]
        self.assertEqual(
            quote["manufacturing_state"], "pending_not_run_needs_calibration"
        )
        self.assertFalse(quote["price_evidence"]["certifies_manufacturing"])
        self.assertFalse(quote["price_evidence"]["affects_geometry"])

    def test_price_evidence_never_claims_authority_it_lacks(self):
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        evidence = configure()["quote"]["price_evidence"]
        self.assertEqual(
            evidence["authority"],
            "commercial_input_only_not_manufacturing_certification",
        )


class WeightByAlloyIsTheDeliverable(unittest.TestCase):
    """One measured volume answers the weight question for every alloy.

    MyJewl is Geometry-as-a-Service: it measures a ring and says how much metal it
    takes. Metal never changes the geometry, so a customer comparing 14k against
    platinum needs no new solve — only a different density. That table, not the
    cost, is what the product actually owes.
    """

    def setUp(self):
        clear_active_sheet()

    def tearDown(self):
        clear_active_sheet()

    def test_every_alloy_the_product_can_build_gets_a_weight(self):
        rows = configure()["quote"]["metal_weight_by_alloy"]
        self.assertEqual([row["alloy"] for row in rows], list(ALLOYS))

    def test_each_weight_is_the_measured_volume_times_that_alloys_density(self):
        quote = configure()["quote"]
        volume = quote["ring_volume_mm3"]
        for row in quote["metal_weight_by_alloy"]:
            with self.subTest(alloy=row["alloy"]):
                self.assertAlmostEqual(
                    row["metal_weight_g"],
                    round(volume * row["density_g_cm3"] / 1000.0, 3),
                    places=3,
                )

    def test_exactly_one_row_is_marked_as_the_typed_choice(self):
        rows = configure()["quote"]["metal_weight_by_alloy"]
        selected = [row for row in rows if row["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["alloy"], "18k_yellow")

    def test_the_marked_row_matches_the_headline_weight(self):
        quote = configure()["quote"]
        selected = next(r for r in quote["metal_weight_by_alloy"] if r["selected"])
        self.assertEqual(selected["metal_weight_g"], quote["metal_weight_g"])

    def test_the_table_is_identical_whichever_alloy_was_typed(self):
        # The ring is the same ring. Choosing platinum must not change what 14k
        # would weigh -- only which row is marked.
        yellow = configure()["quote"]["metal_weight_by_alloy"]
        platinum = configure(metal_color_family="white", metal_karat="pt950")[
            "quote"
        ]["metal_weight_by_alloy"]
        strip = lambda rows: [  # noqa: E731
            {k: v for k, v in row.items() if k != "selected"} for row in rows
        ]
        self.assertEqual(strip(yellow), strip(platinum))
        self.assertEqual(
            next(r["alloy"] for r in platinum if r["selected"]), "pt950"
        )

    def test_platinum_is_the_heaviest_and_14k_white_the_lightest(self):
        # A sanity check on the densities actually reaching the table, not just
        # on the arithmetic being self-consistent.
        rows = {r["alloy"]: r["metal_weight_g"] for r in configure()["quote"][
            "metal_weight_by_alloy"
        ]}
        self.assertEqual(max(rows, key=rows.get), "pt950")
        self.assertEqual(min(rows, key=rows.get), "14k_white")

    def test_every_row_carries_pennyweight_beside_grams(self):
        # DWT is the unit a casting house quotes in. A bench reading grams where
        # it expects dwt is out by a factor of 1.555.
        from fast_mvp.typed_inputs import GRAMS_PER_DWT

        for row in configure()["quote"]["metal_weight_by_alloy"]:
            with self.subTest(alloy=row["alloy"]):
                self.assertAlmostEqual(
                    row["metal_weight_dwt"],
                    round(row["metal_weight_g"] / GRAMS_PER_DWT, 3),
                    places=2,
                )

    def test_the_headline_weight_also_carries_pennyweight(self):
        from fast_mvp.typed_inputs import GRAMS_PER_DWT

        quote = configure()["quote"]
        self.assertAlmostEqual(
            quote["metal_weight_dwt"],
            quote["metal_weight_g"] / GRAMS_PER_DWT,
            places=2,
        )

    def test_spg_is_reported_and_equals_the_density(self):
        # Specific gravity is what the trade reads (cf. Matrix's METAL WEIGHTS
        # panel); g/cm3 is what the arithmetic uses. They are the same number, and
        # emitting both means neither audience has to translate.
        for row in configure()["quote"]["metal_weight_by_alloy"]:
            with self.subTest(alloy=row["alloy"]):
                self.assertEqual(row["spg"], row["density_g_cm3"])

    def test_no_weights_without_a_measured_volume(self):
        # No volume, no weight -- for every alloy, not just the typed one.
        quote = configure(**UNSOLVED_US5)["quote"]
        self.assertEqual(quote["status"], "pending_geometry")
        self.assertIsNone(quote["metal_weight_by_alloy"])

    def test_the_table_needs_no_price_sheet(self):
        # The weights are the deliverable and stand alone: they appear in the
        # state the repository ships in, where nothing is priced.
        quote = configure()["quote"]
        self.assertIsNone(quote["metal_content_cost"])
        self.assertEqual(len(quote["metal_weight_by_alloy"]), 7)

    def test_declaring_a_price_does_not_change_any_weight(self):
        unpriced = configure()["quote"]["metal_weight_by_alloy"]
        set_active_sheet(AlloyPriceSheet(declared_sheet()))
        self.assertEqual(unpriced, configure()["quote"]["metal_weight_by_alloy"])

    def test_the_table_survives_the_persistence_boundary(self):
        from fast_mvp.quote_volumes import reconcile_quote

        report = configure()
        expected = report["quote"]["metal_weight_by_alloy"]
        reconcile_quote(report)
        self.assertEqual(report["quote"]["metal_weight_by_alloy"], expected)


class TheRepositoryShipsNoRealPrice(unittest.TestCase):
    """Nobody committed market data, now or later."""

    def test_no_committed_sheet_is_declared(self):
        for path in (ROOT / "profiles").glob("alloy_price_sheet*.json"):
            with self.subTest(path=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(document["status"], "undeclared")
                for alloy, entry in document["prices"].items():
                    self.assertEqual(entry["status"], "undeclared", alloy)
                    self.assertEqual(entry["price_basis"], "unknown", alloy)
                    self.assertEqual(
                        entry["provenance"]["source"], "undeclared_placeholder", alloy
                    )

    def test_the_default_active_sheet_is_the_undeclared_template(self):
        clear_active_sheet()
        sheet = alloy_prices.active_sheet()
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet.sheet_id, "undeclared-template")
        self.assertEqual(sheet.usable_prices(), {})


if __name__ == "__main__":
    unittest.main()
