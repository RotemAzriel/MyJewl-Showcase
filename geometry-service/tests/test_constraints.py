"""Test suite for jewelry_gaas_foundation.constraints (M2 Revision).

8 legacy adversarial cases (TC-01..TC-08 from evaluation_framework.md §4) +
100% current HARD contract-rule coverage + representative SOFT/ADVISORY
spot-checks + tests for cited shape factors, schema gaps, and count rules.

Run with:
    python3 -m unittest tests.test_constraints -v
    pytest tests/test_constraints.py -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jewelry_gaas_foundation.constraints import (
    ConstraintResult,
    ConstraintViolation,
    _derive_carat,
    _emerald_carat_factor,
    _inner_diameter_mm,
    _shape_factor,
    check_constraints,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
SOLITAIRE_PATH = ROOT / "examples" / "solitaire_valid.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_solitaire() -> dict:
    """Minimal valid US-6.5 solitaire spec — must always pass all HARD rules."""
    return {
        "spec_version": "0.1",
        "job": {
            "job_id": "test_base",
            "created_at": "2026-05-20T00:00:00Z",
            "archetype": "solitaire",
            "output_targets": ["GLB", "STL"],
        },
        "ring": {
            "size_standard": "US",
            "size": 6.5,
            "band_width_mm": 2.0,
            "band_thickness_mm": 1.6,
        },
        "metal": {
            "type": "gold",
            "alloy": "14k yellow gold",
            "finish": "polished",
        },
        "stones": [
            {
                "stone_id": "center_001",
                "role": "center",
                "shape": "round",
                "dimensions_mm": {"length": 6.5, "width": 6.5, "depth": 3.9},
            }
        ],
        "setting": {
            "center_setting": "prong",
            "prong_count": 4,
            "halo_style": "none",
            "side_stone_layout": "none",
        },
    }


def _hard_ids(result: ConstraintResult) -> set[str]:
    return {v.rule_id for v in result.hard_violations}


def _soft_ids(result: ConstraintResult) -> set[str]:
    return {v.rule_id for v in result.soft_warnings}


def _advisory_ids(result: ConstraintResult) -> set[str]:
    return {v.rule_id for v in result.advisory_notes}


def _side_stones(count: int, shape: str = "round",
                 length: float = 4.0, width: float = 4.0, depth: float = 2.5) -> list[dict]:
    return [
        {
            "stone_id": f"side_{i}",
            "role": "side",
            "shape": shape,
            "dimensions_mm": {"length": length, "width": width, "depth": depth},
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# TC-01..TC-08: 8 mandatory adversarial cases (evaluation_framework.md §4)
# ---------------------------------------------------------------------------

class TestAdversarialCases(unittest.TestCase):

    def test_TC01_band001_band_width_1_5(self):
        """TC-01: uncalibrated band-width threshold is SOFT, not certifying."""
        spec = _base_solitaire()
        spec["ring"]["band_width_mm"] = 1.5
        r = check_constraints(spec)
        self.assertTrue(r.accepted)
        self.assertIn("BAND-001", _soft_ids(r))
        self.assertNotIn("BAND-001", _hard_ids(r))

    def test_TC02_stone005_bezel_plus_halo(self):
        """TC-02: bezel + halo is valid but requires geometry review."""
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["halo_style"] = "single"
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 0
        spec["stones"].append({
            "stone_id": "halo_001", "role": "halo", "shape": "round",
            "dimensions_mm": {"length": 1.3, "width": 1.3, "depth": 0.8},
        })
        r = check_constraints(spec)
        self.assertTrue(r.accepted)
        self.assertIn("STONE-005", _advisory_ids(r))
        self.assertNotIn("STONE-005", _hard_ids(r))

    def test_TC03_prong002_round_2_prongs(self):
        """TC-03: round + two prongs is outside the catalog baseline, not universal HARD."""
        spec = _base_solitaire()
        spec["setting"]["prong_count"] = 2
        r = check_constraints(spec)
        self.assertTrue(r.accepted)
        self.assertIn("PRONG-002", _soft_ids(r))
        self.assertNotIn("PRONG-002", _hard_ids(r))
        self.assertNotIn("PRONG-001", _hard_ids(r))

    def test_TC04_stone011_heart_shape(self):
        """TC-04: heart shape → STONE-011 HARD (not in schema v0.1)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "heart"
        r = check_constraints(spec)
        self.assertFalse(r.accepted)
        self.assertIn("STONE-011", _hard_ids(r))

    def test_TC05_stone008_trilogy_asymmetric_sides(self):
        """TC-05: trilogy + mismatched side stones → STONE-008 HARD."""
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["ring"]["band_width_mm"] = 2.0
        spec["stones"].append({"stone_id": "side_L", "role": "side", "shape": "round",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        spec["stones"].append({"stone_id": "side_R", "role": "side", "shape": "oval",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        r = check_constraints(spec)
        self.assertFalse(r.accepted)
        self.assertIn("STONE-008", _hard_ids(r))

    def test_TC06_setting002_channel_center(self):
        """TC-06: center_setting='channel' → SETTING-002 HARD."""
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "channel"
        r = check_constraints(spec)
        self.assertFalse(r.accepted)
        self.assertIn("SETTING-002", _hard_ids(r))

    def test_TC07_solitaire_valid_passes(self):
        """TC-07: solitaire_valid.json → accepted=True, 0 hard violations."""
        spec = json.loads(SOLITAIRE_PATH.read_text(encoding="utf-8"))
        r = check_constraints(spec)
        self.assertTrue(r.accepted)
        self.assertEqual(r.hard_violations, [])

    def test_TC08_prong009_bezel_nonzero_prong_count(self):
        """TC-08: bezel + prong_count>0 → PRONG-009 HARD."""
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 4
        r = check_constraints(spec)
        self.assertFalse(r.accepted)
        self.assertIn("PRONG-009", _hard_ids(r))


# ---------------------------------------------------------------------------
# BAND rules — uncalibrated dimensional baselines are SOFT
# ---------------------------------------------------------------------------

class TestBand001(unittest.TestCase):
    """BAND-001: band_width_mm ≥ 1.8 mm."""

    def test_below_minimum_fails(self):
        spec = _base_solitaire()
        spec["ring"]["band_width_mm"] = 1.5
        self.assertIn("BAND-001", _soft_ids(check_constraints(spec)))

    def test_at_minimum_passes(self):
        spec = _base_solitaire()
        spec["ring"]["band_width_mm"] = 1.8
        self.assertNotIn("BAND-001", _soft_ids(check_constraints(spec)))

    def test_above_minimum_passes(self):
        spec = _base_solitaire()
        spec["ring"]["band_width_mm"] = 2.5
        self.assertNotIn("BAND-001", _soft_ids(check_constraints(spec)))


class TestBand002(unittest.TestCase):
    """BAND-002: band_thickness_mm ≥ 1.5 mm."""

    def test_below_minimum_fails(self):
        spec = _base_solitaire()
        spec["ring"]["band_thickness_mm"] = 1.2
        self.assertIn("BAND-002", _soft_ids(check_constraints(spec)))

    def test_at_minimum_passes(self):
        spec = _base_solitaire()
        spec["ring"]["band_thickness_mm"] = 1.5
        self.assertNotIn("BAND-002", _soft_ids(check_constraints(spec)))


class TestBand003(unittest.TestCase):
    """BAND-003: knife-edge profile → band_width ≥ 2.0 mm."""

    def test_knife_edge_narrow_fails(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "knife-edge"
        spec["ring"]["band_width_mm"] = 1.9
        self.assertIn("BAND-003", _soft_ids(check_constraints(spec)))

    def test_knife_edge_adequate_passes(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "knife-edge"
        spec["ring"]["band_width_mm"] = 2.0
        self.assertNotIn("BAND-003", _soft_ids(check_constraints(spec)))

    def test_no_profile_no_trigger(self):
        spec = _base_solitaire()
        spec["ring"]["band_width_mm"] = 1.9
        self.assertNotIn("BAND-003", _soft_ids(check_constraints(spec)))


class TestBand004(unittest.TestCase):
    """BAND-004: comfort-fit profile → band_thickness ≥ 1.5 mm."""

    def test_comfort_thin_fails(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "comfort-fit"
        spec["ring"]["band_thickness_mm"] = 1.2
        self.assertIn("BAND-004", _soft_ids(check_constraints(spec)))

    def test_comfort_adequate_passes(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "comfort-fit"
        spec["ring"]["band_thickness_mm"] = 1.5
        self.assertNotIn("BAND-004", _soft_ids(check_constraints(spec)))


class TestBand005(unittest.TestCase):
    """BAND-005: invalid finger-bore proxy is deferred to geometry."""

    def test_wide_stone_does_not_trigger_a_false_hard_reject(self):
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"]["width"] = 11.0
        r = check_constraints(spec)
        self.assertNotIn("BAND-005", _hard_ids(r))
        self.assertTrue(any("BAND-005" in gap for gap in r.schema_gaps))

    def test_stone_within_limit_passes(self):
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"]["width"] = 6.5
        self.assertNotIn("BAND-005", _hard_ids(check_constraints(spec)))

    def test_non_us_not_triggered(self):
        spec = _base_solitaire()
        spec["ring"]["size_standard"] = "EU"
        spec["stones"][0]["dimensions_mm"]["width"] = 50.0
        self.assertNotIn("BAND-005", _hard_ids(check_constraints(spec)))


class TestBand007(unittest.TestCase):
    """BAND-007: trilogy → band_width ≥ 2.0 mm."""

    def _trilogy(self) -> dict:
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["stones"].extend(_side_stones(2))
        return spec

    def test_trilogy_narrow_fails(self):
        spec = self._trilogy()
        spec["ring"]["band_width_mm"] = 1.9
        self.assertIn("BAND-007", _soft_ids(check_constraints(spec)))

    def test_trilogy_adequate_passes(self):
        spec = self._trilogy()
        spec["ring"]["band_width_mm"] = 2.0
        self.assertNotIn("BAND-007", _soft_ids(check_constraints(spec)))


class TestBand008(unittest.TestCase):
    """BAND-008: comfort-fit + integer ring size → ADVISORY."""

    def test_comfort_integer_size_advisory(self):
        """US size 6 (integer) with comfort-fit → BAND-008 ADVISORY."""
        spec = _base_solitaire()
        spec["ring"]["profile"] = "comfort-fit"
        spec["ring"]["size"] = 6
        spec["ring"]["band_thickness_mm"] = 1.6  # pass BAND-004
        r = check_constraints(spec)
        self.assertIn("BAND-008", _advisory_ids(r))

    def test_comfort_half_size_no_advisory(self):
        """US size 6.5 (half-size) → no BAND-008 advisory."""
        spec = _base_solitaire()
        spec["ring"]["profile"] = "comfort-fit"
        spec["ring"]["size"] = 6.5
        spec["ring"]["band_thickness_mm"] = 1.6
        r = check_constraints(spec)
        self.assertNotIn("BAND-008", _advisory_ids(r))

    def test_no_comfort_no_band008(self):
        spec = _base_solitaire()
        spec["ring"]["size"] = 6
        r = check_constraints(spec)
        self.assertNotIn("BAND-008", _advisory_ids(r))

    def test_band008_does_not_reject(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "comfort-fit"
        spec["ring"]["size"] = 6
        spec["ring"]["band_thickness_mm"] = 1.6
        self.assertTrue(check_constraints(spec).accepted)


class TestBand009(unittest.TestCase):
    """BAND-009: knife-edge profile → manufacturing ADVISORY."""

    def test_knife_edge_advisory(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "knife-edge"
        spec["ring"]["band_width_mm"] = 2.0
        r = check_constraints(spec)
        self.assertIn("BAND-009", _advisory_ids(r))

    def test_knife_profile_advisory(self):
        """'knife' substring match."""
        spec = _base_solitaire()
        spec["ring"]["profile"] = "knife"
        spec["ring"]["band_width_mm"] = 2.0
        r = check_constraints(spec)
        self.assertIn("BAND-009", _advisory_ids(r))

    def test_no_knife_no_band009(self):
        spec = _base_solitaire()
        r = check_constraints(spec)
        self.assertNotIn("BAND-009", _advisory_ids(r))

    def test_band009_does_not_reject(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "knife-edge"
        spec["ring"]["band_width_mm"] = 2.0
        self.assertTrue(check_constraints(spec).accepted)


# ---------------------------------------------------------------------------
# PRONG rules
# ---------------------------------------------------------------------------

class TestProng001And002(unittest.TestCase):
    """PRONG-001: round stone → prong_count ∈ {3,4,6}.
       PRONG-002: round stone + prong_count == 2 (distinct sub-rule).
    """

    def test_prong001_count_1(self):
        spec = _base_solitaire()
        spec["setting"]["prong_count"] = 1
        self.assertIn("PRONG-001", _soft_ids(check_constraints(spec)))

    def test_prong001_count_5(self):
        spec = _base_solitaire()
        spec["setting"]["prong_count"] = 5
        self.assertIn("PRONG-001", _soft_ids(check_constraints(spec)))

    def test_prong001_count_7(self):
        spec = _base_solitaire()
        spec["setting"]["prong_count"] = 7
        self.assertIn("PRONG-001", _soft_ids(check_constraints(spec)))

    def test_prong001_valid_counts_pass(self):
        for count in (3, 4, 6):
            with self.subTest(prong_count=count):
                spec = _base_solitaire()
                spec["setting"]["prong_count"] = count
                self.assertNotIn("PRONG-001", _soft_ids(check_constraints(spec)))

    def test_prong002_fires_for_count_2_not_prong001(self):
        """prong_count=2 fires PRONG-002, not PRONG-001."""
        spec = _base_solitaire()
        spec["setting"]["prong_count"] = 2
        ids = _soft_ids(check_constraints(spec))
        self.assertIn("PRONG-002", ids)
        self.assertNotIn("PRONG-001", ids)

    def test_prong002_not_fired_for_valid_counts(self):
        for count in (3, 4, 6):
            with self.subTest(prong_count=count):
                spec = _base_solitaire()
                spec["setting"]["prong_count"] = count
                self.assertNotIn("PRONG-002", _soft_ids(check_constraints(spec)))


class TestProng003Advisory(unittest.TestCase):
    """PRONG-003: pear/marquise + prong → ADVISORY in v0.1.

    GIA supports protecting pointed tips, but prong_style is a v0.2
    schema gap, so the implementation cannot verify V-tip geometry.
    """

    def test_pear_prong_is_advisory(self):
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _advisory_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_marquise_prong_is_advisory(self):
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "marquise"
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _advisory_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_round_prong_no_prong003(self):
        spec = _base_solitaire()
        r = check_constraints(spec)
        self.assertNotIn("PRONG-003", _advisory_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_prong003_advisory_does_not_reject(self):
        """PRONG-003 advisory alone must not set accepted=False."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 5  # adequate count — no SOFT fires
        self.assertTrue(check_constraints(spec).accepted)


class TestProng003CountEnforcement(unittest.TestCase):
    """PRONG-003 count enforcement: pear and marquise prong counts."""

    def test_pear_low_prong_count_soft(self):
        """Pear + prong_count=3 → PRONG-003 SOFT (insufficient)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 3
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _soft_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_pear_four_prong_soft(self):
        """Pear + prong_count=4 → PRONG-003 SOFT (marginal coverage at pointed tip)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 4
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _soft_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_pear_adequate_prong_count_passes(self):
        """Pear + prong_count=5 → no SOFT PRONG-003 (standard count)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 5
        r = check_constraints(spec)
        self.assertNotIn("PRONG-003", _soft_ids(r))

    def test_pear_six_prong_passes(self):
        """Pear + prong_count=6 → no SOFT PRONG-003."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 6
        r = check_constraints(spec)
        self.assertNotIn("PRONG-003", _soft_ids(r))

    def test_marquise_unusual_prong_count_soft(self):
        """Marquise + prong_count=3 → PRONG-003 SOFT."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "marquise"
        spec["setting"]["prong_count"] = 3
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _soft_ids(r))
        self.assertNotIn("PRONG-003", _hard_ids(r))

    def test_marquise_four_prong_ok(self):
        """Marquise + prong_count=4 → no SOFT PRONG-003 (4-prong with V-tips is accepted)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "marquise"
        spec["setting"]["prong_count"] = 4
        r = check_constraints(spec)
        self.assertNotIn("PRONG-003", _soft_ids(r))

    def test_marquise_six_prong_ok(self):
        """Marquise + prong_count=6 → no SOFT PRONG-003."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "marquise"
        spec["setting"]["prong_count"] = 6
        r = check_constraints(spec)
        self.assertNotIn("PRONG-003", _soft_ids(r))

    def test_count_soft_does_not_reject(self):
        """Count SOFT violation still returns accepted=True."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 3
        self.assertTrue(check_constraints(spec).accepted)


class TestProng007(unittest.TestCase):
    """PRONG-007: stone < 1.5 mm + prong_count > 0 → SOFT review."""

    def test_tiny_stone_with_prongs_warns(self):
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"] = {"length": 1.2, "width": 1.2, "depth": 0.7}
        spec["setting"]["prong_count"] = 4
        self.assertIn("PRONG-007", _soft_ids(check_constraints(spec)))

    def test_tiny_stone_zero_prongs_passes(self):
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"] = {"length": 1.2, "width": 1.2, "depth": 0.7}
        spec["setting"]["prong_count"] = 0
        spec["setting"]["center_setting"] = "bezel"
        self.assertNotIn("PRONG-007", _soft_ids(check_constraints(spec)))

    def test_normal_stone_passes(self):
        spec = _base_solitaire()  # 6.5mm stone
        self.assertNotIn("PRONG-007", _soft_ids(check_constraints(spec)))


class TestProng009(unittest.TestCase):
    """PRONG-009: bezel + prong_count > 0 → HARD."""

    def test_bezel_with_prongs_fails(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 4
        self.assertIn("PRONG-009", _hard_ids(check_constraints(spec)))

    def test_bezel_zero_prongs_passes(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 0
        self.assertNotIn("PRONG-009", _hard_ids(check_constraints(spec)))

    def test_prong_setting_not_triggered(self):
        spec = _base_solitaire()
        self.assertNotIn("PRONG-009", _hard_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# METAL rules
# ---------------------------------------------------------------------------

class TestMetal001(unittest.TestCase):
    """METAL-001: gold/silver + ≥1ct + cathedral → SOFT advisory (M2 Revision)."""

    def test_gold_cathedral_1ct_soft_warning(self):
        """Gold + cathedral + 1ct → METAL-001 SOFT (not HARD)."""
        spec = _base_solitaire()
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 2.0
        spec["stones"][0]["carat"] = 1.0
        r = check_constraints(spec)
        self.assertIn("METAL-001", _soft_ids(r))
        self.assertNotIn("METAL-001", _hard_ids(r))
        self.assertTrue(r.accepted)

    def test_gold_no_cathedral_1ct_no_metal001(self):
        spec = _base_solitaire()
        spec["stones"][0]["carat"] = 1.0
        self.assertNotIn("METAL-001", _soft_ids(check_constraints(spec)))

    def test_gold_cathedral_sub_1ct_no_metal001(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 2.0
        spec["stones"][0]["carat"] = 0.9
        self.assertNotIn("METAL-001", _soft_ids(check_constraints(spec)))


class TestMetal002(unittest.TestCase):
    """METAL-002: platinum + ≥1ct + cathedral → SOFT advisory (M2 Revision)."""

    def test_platinum_cathedral_1ct_soft_warning(self):
        """Platinum + cathedral + 1ct → METAL-002 SOFT (not HARD)."""
        spec = _base_solitaire()
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 2.0
        spec["metal"]["type"] = "platinum"
        spec["metal"]["alloy"] = "Pt950"
        spec["stones"][0]["carat"] = 1.0
        r = check_constraints(spec)
        self.assertIn("METAL-002", _soft_ids(r))
        self.assertNotIn("METAL-002", _hard_ids(r))
        self.assertTrue(r.accepted)


# ---------------------------------------------------------------------------
# SETTING rules — contract rules are HARD; empirical compatibility is SOFT
# ---------------------------------------------------------------------------

class TestSetting001(unittest.TestCase):
    """SETTING-001: tension + shape compatibility.

    Shape-only compatibility cannot certify tension-setting feasibility, so
    every flagged shape is SOFT pending girdle and shop-process inputs.
    """

    def test_tension_pear_warns(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["stones"][0]["shape"] = "pear"
        r = check_constraints(spec)
        self.assertIn("SETTING-001", _soft_ids(r))
        self.assertNotIn("SETTING-001", _hard_ids(r))

    def test_tension_marquise_warns(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["stones"][0]["shape"] = "marquise"
        r = check_constraints(spec)
        self.assertIn("SETTING-001", _soft_ids(r))
        self.assertNotIn("SETTING-001", _hard_ids(r))

    def test_tension_heart_setting_rule_is_soft(self):
        """Heart remains unsupported by STONE-011, but SETTING-001 itself is SOFT."""
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["stones"][0]["shape"] = "heart"
        r = check_constraints(spec)
        self.assertIn("SETTING-001", _soft_ids(r))
        self.assertNotIn("SETTING-001", _hard_ids(r))

    def test_tension_oval_soft(self):
        """Oval + tension → SETTING-001 SOFT (not HARD) after M2 Revision."""
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["setting"]["prong_count"] = 0
        spec["stones"][0]["shape"] = "oval"
        r = check_constraints(spec)
        self.assertIn("SETTING-001", _soft_ids(r))
        self.assertNotIn("SETTING-001", _hard_ids(r))
        self.assertTrue(r.accepted)

    def test_tension_cushion_soft(self):
        """Cushion + tension → SETTING-001 SOFT (not HARD) after M2 Revision."""
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["setting"]["prong_count"] = 0
        spec["stones"][0]["shape"] = "cushion"
        r = check_constraints(spec)
        self.assertIn("SETTING-001", _soft_ids(r))
        self.assertNotIn("SETTING-001", _hard_ids(r))
        self.assertTrue(r.accepted)

    def test_tension_round_passes(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "tension"
        spec["setting"]["prong_count"] = 0
        spec["stones"][0]["shape"] = "round"
        self.assertNotIn("SETTING-001", _hard_ids(check_constraints(spec)))

    def test_prong_pear_no_setting001(self):
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        self.assertNotIn("SETTING-001", _hard_ids(check_constraints(spec)))


class TestSetting002(unittest.TestCase):
    """SETTING-002: channel center setting → HARD."""

    def test_channel_fails(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "channel"
        self.assertIn("SETTING-002", _hard_ids(check_constraints(spec)))

    def test_prong_passes(self):
        self.assertNotIn("SETTING-002", _hard_ids(check_constraints(_base_solitaire())))

    def test_bezel_no_setting002(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 0
        self.assertNotIn("SETTING-002", _hard_ids(check_constraints(spec)))


class TestSetting003(unittest.TestCase):
    """SETTING-003: cathedral + band_width < 2.0 mm → SOFT."""

    def test_cathedral_narrow_warns(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 1.8
        self.assertIn("SETTING-003", _soft_ids(check_constraints(spec)))

    def test_cathedral_adequate_passes(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 2.0
        self.assertNotIn("SETTING-003", _soft_ids(check_constraints(spec)))


class TestSetting004(unittest.TestCase):
    """SETTING-004: low-profile + center_depth > 6.0 mm → SOFT."""

    def test_low_profile_deep_warns(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "low-profile"
        spec["stones"][0]["dimensions_mm"]["depth"] = 7.0
        self.assertIn("SETTING-004", _soft_ids(check_constraints(spec)))

    def test_low_profile_shallow_passes(self):
        spec = _base_solitaire()
        spec["ring"]["profile"] = "low-profile"
        spec["stones"][0]["dimensions_mm"]["depth"] = 5.0
        self.assertNotIn("SETTING-004", _soft_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# STONE rules
# ---------------------------------------------------------------------------

class TestStone005(unittest.TestCase):
    """STONE-005: bezel + halo is valid and receives geometry review."""

    def test_halo_bezel_is_advisory(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["halo_style"] = "single"
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 0
        spec["stones"].append({"stone_id": "halo_001", "role": "halo", "shape": "round",
                               "dimensions_mm": {"length": 1.3, "width": 1.3, "depth": 0.8}})
        r = check_constraints(spec)
        self.assertIn("STONE-005", _advisory_ids(r))
        self.assertNotIn("STONE-005", _hard_ids(r))

    def test_halo_prong_passes(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["halo_style"] = "single"
        spec["setting"]["center_setting"] = "prong"
        spec["stones"].append({"stone_id": "halo_001", "role": "halo", "shape": "round",
                               "dimensions_mm": {"length": 1.3, "width": 1.3, "depth": 0.8}})
        self.assertNotIn("STONE-005", _advisory_ids(check_constraints(spec)))

    def test_no_halo_bezel_no_stone005(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 0
        self.assertNotIn("STONE-005", _advisory_ids(check_constraints(spec)))


class TestStone007(unittest.TestCase):
    """STONE-007: trilogy → exactly 2 side stones."""

    def test_zero_side_stones_fails(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        self.assertIn("STONE-007", _hard_ids(check_constraints(spec)))

    def test_one_side_stone_fails(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["stones"].extend(_side_stones(1))
        self.assertIn("STONE-007", _hard_ids(check_constraints(spec)))

    def test_two_side_stones_passes(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["ring"]["band_width_mm"] = 2.0
        spec["stones"].extend(_side_stones(2))
        self.assertNotIn("STONE-007", _hard_ids(check_constraints(spec)))


class TestStone008(unittest.TestCase):
    """STONE-008: trilogy side stones must be identical in shape and dimensions."""

    def _trilogy_base(self) -> dict:
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["ring"]["band_width_mm"] = 2.0
        return spec

    def test_mismatched_shape_fails(self):
        spec = self._trilogy_base()
        spec["stones"].append({"stone_id": "side_L", "role": "side", "shape": "round",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        spec["stones"].append({"stone_id": "side_R", "role": "side", "shape": "oval",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        self.assertIn("STONE-008", _hard_ids(check_constraints(spec)))

    def test_mismatched_dimensions_fails(self):
        spec = self._trilogy_base()
        spec["stones"].append({"stone_id": "side_L", "role": "side", "shape": "round",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        spec["stones"].append({"stone_id": "side_R", "role": "side", "shape": "round",
                               "dimensions_mm": {"length": 3.5, "width": 3.5, "depth": 2.5}})
        self.assertIn("STONE-008", _hard_ids(check_constraints(spec)))

    def test_matching_stones_passes(self):
        spec = self._trilogy_base()
        spec["stones"].extend(_side_stones(2))
        self.assertNotIn("STONE-008", _hard_ids(check_constraints(spec)))


class TestStone011(unittest.TestCase):
    """STONE-011: heart shape → HARD (not supported in schema v0.1)."""

    def test_heart_fails(self):
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "heart"
        self.assertIn("STONE-011", _hard_ids(check_constraints(spec)))

    def test_supported_shapes_pass(self):
        for shape in ("round", "oval", "cushion", "pear", "emerald", "radiant", "princess"):
            with self.subTest(shape=shape):
                spec = _base_solitaire()
                spec["stones"][0]["shape"] = shape
                self.assertNotIn("STONE-011", _hard_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# ARCH rules — 100% HARD coverage
# ---------------------------------------------------------------------------

class TestArch001(unittest.TestCase):
    """ARCH-001: solitaire + halo_style ≠ 'none' → HARD."""

    def test_solitaire_with_halo_style_fails(self):
        spec = _base_solitaire()
        spec["setting"]["halo_style"] = "single"
        self.assertIn("ARCH-001", _hard_ids(check_constraints(spec)))

    def test_solitaire_none_halo_passes(self):
        spec = _base_solitaire()
        spec["setting"]["halo_style"] = "none"
        self.assertNotIn("ARCH-001", _hard_ids(check_constraints(spec)))


class TestArch002(unittest.TestCase):
    """ARCH-002: solitaire/halo + side_stone_layout ≠ 'none' → HARD."""

    def test_solitaire_with_side_layout_fails(self):
        spec = _base_solitaire()
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        self.assertIn("ARCH-002", _hard_ids(check_constraints(spec)))

    def test_halo_with_side_layout_fails(self):
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        self.assertIn("ARCH-002", _hard_ids(check_constraints(spec)))

    def test_solitaire_none_side_layout_passes(self):
        spec = _base_solitaire()
        spec["setting"]["side_stone_layout"] = "none"
        self.assertNotIn("ARCH-002", _hard_ids(check_constraints(spec)))


class TestArch003(unittest.TestCase):
    """ARCH-003: solitaire/halo + stone with role='side' → HARD."""

    def test_side_stone_in_solitaire_fails(self):
        spec = _base_solitaire()
        spec["stones"].append({"stone_id": "side_001", "role": "side", "shape": "round",
                               "dimensions_mm": {"length": 4.0, "width": 4.0, "depth": 2.5}})
        self.assertIn("ARCH-003", _hard_ids(check_constraints(spec)))

    def test_accent_stone_in_solitaire_passes(self):
        spec = _base_solitaire()
        spec["stones"].append({"stone_id": "accent_001", "role": "accent", "shape": "round",
                               "dimensions_mm": {"length": 1.2, "width": 1.2, "depth": 0.7}})
        self.assertNotIn("ARCH-003", _hard_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# Schema gaps — geometry-gate deferred rules (M2 Revision)
# ---------------------------------------------------------------------------

class TestSchemaGaps(unittest.TestCase):
    """Geometry-gate deferred rules are always documented in schema_gaps."""

    def test_stone002_003_004_always_deferred(self):
        """STONE-002, STONE-003, STONE-004 must appear individually in schema_gaps."""
        r = check_constraints(_base_solitaire())
        gap_text = " ".join(r.schema_gaps)
        self.assertIn("STONE-002", gap_text)
        self.assertIn("STONE-003", gap_text)
        self.assertIn("STONE-004", gap_text)

    def test_prong_style_gap_documented(self):
        r = check_constraints(_base_solitaire())
        self.assertTrue(any("prong_style" in g for g in r.schema_gaps))

    def test_halo_radius_gap_documented(self):
        r = check_constraints(_base_solitaire())
        self.assertTrue(any("halo_radius_mm" in g for g in r.schema_gaps))

    def test_clearance_rules_in_schema_gaps(self):
        """CLEARANCE-001/002/003 must all appear in schema_gaps (M2 Revision)."""
        r = check_constraints(_base_solitaire())
        gap_text = " ".join(r.schema_gaps)
        self.assertIn("CLEARANCE-001", gap_text)
        self.assertIn("CLEARANCE-002", gap_text)
        self.assertIn("CLEARANCE-003", gap_text)

    def test_stone009_in_schema_gaps(self):
        """STONE-009 must appear in schema_gaps (M2 Revision)."""
        r = check_constraints(_base_solitaire())
        self.assertTrue(any("STONE-009" in g for g in r.schema_gaps))

    def test_prong006_in_schema_gaps(self):
        """PRONG-006 must appear in schema_gaps (M2 Revision)."""
        r = check_constraints(_base_solitaire())
        self.assertTrue(any("PRONG-006" in g for g in r.schema_gaps))

    def test_metal001_002_in_schema_gaps(self):
        """METAL-001/002 gap note must appear in schema_gaps (M2 Revision)."""
        r = check_constraints(_base_solitaire())
        gap_text = " ".join(r.schema_gaps)
        self.assertIn("METAL-001/002", gap_text)


# ---------------------------------------------------------------------------
# Weight shape factors (M2 Revision — Finding 1)
# ---------------------------------------------------------------------------

class TestWeightShapeFactors(unittest.TestCase):
    """_derive_carat() must use the cited GIA shape formulas and tables."""

    def _stone(self, shape, length=7.0, width=5.0, depth=3.5) -> dict:
        return {
            "stone_id": "test_s",
            "role": "center",
            "shape": shape,
            "dimensions_mm": {"length": length, "width": width, "depth": depth},
        }

    def test_emerald_factor_higher_than_round(self):
        """An emerald at 1.4:1 uses interpolation between GIA table points."""
        emerald_ct = _derive_carat(self._stone("emerald", 7.0, 5.0, 3.5))
        round_ct = _derive_carat(self._stone("round", 7.0, 5.0, 3.5))
        self.assertIsNotNone(emerald_ct)
        self.assertIsNotNone(round_ct)
        self.assertGreater(emerald_ct, round_ct)

    def test_princess_factor_higher_than_round(self):
        """Princess (0.0083) should yield higher carat than round (0.0061) for same dims."""
        princess_ct = _derive_carat(self._stone("princess"))
        round_ct = _derive_carat(self._stone("round"))
        self.assertGreater(princess_ct, round_ct)

    def test_round_factor_is_0061(self):
        """Round brilliant uses exactly 0.0061."""
        ct = _derive_carat(self._stone("round", 6.5, 6.5, 3.9))
        self.assertAlmostEqual(ct, 6.5 * 6.5 * 3.9 * 0.0061, places=6)

    def test_oval_uses_average_girdle_diameter_squared(self):
        """GIA oval estimate is AGD² × depth × 0.0062."""
        ct = _derive_carat(self._stone("oval", 7.9, 5.6, 3.69))
        expected = ((7.9 + 5.6) / 2.0) ** 2 * 3.69 * 0.0062
        self.assertAlmostEqual(ct, expected, places=6)

    def test_emerald_table_points(self):
        """Emerald/radiant factor lookup follows the GIA L:W table."""
        for ratio, expected in (
            (1.0, 0.0080),
            (1.5, 0.0092),
            (2.0, 0.0100),
            (2.5, 0.0106),
        ):
            with self.subTest(ratio=ratio):
                self.assertAlmostEqual(_emerald_carat_factor(ratio, 1.0), expected, places=7)

    def test_emerald_factor_interpolates(self):
        self.assertAlmostEqual(_emerald_carat_factor(1.25, 1.0), 0.0086, places=7)

    def test_pear_and_marquise_table_points(self):
        self.assertAlmostEqual(_shape_factor("pear", 1.50, 1.0), 0.00600, places=7)
        self.assertAlmostEqual(_shape_factor("pear", 2.00, 1.0), 0.00575, places=7)
        self.assertAlmostEqual(_shape_factor("marquise", 2.00, 1.0), 0.00580, places=7)
        self.assertAlmostEqual(_shape_factor("marquise", 3.00, 1.0), 0.00595, places=7)

    def test_radiant_uses_emerald_ratio_table(self):
        self.assertAlmostEqual(_shape_factor("radiant", 1.50, 1.0), 0.0092, places=7)

    def test_weight001_no_false_positive_cushion(self):
        """A valid 1.05ct cushion with proportional dimensions should not fire WEIGHT-001."""
        # cushion factor = 0.00815; 6.5×6.5×4.0×0.00815 ≈ 1.376ct
        # stated 1.05ct → WR = 1.05/1.376 ≈ 0.763 → no SOFT
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "cushion"
        spec["stones"][0]["dimensions_mm"] = {"length": 6.5, "width": 6.5, "depth": 4.0}
        spec["stones"][0]["carat"] = 1.05
        r = check_constraints(spec)
        self.assertNotIn("WEIGHT-001", _soft_ids(r))

    def test_weight001_still_fires_for_heavy_round(self):
        """Round stone with high stated carat still fires WEIGHT-001 (regression check)."""
        # calc = 6.5×6.5×3.9×0.0061 ≈ 1.003ct; 1.20/1.003 ≈ 1.197 → SOFT
        spec = _base_solitaire()
        spec["stones"][0]["carat"] = 1.20
        self.assertIn("WEIGHT-001", _soft_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# WEIGHT-002/003 silent correction factors — must NOT appear in soft_warnings
# ---------------------------------------------------------------------------

class TestWeight002Weight003(unittest.TestCase):
    """WEIGHT-002/003 are silent mass corrections — no user-visible violations."""

    def test_weight002_not_in_soft_warnings(self):
        """Emerald shape triggers WEIGHT-002 (debug log only) — not in soft_warnings."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "emerald"
        r = check_constraints(spec)
        self.assertNotIn("WEIGHT-002", _soft_ids(r))
        self.assertNotIn("WEIGHT-002", _hard_ids(r))
        self.assertNotIn("WEIGHT-002", _advisory_ids(r))

    def test_weight003_not_in_soft_warnings(self):
        """Pear shape triggers WEIGHT-003 (debug log only) — not in soft_warnings."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 5  # avoid PRONG-003 SOFT noise
        r = check_constraints(spec)
        self.assertNotIn("WEIGHT-003", _soft_ids(r))
        self.assertNotIn("WEIGHT-003", _hard_ids(r))
        self.assertNotIn("WEIGHT-003", _advisory_ids(r))


# ---------------------------------------------------------------------------
# SOFT rules — representative spot-checks
# ---------------------------------------------------------------------------

class TestSoftRules(unittest.TestCase):

    def test_band006_top_heavy(self):
        """BAND-006: band_width < 20% stone width → SOFT."""
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"]["width"] = 10.0
        spec["stones"][0]["dimensions_mm"]["length"] = 10.0
        spec["ring"]["band_width_mm"] = 1.9  # 1.9 < 10×0.2=2.0
        self.assertIn("BAND-006", _soft_ids(check_constraints(spec)))

    def test_prong004_princess_soft(self):
        """PRONG-004: princess + prong → SOFT (L-claw preferred)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "princess"
        self.assertIn("PRONG-004", _soft_ids(check_constraints(spec)))

    def test_ring001_hk_soft(self):
        """RING-001: HK size → SOFT."""
        spec = _base_solitaire()
        spec["ring"]["size_standard"] = "HK"
        self.assertIn("RING-001", _soft_ids(check_constraints(spec)))

    def test_ring001_jp_soft(self):
        """RING-001: JP size → SOFT."""
        spec = _base_solitaire()
        spec["ring"]["size_standard"] = "JP"
        self.assertIn("RING-001", _soft_ids(check_constraints(spec)))

    def test_ring001_us_no_soft(self):
        spec = _base_solitaire()
        self.assertNotIn("RING-001", _soft_ids(check_constraints(spec)))

    def test_ring001_soft_does_not_reject(self):
        spec = _base_solitaire()
        spec["ring"]["size_standard"] = "JP"
        r = check_constraints(spec)
        self.assertTrue(r.accepted)

    def test_metal003_platinum_soft(self):
        """METAL-003: Pt alloy → SOFT density note."""
        spec = _base_solitaire()
        spec["metal"]["alloy"] = "Pt950"
        self.assertIn("METAL-003", _soft_ids(check_constraints(spec)))

    def test_weight001_high_wr_soft(self):
        """WEIGHT-001: carat / shape-corrected estimate > 1.10 → SOFT."""
        # round: calc = 6.5×6.5×3.9×0.0061 ≈ 1.003ct; stated 1.20 → WR≈1.197
        spec = _base_solitaire()
        spec["stones"][0]["carat"] = 1.20
        self.assertIn("WEIGHT-001", _soft_ids(check_constraints(spec)))

    def test_stone001_halo_too_large_soft(self):
        """STONE-001: avg halo stone > 40% center → SOFT."""
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["halo_style"] = "single"
        # center 6.5mm; halo 3.0mm > 6.5×0.4=2.6mm
        spec["stones"].append({"stone_id": "halo_001", "role": "halo", "shape": "round",
                               "dimensions_mm": {"length": 3.0, "width": 3.0, "depth": 1.8}})
        self.assertIn("STONE-001", _soft_ids(check_constraints(spec)))

    def test_stone006_halo_cathedral_soft(self):
        """STONE-006: halo + cathedral → SOFT."""
        spec = _base_solitaire()
        spec["job"]["archetype"] = "halo"
        spec["setting"]["halo_style"] = "single"
        spec["ring"]["profile"] = "cathedral"
        spec["ring"]["band_width_mm"] = 2.0
        spec["stones"].append({"stone_id": "halo_001", "role": "halo", "shape": "round",
                               "dimensions_mm": {"length": 1.3, "width": 1.3, "depth": 0.8}})
        self.assertIn("STONE-006", _soft_ids(check_constraints(spec)))


# ---------------------------------------------------------------------------
# ADVISORY rules — representative spot-checks
# ---------------------------------------------------------------------------

class TestAdvisoryRules(unittest.TestCase):

    def test_prong003_pear_advisory(self):
        """PRONG-003: pear + prong → ADVISORY in advisory_notes (V-tip)."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "pear"
        spec["setting"]["prong_count"] = 5  # adequate count
        r = check_constraints(spec)
        self.assertIn("PRONG-003", _advisory_ids(r))

    def test_prong005_emerald_advisory(self):
        """PRONG-005: emerald + prong → ADVISORY."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "emerald"
        self.assertIn("PRONG-005", _advisory_ids(check_constraints(spec)))

    def test_prong008_large_stone_4prong_advisory(self):
        """PRONG-008: stone ≥6.5mm + 4-prong → ADVISORY."""
        spec = _base_solitaire()
        spec["stones"][0]["dimensions_mm"]["width"] = 7.0
        spec["setting"]["prong_count"] = 4
        self.assertIn("PRONG-008", _advisory_ids(check_constraints(spec)))

    def test_stone010_large_side_advisory(self):
        """STONE-010: side > 75% center → ADVISORY."""
        spec = _base_solitaire()
        spec["job"]["archetype"] = "trilogy"
        spec["setting"]["side_stone_layout"] = "left_right_pair"
        spec["ring"]["band_width_mm"] = 2.0
        # center 6.5mm; side 5.5mm > 6.5×0.75=4.875mm
        spec["stones"].extend(_side_stones(2, width=5.5, length=5.5, depth=3.3))
        self.assertIn("STONE-010", _advisory_ids(check_constraints(spec)))

    def test_prong006_not_fired_for_oval(self):
        """PRONG-006 removed in M2 Revision — must NOT fire for oval+prong."""
        spec = _base_solitaire()
        spec["stones"][0]["shape"] = "oval"
        r = check_constraints(spec)
        self.assertNotIn("PRONG-006", _advisory_ids(r))
        self.assertNotIn("PRONG-006", _soft_ids(r))
        self.assertNotIn("PRONG-006", _hard_ids(r))


# ---------------------------------------------------------------------------
# Robustness — check_constraints must never raise
# ---------------------------------------------------------------------------

class TestRobustness(unittest.TestCase):

    def test_empty_spec(self):
        self.assertIsInstance(check_constraints({}), ConstraintResult)

    def test_none_ring_values(self):
        spec = {"ring": {"band_width_mm": None, "band_thickness_mm": None}}
        self.assertIsInstance(check_constraints(spec), ConstraintResult)

    def test_missing_stones_key(self):
        spec = _base_solitaire()
        del spec["stones"]
        self.assertIsInstance(check_constraints(spec), ConstraintResult)

    def test_missing_setting_key(self):
        spec = _base_solitaire()
        del spec["setting"]
        self.assertIsInstance(check_constraints(spec), ConstraintResult)

    def test_missing_center_dims(self):
        spec = _base_solitaire()
        del spec["stones"][0]["dimensions_mm"]
        self.assertIsInstance(check_constraints(spec), ConstraintResult)

    def test_accepted_is_bool(self):
        self.assertIsInstance(check_constraints(_base_solitaire()).accepted, bool)

    def test_all_fields_are_lists(self):
        r = check_constraints(_base_solitaire())
        for attr in ("hard_violations", "soft_warnings", "advisory_notes", "schema_gaps"):
            self.assertIsInstance(getattr(r, attr), list)

    def test_violation_fields_populated(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 4
        r = check_constraints(spec)
        self.assertTrue(r.hard_violations)
        v = r.hard_violations[0]
        self.assertIsInstance(v, ConstraintViolation)
        self.assertTrue(v.rule_id)
        self.assertEqual(v.tier, "HARD")
        self.assertIsInstance(v.parameters, list)
        self.assertTrue(v.message)
        self.assertTrue(v.suggested_fix)

    def test_validate_alias(self):
        r1 = validate(_base_solitaire())
        r2 = check_constraints(_base_solitaire())
        self.assertEqual(r1.accepted, r2.accepted)
        self.assertEqual(len(r1.hard_violations), len(r2.hard_violations))


# ---------------------------------------------------------------------------
# accepted flag contract
# ---------------------------------------------------------------------------

class TestAcceptedFlag(unittest.TestCase):

    def test_no_hard_accepted_true(self):
        r = check_constraints(_base_solitaire())
        self.assertTrue(r.accepted)
        self.assertEqual(r.hard_violations, [])

    def test_with_hard_accepted_false(self):
        spec = _base_solitaire()
        spec["setting"]["center_setting"] = "bezel"
        spec["setting"]["prong_count"] = 4
        r = check_constraints(spec)
        self.assertFalse(r.accepted)
        self.assertTrue(r.hard_violations)

    def test_soft_only_still_accepted(self):
        spec = _base_solitaire()
        spec["ring"]["size_standard"] = "JP"
        r = check_constraints(spec)
        self.assertIn("RING-001", _soft_ids(r))
        self.assertTrue(r.accepted)


class TestRingSizeConversion(unittest.TestCase):
    """US size conversion matches Stuller's published size/diameter table."""

    def test_published_us_size_points(self):
        for size, expected_mm in ((1.0, 12.44), (6.5, 16.91), (15.0, 23.82)):
            with self.subTest(size=size):
                self.assertAlmostEqual(
                    _inner_diameter_mm({"size_standard": "US", "size": size}),
                    expected_mm,
                    places=2,
                )

    def test_non_us_standards_are_not_converted(self):
        self.assertIsNone(_inner_diameter_mm({"size_standard": "JP", "size": 13.0}))
        self.assertIsNone(_inner_diameter_mm({"size_standard": "HK", "size": 14.0}))
