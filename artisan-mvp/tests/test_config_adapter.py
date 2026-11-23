"""Tests for the flat -> canonical adapter (G-6 / R-10).

Proves the adapter closes the silent constraint-filter no-op: an invalid flat
config (stone too large for the ring size) yields HARD violations after
adaptation, while a valid flat config round-trips to accepted=True.
"""

import copy
import unittest

from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.validation import validate_job_spec


def _valid_flat() -> dict:
    """Representative valid flat config: round 1.00ct, 4-prong, 14k yellow, US 6.5."""
    return {
        "config_id": "config_solitaire_round_1.00ct_prong4_14ky_2.0mm_us65",
        "archetype": "solitaire",
        "stone_shape": "round",
        "stone_length_mm": 6.5,
        "stone_width_mm": 6.5,
        "stone_depth_mm": 3.9,
        "stone_carat_eq": 1.0,
        "stone_color": "diamond_colorless",
        "center_setting": "prong",
        "prong_count": 4,
        "metal_type": "gold",
        "metal_alloy": "14k_yellow",
        "band_width_mm": 2.0,
        "band_thickness_mm": 1.6,
        "ring_size_us": 6.5,
    }


class FlatToCanonicalTests(unittest.TestCase):
    def test_valid_flat_round_trips_accepted(self):
        canonical = flat_to_canonical(_valid_flat())
        result = constraints.validate(canonical)
        self.assertTrue(result.accepted)
        self.assertEqual(result.hard_violations, [])
        # The adapter must also produce a schema-valid canonical spec.
        self.assertTrue(validate_job_spec(canonical).accepted)

    def test_invalid_stone_too_large_yields_hard(self):
        flat = _valid_flat()
        # Stone far too wide for a US 6.5 ring with a 2.0 mm band (BAND-005:
        # max_stone = 16.9132 - 2*2.0 - 3.0 ~= 9.91 mm).
        flat["stone_length_mm"] = 12.0
        flat["stone_width_mm"] = 12.0

        canonical = flat_to_canonical(flat)
        result = constraints.validate(canonical)

        self.assertFalse(result.accepted)
        self.assertGreater(len(result.hard_violations), 0)
        self.assertIn("BAND-005", {v.rule_id for v in result.hard_violations})

    def test_missing_required_field_raises(self):
        flat = _valid_flat()
        del flat["ring_size_us"]
        with self.assertRaises(ValueError):
            flat_to_canonical(flat)

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            flat_to_canonical(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
