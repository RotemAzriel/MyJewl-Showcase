"""Claude-driven config proposer with live constraint gating (Goal 0002).

The model proposes **mounting only** (center setting, prong count, metal alloy,
band proportions). Stone shape, dimensions, carat, and ring size are confirmed
typed inputs and are never model-controlled. Every candidate — LLM or heuristic
— is assembled into a flat config and gated live through
``config_adapter.flat_to_canonical`` → ``validation.validate_job_spec`` →
``constraints.validate``. The proposer never returns an accepted config that the
constraint engine rejects; rule values live only in the constraint engine.

Live mode uses ``claude-fable-5`` (thinking always on — no ``thinking`` param;
server-side fallback to ``claude-opus-4-8``; ``stop_reason == "refusal"`` is
handled). Without an Anthropic client/credentials the deterministic offline
heuristic runs through the same gate, so the app degrades instead of breaking.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp.pipeline import (  # noqa: E402
    MAX_BAND_WIDTH_MM,
    MIN_BAND_THICKNESS_MM,
    MIN_BAND_WIDTH_MM,
    StoneInputs,
    _default_prong_count,
    _parse_stone_inputs,
)
from jewelry_gaas_foundation import constraints  # noqa: E402
from jewelry_gaas_foundation.config_adapter import flat_to_canonical  # noqa: E402
from jewelry_gaas_foundation.validation import validate_job_spec  # noqa: E402


DEFAULT_MODEL = "claude-fable-5"
FALLBACK_MODEL = "claude-opus-4-8"
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-06-01"
MAX_ROUNDS = 3

SETTING_CHOICES = ("prong", "bezel", "basket")
METAL_ALLOYS = (
    "14k_yellow",
    "14k_white",
    "14k_rose",
    "18k_yellow",
    "18k_white",
    "18k_rose",
    "pt950",
)

# Structured-outputs schema for the mounting proposal. Enum/type constraints
# only — numeric ranges are deliberately absent so the live constraint engine
# stays the sole authority on rule values.
MOUNTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "center_setting": {"type": "string", "enum": list(SETTING_CHOICES)},
        "prong_count": {"type": "integer", "enum": [0, 3, 4, 5, 6, 8]},
        "metal_alloy": {"type": "string", "enum": list(METAL_ALLOYS)},
        "band_width_mm": {"type": "number"},
        "band_thickness_mm": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": [
        "center_setting",
        "prong_count",
        "metal_alloy",
        "band_width_mm",
        "band_thickness_mm",
        "rationale",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the mounting proposer for MyJewl, a ring-only jewelry \
geometry platform. Given a confirmed typed stone spec (shape, dimensions in mm, \
optional carat) plus a US ring size and the customer's intent in prose, propose a \
mounting: center setting style, prong count (0 unless the setting is prong), metal \
alloy, band width, and band thickness in millimeters.

Rules:
- The stone spec and ring size are immutable typed inputs. Never restate or adjust them.
- Propose realistic solitaire mounting values; bands are typically 1.8-4.0 mm wide and \
at least 1.5 mm thick, but an authoritative constraint engine — not you — decides \
acceptance. If your proposal is rejected you will receive the violated rule IDs and \
messages; correct the proposal accordingly.
- Respect the customer's intent where the constraints allow it, and explain your \
choice briefly in `rationale`."""


class ProposalFormatError(ValueError):
    """A candidate mounting did not conform to the expected shape."""


class ProposerRefusal(RuntimeError):
    """The model declined the request (stop_reason == "refusal")."""


@dataclass(frozen=True)
class Mounting:
    center_setting: str
    prong_count: int
    metal_alloy: str
    band_width_mm: float
    band_thickness_mm: float
    rationale: str | None = None


@dataclass(frozen=True)
class GateResult:
    flat: dict[str, Any]
    canonical: dict[str, Any]
    schema_accepted: bool
    constraint_result: constraints.ConstraintResult

    @property
    def accepted(self) -> bool:
        return self.schema_accepted and self.constraint_result.accepted


def parse_mounting(raw: Any) -> Mounting:
    """Validate the shape of a candidate mounting (model output is a boundary)."""
    if not isinstance(raw, dict):
        raise ProposalFormatError("mounting proposal must be a JSON object")
    setting = str(raw.get("center_setting", "")).strip().lower()
    if setting not in SETTING_CHOICES:
        raise ProposalFormatError(
            f"center_setting must be one of {', '.join(SETTING_CHOICES)}"
        )
    alloy = str(raw.get("metal_alloy", "")).strip().lower().replace(" ", "_")
    if alloy not in METAL_ALLOYS:
        raise ProposalFormatError(
            f"metal_alloy must be one of {', '.join(METAL_ALLOYS)}"
        )
    try:
        prongs = int(raw.get("prong_count"))
        width = float(raw.get("band_width_mm"))
        thickness = float(raw.get("band_thickness_mm"))
    except (TypeError, ValueError) as exc:
        raise ProposalFormatError(
            "prong_count, band_width_mm, and band_thickness_mm must be numbers"
        ) from exc
    # No prong/setting normalization here: the live constraint engine (e.g.
    # PRONG-009 for bezel + prongs) is the sole authority on such rules.
    rationale = raw.get("rationale")
    return Mounting(
        center_setting=setting,
        prong_count=prongs,
        metal_alloy=alloy,
        band_width_mm=width,
        band_thickness_mm=thickness,
        rationale=str(rationale) if rationale else None,
    )


def assemble_flat(stone: StoneInputs, mounting: Mounting) -> dict[str, Any]:
    """Build the flat parameters.json-shaped config from typed stone + mounting."""
    metal_type = "platinum" if mounting.metal_alloy.startswith("pt") else "gold"
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    config_id = (
        f"prop_{stamp}_{stone.shape}_{stone.length_mm:.2f}x{stone.width_mm:.2f}"
        f"_{mounting.center_setting}{mounting.prong_count}"
        f"_{mounting.metal_alloy}_us{stone.ring_size_us:g}"
    ).replace(".", "p")
    flat: dict[str, Any] = {
        "config_id": config_id,
        "archetype": "solitaire",
        "stone_shape": stone.shape,
        "stone_length_mm": round(stone.length_mm, 2),
        "stone_width_mm": round(stone.width_mm, 2),
        "stone_depth_mm": round(stone.depth_mm, 2),
        "stone_color": "diamond_colorless",
        "center_setting": mounting.center_setting,
        "prong_count": mounting.prong_count,
        "metal_type": metal_type,
        "metal_alloy": mounting.metal_alloy,
        "band_width_mm": round(mounting.band_width_mm, 2),
        "band_thickness_mm": round(mounting.band_thickness_mm, 2),
        "ring_size_us": stone.ring_size_us,
        "geometry_gates": {
            "minimum_structural_floors": "pending",
            "stone_metal_collision": "pending",
            "stone_stone_collision": "pending",
            "mountability_and_setting_access": "pending",
            "mesh_closedness_and_manifold_suitability": "pending",
        },
    }
    if stone.carat_eq is not None:
        flat["stone_carat_eq"] = round(stone.carat_eq, 2)
    return flat


def gate(stone: StoneInputs, mounting: Mounting) -> GateResult:
    """Run a candidate through the live schema + constraint gate."""
    flat = assemble_flat(stone, mounting)
    canonical = flat_to_canonical(flat)
    schema_result = validate_job_spec(canonical)
    constraint_result = constraints.validate(canonical)
    return GateResult(
        flat=flat,
        canonical=canonical,
        schema_accepted=schema_result.accepted,
        constraint_result=constraint_result,
    )


def heuristic_mounting(stone: StoneInputs, intent: str) -> Mounting:
    """Deterministic offline proposal from intent keywords, same gate applies."""
    lowered = (intent or "").lower()
    if "bezel" in lowered:
        setting = "bezel"
    elif "basket" in lowered:
        setting = "basket"
    else:
        setting = "prong"
    if "platinum" in lowered:
        alloy = "pt950"
    elif "rose" in lowered or "pink" in lowered:
        alloy = "14k_rose"
    elif "white" in lowered or "silver" in lowered:
        alloy = "14k_white"
    elif "18k" in lowered:
        alloy = "18k_yellow"
    else:
        alloy = "14k_yellow"
    if any(word in lowered for word in ("delicate", "thin", "dainty", "minimal")):
        width = MIN_BAND_WIDTH_MM
    elif any(word in lowered for word in ("bold", "wide", "chunky", "statement")):
        width = min(MAX_BAND_WIDTH_MM, 3.0)
    else:
        width = 2.0
    prongs = _default_prong_count(stone.shape, stone.width_mm) if setting == "prong" else 0
    if setting == "prong" and any(k in lowered for k in ("six", "6 prong", "6-prong")):
        prongs = 6
    return Mounting(
        center_setting=setting,
        prong_count=prongs,
        metal_alloy=alloy,
        band_width_mm=width,
        band_thickness_mm=max(MIN_BAND_THICKNESS_MM, 1.6),
        rationale="offline heuristic from intent keywords",
    )


def _default_client() -> Any | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001 - missing credentials → offline mode
        return None


def _user_message(stone: StoneInputs, intent: str) -> str:
    payload = {
        "stone": {
            "shape": stone.shape,
            "length_mm": stone.length_mm,
            "width_mm": stone.width_mm,
            "depth_mm": stone.depth_mm,
            "carat_eq": stone.carat_eq,
        },
        "ring_size_us": stone.ring_size_us,
        "intent": intent,
    }
    return json.dumps(payload, sort_keys=True)


def _extract_json(response: Any) -> dict[str, Any]:
    if getattr(response, "stop_reason", None) == "refusal":
        raise ProposerRefusal("model declined the proposal request")
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalFormatError("model output was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProposalFormatError("model output must be a JSON object")
    return parsed


def _llm_propose(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "output_config": {"format": {"type": "json_schema", "schema": MOUNTING_SCHEMA}},
        "messages": messages,
    }
    if model == DEFAULT_MODEL:
        # Fable 5: thinking always on (no `thinking` param) + server-side fallback.
        response = client.beta.messages.create(
            betas=[SERVER_SIDE_FALLBACK_BETA],
            fallbacks=[{"model": FALLBACK_MODEL}],
            **kwargs,
        )
    else:
        response = client.messages.create(**kwargs)
    return _extract_json(response)


def _violation_feedback(result: GateResult, format_error: str | None) -> str:
    if format_error is not None:
        return json.dumps({"format_error": format_error})
    issues = [
        {"rule_id": issue.rule_id, "message": issue.message}
        for issue in result.constraint_result.hard_violations
    ]
    return json.dumps(
        {
            "rejected": True,
            "schema_accepted": result.schema_accepted,
            "hard_violations": issues,
            "instruction": "Propose a corrected mounting that resolves every violation.",
        },
        sort_keys=True,
    )


def _gate_report(result: GateResult) -> dict[str, Any]:
    return {
        "schema_accepted": result.schema_accepted,
        "constraint_accepted": result.constraint_result.accepted,
        "hard_violations": [
            {"rule_id": issue.rule_id, "message": issue.message}
            for issue in result.constraint_result.hard_violations
        ],
        "warnings": [
            {"rule_id": issue.rule_id, "tier": issue.tier, "message": issue.message}
            for issue in (
                result.constraint_result.soft_warnings
                + result.constraint_result.advisory_notes
            )
        ][:12],
        "schema_gaps": result.constraint_result.schema_gaps[:8],
    }


def _accepted_report(
    result: GateResult,
    provider: str,
    rounds: int,
    rationale: str | None,
    fallback_reason: str | None,
    rejected_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "accepted",
        "provider": provider,
        "rounds": rounds,
        "fallback_reason": fallback_reason,
        "rationale": rationale,
        "flat_parameters": result.flat,
        "canonical_spec": result.canonical,
        "validation": _gate_report(result),
        "rejected_candidates": rejected_candidates,
    }


def propose_config(
    payload: dict[str, Any],
    *,
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
    max_rounds: int = MAX_ROUNDS,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Propose a validated flat config from typed stone inputs + intent.

    Returns a report whose ``status`` is ``accepted`` (with ``flat_parameters``
    and ``canonical_spec`` that passed the live gate) or ``rejected`` (violation
    rule IDs only — a rejected config is never emitted).
    """
    stone = _parse_stone_inputs(payload)
    intent = str(payload.get("intent") or "").strip()

    if client is None and allow_llm:
        client = _default_client()

    rejected_candidates: list[dict[str, Any]] = []
    fallback_reason: str | None = None

    if client is not None and allow_llm:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _user_message(stone, intent)}
        ]
        last_result: GateResult | None = None
        for round_index in range(1, max_rounds + 1):
            format_error: str | None = None
            try:
                raw = _llm_propose(client, model, messages)
                mounting = parse_mounting(raw)
            except ProposalFormatError as exc:
                format_error = str(exc)
                mounting = None
            except ProposerRefusal as exc:
                fallback_reason = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - external boundary
                fallback_reason = f"{exc.__class__.__name__}: {exc}"
                break

            if mounting is not None:
                result = gate(stone, mounting)
                if result.accepted:
                    return _accepted_report(
                        result,
                        provider=model,
                        rounds=round_index,
                        rationale=mounting.rationale,
                        fallback_reason=None,
                        rejected_candidates=rejected_candidates,
                    )
                last_result = result
                rejected_candidates.append(
                    {
                        "mounting": {
                            "center_setting": mounting.center_setting,
                            "prong_count": mounting.prong_count,
                            "metal_alloy": mounting.metal_alloy,
                            "band_width_mm": mounting.band_width_mm,
                            "band_thickness_mm": mounting.band_thickness_mm,
                        },
                        "hard_violations": [
                            issue.rule_id
                            for issue in result.constraint_result.hard_violations
                        ],
                    }
                )
            feedback = _violation_feedback(last_result, format_error) if (
                last_result is not None or format_error is not None
            ) else json.dumps({"format_error": "no proposal received"})
            messages.append({"role": "assistant", "content": json.dumps(raw) if mounting else "{}"})
            messages.append({"role": "user", "content": feedback})
        else:
            # LLM rounds exhausted without an accepted config.
            return {
                "status": "rejected",
                "provider": model,
                "rounds": max_rounds,
                "fallback_reason": None,
                "rationale": None,
                "flat_parameters": None,
                "canonical_spec": None,
                "validation": _gate_report(last_result) if last_result else None,
                "rejected_candidates": rejected_candidates,
            }

    # Offline / fallback path: deterministic heuristic through the same gate.
    mounting = heuristic_mounting(stone, intent)
    result = gate(stone, mounting)
    provider = "offline_heuristic"
    if result.accepted:
        return _accepted_report(
            result,
            provider=provider,
            rounds=1,
            rationale=mounting.rationale,
            fallback_reason=fallback_reason,
            rejected_candidates=rejected_candidates,
        )
    return {
        "status": "rejected",
        "provider": provider,
        "rounds": 1,
        "fallback_reason": fallback_reason,
        "rationale": None,
        "flat_parameters": None,
        "canonical_spec": None,
        "validation": _gate_report(result),
        "rejected_candidates": rejected_candidates
        + [
            {
                "mounting": {
                    "center_setting": mounting.center_setting,
                    "prong_count": mounting.prong_count,
                    "metal_alloy": mounting.metal_alloy,
                    "band_width_mm": mounting.band_width_mm,
                    "band_thickness_mm": mounting.band_thickness_mm,
                },
                "hard_violations": [
                    issue.rule_id
                    for issue in result.constraint_result.hard_violations
                ],
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Propose a validated MyJewl ring config from typed inputs + intent."
    )
    parser.add_argument("--intent", required=True, help="Customer intent in prose.")
    parser.add_argument("--stone-shape", required=True)
    parser.add_argument("--stone-length-mm", type=float, required=True)
    parser.add_argument("--stone-width-mm", type=float, required=True)
    parser.add_argument("--stone-depth-mm", type=float, required=True)
    parser.add_argument("--ring-size-us", type=float, required=True)
    parser.add_argument("--stone-carat-eq", type=float, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the LLM and use the deterministic heuristic proposer.",
    )
    parser.add_argument("--output", default=None, help="Write the report JSON here.")
    args = parser.parse_args(argv)

    payload = {
        "intent": args.intent,
        "stone_shape": args.stone_shape,
        "stone_length_mm": args.stone_length_mm,
        "stone_width_mm": args.stone_width_mm,
        "stone_depth_mm": args.stone_depth_mm,
        "ring_size_us": args.ring_size_us,
        "stone_carat_eq": args.stone_carat_eq,
    }
    report = propose_config(payload, model=args.model, allow_llm=not args.offline)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
