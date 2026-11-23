"""Zero-shot Claude-vision mounting extractor (Goal 0005, Stage 0 / plan §4).

Lane B's cheapest rung: a photo plus the **typed** stone spec, ring size, and
metal in, a mounting-only proposal out, gated live through
``flat_to_canonical -> validate_job_spec -> constraints.validate``. This is the
baseline that retrieval must beat (gate G-A) and doubles as the later
explanation layer.

Invariants (plan §"Invariants", CLAUDE.md, product contract 2026-07-27):
* Stone shape, L/W/D, carat, ring size, **and metal (colour + karat/purity)**
  are typed inputs. The model never sees a request to infer them and its output
  schema has no field for them.
* The model reports band width and band thickness as **ratios of the stone
  width**, not in millimetres — absolute geometry comes from the typed spec,
  never from pixels.
* Unsupported archetypes and non-rings **abstain**; they are never forced onto
  the nearest catalog mounting.
* Rule values live only in the constraint engine. This module has no thresholds.

Bulk extraction runs on ``claude-opus-4-8`` (owner credit is limited; Fable is
2x the price and this is a high-volume, low-difficulty vision call). Without
credentials the deterministic offline extractor runs through the same gate, so
CI and dev never need the network.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_mvp.pipeline import (  # noqa: E402
    StoneInputs,
    _default_prong_count,
    _parse_stone_inputs,
)
from fast_mvp.proposer import (  # noqa: E402
    SETTING_CHOICES,
    GateResult,
    Mounting,
    ProposalFormatError,
    ProposerRefusal,
    gate,
)
from fast_mvp.typed_inputs import (  # noqa: E402
    MetalSpec,
    apply_certificate,
    parse_metal_spec,
    stone_spec_source,
)

# Bulk vision extraction model. Opus 4.8 (not Fable 5) is a deliberate cost
# choice recorded in the Goal 0005 plan §7; thinking stays off (omitted, which
# on Opus 4.8 means no thinking) because this is a perception call, not a
# reasoning one.
DEFAULT_VISION_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "medium"
MAX_REPAIR_ROUNDS = 2

SUPPORTED_ARCHETYPES = ("solitaire",)
ARCHETYPE_CHOICES = (
    "solitaire",
    "halo",
    "trilogy",
    "eternity",
    "other_ring",
    "not_a_ring",
)

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Mounting-only observation schema. Note what is absent: no stone shape, no
# millimetre dimensions, no carat, no ring size, **no metal**. Metal is a typed
# input (colour + karat/purity) and a cost/appearance overlay, so the model is
# never asked to read it and has no field to report it in. Confidences are the
# model's own calibration signal and are combined with retrieval signals in
# ``image_lane`` — they are never treated as authoritative on their own.
VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string", "enum": list(ARCHETYPE_CHOICES)},
        "center_setting": {"type": "string", "enum": list(SETTING_CHOICES)},
        "center_setting_confidence": {"type": "number"},
        "prong_count": {"type": "integer", "enum": [0, 3, 4, 5, 6, 8]},
        "prong_count_visible": {"type": "boolean"},
        "prong_count_confidence": {"type": "number"},
        "band_width_ratio": {"type": "number"},
        "band_width_ratio_confidence": {"type": "number"},
        "band_thickness_ratio": {"type": "number"},
        "band_thickness_ratio_confidence": {"type": "number"},
        "observations": {"type": "string"},
    },
    "required": [
        "archetype",
        "center_setting",
        "center_setting_confidence",
        "prong_count",
        "prong_count_visible",
        "prong_count_confidence",
        "band_width_ratio",
        "band_width_ratio_confidence",
        "band_thickness_ratio",
        "band_thickness_ratio_confidence",
        "observations",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the mounting observer for MyJewl, a ring-only jewelry \
geometry platform. You are shown one photograph of a ring and told the stone \
specification, ring size, and metal, which are already confirmed by the customer.

Report ONLY what the mounting looks like:
- `archetype`: what kind of ring this is. Use `solitaire` for a single centre \
stone; `halo`, `trilogy`, or `eternity` when you see those; `other_ring` for a \
ring that is none of these; `not_a_ring` when the image is not a ring.
- `center_setting`: prong, bezel, or basket.
- `prong_count`: how many prongs grip the centre stone; 0 for a bezel. Set \
`prong_count_visible` to false when the viewing angle hides prongs (for example \
a pure side profile) and you are inferring rather than counting — still give \
your best estimate.
- `band_width_ratio`: the band's width divided by the centre stone's width, \
measured across the same photograph. This is a dimensionless ratio — never \
report millimetres. A typical solitaire band is a fraction of the stone width.
- `band_thickness_ratio`: the band's thickness (its depth from the finger \
outward, seen edge-on) divided by the same centre stone's width. Also \
dimensionless; estimate it from the visible edge of the band.

Never infer or restate stone shape, stone dimensions, carat weight, ring size, \
or metal colour and karat: those are typed inputs you must ignore even if the \
photo contradicts them. Metal is appearance and pricing only and plays no part \
in identifying the mounting.

Give each confidence as 0.0-1.0 reflecting how clearly the photograph supports \
that reading. Be honest: a low confidence is more useful than a confident \
guess, because low-confidence attributes are resolved by asking the customer."""


class ImageDecodeError(ValueError):
    """The supplied image could not be read."""


@dataclass(frozen=True)
class VisionObservation:
    """Mounting-only observation, before any gating.

    These four fields *are* the image lane's output contract: the mounting match,
    band-width ratio, band-thickness ratio, centre setting, and prong count when
    visible. Metal is deliberately not among them.
    """

    archetype: str
    center_setting: str
    prong_count: int
    prong_count_visible: bool
    band_width_ratio: float
    band_thickness_ratio: float
    confidences: dict[str, float] = field(default_factory=dict)
    observations: str | None = None

    @property
    def supported_archetype(self) -> bool:
        return self.archetype in SUPPORTED_ARCHETYPES

    def to_json(self) -> dict[str, Any]:
        return {
            "archetype": self.archetype,
            "center_setting": self.center_setting,
            "prong_count": self.prong_count,
            "prong_count_visible": self.prong_count_visible,
            "band_width_ratio": round(self.band_width_ratio, 4),
            "band_thickness_ratio": round(self.band_thickness_ratio, 4),
            "confidences": {k: round(v, 3) for k, v in sorted(self.confidences.items())},
            "observations": self.observations,
        }


def _clamp_unit(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def parse_observation(raw: Any) -> VisionObservation:
    """Validate the shape of a vision response (model output is a boundary)."""
    if not isinstance(raw, dict):
        raise ProposalFormatError("vision observation must be a JSON object")

    archetype = str(raw.get("archetype", "")).strip().lower()
    if archetype not in ARCHETYPE_CHOICES:
        raise ProposalFormatError(
            f"archetype must be one of {', '.join(ARCHETYPE_CHOICES)}"
        )
    setting = str(raw.get("center_setting", "")).strip().lower()
    if setting not in SETTING_CHOICES:
        raise ProposalFormatError(
            f"center_setting must be one of {', '.join(SETTING_CHOICES)}"
        )
    if "metal_color_family" in raw:
        raise ProposalFormatError(
            "metal is a typed input (colour + karat/purity); the image lane must "
            "not report it"
        )
    try:
        prongs = int(raw.get("prong_count"))
        ratio = float(raw.get("band_width_ratio"))
        thickness_ratio = float(raw.get("band_thickness_ratio"))
    except (TypeError, ValueError) as exc:
        raise ProposalFormatError(
            "prong_count, band_width_ratio, and band_thickness_ratio must be numbers"
        ) from exc
    if not ratio > 0.0:
        raise ProposalFormatError("band_width_ratio must be positive")
    if not thickness_ratio > 0.0:
        raise ProposalFormatError("band_thickness_ratio must be positive")

    confidences = {
        "center_setting": _clamp_unit(raw.get("center_setting_confidence")),
        "prong_count": _clamp_unit(raw.get("prong_count_confidence")),
        "band_width_ratio": _clamp_unit(raw.get("band_width_ratio_confidence")),
        "band_thickness_ratio": _clamp_unit(raw.get("band_thickness_ratio_confidence")),
    }
    notes = raw.get("observations")
    return VisionObservation(
        archetype=archetype,
        center_setting=setting,
        prong_count=prongs,
        prong_count_visible=bool(raw.get("prong_count_visible", True)),
        band_width_ratio=ratio,
        band_thickness_ratio=thickness_ratio,
        confidences=confidences,
        observations=str(notes) if notes else None,
    )


def observation_to_mounting(
    observation: VisionObservation,
    stone: StoneInputs,
    metal: MetalSpec,
) -> Mounting:
    """Turn a dimensionless observation plus the typed metal into a mounting.

    Band width and thickness become millimetres only here, by scaling the
    observed ratios by the **typed** stone width — pixels set proportion, the
    typed spec sets scale. The alloy comes from the typed metal, never from the
    photograph. No clamping happens: the live constraint engine, not this module,
    decides whether the resulting mounting is acceptable.
    """
    return Mounting(
        center_setting=observation.center_setting,
        prong_count=observation.prong_count,
        metal_alloy=metal.alloy,
        band_width_mm=observation.band_width_ratio * stone.width_mm,
        band_thickness_mm=observation.band_thickness_ratio * stone.width_mm,
        rationale=observation.observations,
    )


def load_image(path: str | Path) -> tuple[bytes, str]:
    """Read an image and resolve its media type for the vision API."""
    resolved = Path(path)
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ImageDecodeError(f"could not read image: {resolved}") from exc
    media_type = MEDIA_TYPES.get(resolved.suffix.lower())
    if media_type is None:
        raise ImageDecodeError(
            f"unsupported image type '{resolved.suffix}' for {resolved}"
        )
    return raw, media_type


def _image_block(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        },
    }


def build_user_content(
    image_bytes: bytes,
    media_type: str,
    stone: StoneInputs,
    metal: MetalSpec | None = None,
) -> list[dict[str, Any]]:
    """Vision message content: the photo plus the typed spec as read-only context."""
    typed = {
        "stone_shape": stone.shape,
        "stone_length_mm": stone.length_mm,
        "stone_width_mm": stone.width_mm,
        "stone_depth_mm": stone.depth_mm,
        "stone_carat_eq": stone.carat_eq,
        "ring_size_us": stone.ring_size_us,
    }
    if metal is not None:
        typed["metal_color_family"] = metal.color_family
        typed["metal_purity"] = metal.purity
    return [
        _image_block(image_bytes, media_type),
        {
            "type": "text",
            "text": (
                "Confirmed typed inputs (do not infer or restate these):\n"
                + json.dumps(typed, sort_keys=True)
                + "\n\nReport the mounting you can see."
            ),
        },
    ]


def _default_client() -> Any | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001 - missing credentials -> offline mode
        return None


def build_request_params(
    image_bytes: bytes,
    media_type: str,
    stone: StoneInputs,
    metal: MetalSpec | None = None,
    *,
    model: str = DEFAULT_VISION_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Assemble the Messages-API params (shared by live and Batch-API paths)."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "output_config": {
            "format": {"type": "json_schema", "schema": VISION_SCHEMA},
            "effort": effort,
        },
        "messages": [
            {
                "role": "user",
                "content": build_user_content(image_bytes, media_type, stone, metal),
            }
        ],
    }


def _extract_json(response: Any) -> dict[str, Any]:
    if getattr(response, "stop_reason", None) == "refusal":
        raise ProposerRefusal("model declined the image extraction request")
    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalFormatError("vision output was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProposalFormatError("vision output must be a JSON object")
    return parsed


def heuristic_observation(
    image_bytes: bytes | None,
    stone: StoneInputs,
) -> VisionObservation:
    """Deterministic offline observation — catalog priors only, no pixel reading.

    This is a *floor*, not a model: the setting/prong/band priors come from the
    typed shape, not from the photograph. It exists so the whole lane runs with
    no credentials, and every report it produces is labelled ``offline_heuristic``
    so its numbers are never mistaken for G-A numbers. It deliberately reads
    nothing from the image — including metal colour, which is a typed input.
    """
    del image_bytes  # the floor is a prior over the typed shape, not a pixel read
    return VisionObservation(
        archetype="solitaire",
        center_setting="prong",
        prong_count=_default_prong_count(stone.shape, stone.width_mm),
        prong_count_visible=False,
        band_width_ratio=2.0 / stone.width_mm,
        band_thickness_ratio=1.6 / stone.width_mm,
        confidences={
            "center_setting": 0.35,
            "prong_count": 0.30,
            "band_width_ratio": 0.25,
            "band_thickness_ratio": 0.20,
        },
        observations="offline heuristic: shape prior only (no pixels read)",
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


def extract_mounting(
    payload: dict[str, Any],
    *,
    image_bytes: bytes | None = None,
    media_type: str = "image/png",
    client: Any | None = None,
    model: str = DEFAULT_VISION_MODEL,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Extract a mounting from one photo and run it through the live gate.

    Returns a report whose ``status`` is one of:

    ``accepted``  — the observation passed schema + constraints.
    ``abstained`` — the image is not a supported solitaire ring (never forced
                    onto the nearest catalog mounting).
    ``rejected``  — a supported ring was seen but the mounting violates a HARD
                    contract rule; violation IDs only, no config emitted.
    """
    payload, certificate = apply_certificate(payload)
    stone = _parse_stone_inputs(payload)
    metal = parse_metal_spec(payload)
    if image_bytes is None:
        image_path = payload.get("image_path")
        if image_path:
            image_bytes, media_type = load_image(image_path)

    provider = "offline_heuristic"
    fallback_reason: str | None = None
    observation: VisionObservation | None = None

    if client is None and allow_llm:
        client = _default_client()

    if client is not None and allow_llm and image_bytes is not None:
        params = build_request_params(image_bytes, media_type, stone, metal, model=model)
        for _ in range(MAX_REPAIR_ROUNDS):
            try:
                response = client.messages.create(**params)
                observation = parse_observation(_extract_json(response))
                provider = model
                break
            except ProposalFormatError as exc:
                fallback_reason = f"format_error: {exc}"
                continue
            except ProposerRefusal as exc:
                fallback_reason = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - external boundary
                fallback_reason = f"{exc.__class__.__name__}: {exc}"
                break

    if observation is None:
        observation = heuristic_observation(image_bytes, stone)
        provider = "offline_heuristic"

    base: dict[str, Any] = {
        "provider": provider,
        "model": model if provider == model else None,
        "fallback_reason": fallback_reason,
        "observation": observation.to_json(),
        "typed_inputs": {
            "stone_shape": stone.shape,
            "stone_length_mm": stone.length_mm,
            "stone_width_mm": stone.width_mm,
            "stone_depth_mm": stone.depth_mm,
            "stone_carat_eq": stone.carat_eq,
            "ring_size_us": stone.ring_size_us,
            "stone_spec_source": stone_spec_source(certificate),
            "certificate": certificate.to_json() if certificate else None,
            "metal": metal.to_json(),
        },
    }

    if not observation.supported_archetype:
        return {
            **base,
            "status": "abstained",
            "abstention_reason": (
                "not_a_ring"
                if observation.archetype == "not_a_ring"
                else "unsupported_archetype"
            ),
            "abstention_detail": (
                f"Observed archetype '{observation.archetype}'. MyJewl's pilot "
                "catalog covers solitaire mountings only; this design is not "
                "matched to the nearest catalog entry."
            ),
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": None,
        }

    mounting = observation_to_mounting(observation, stone, metal)
    result = gate(stone, mounting)
    if not result.accepted:
        return {
            **base,
            "status": "rejected",
            "flat_parameters": None,
            "canonical_spec": None,
            "validation": _gate_report(result),
        }
    return {
        **base,
        "status": "accepted",
        "flat_parameters": result.flat,
        "canonical_spec": result.canonical,
        "validation": _gate_report(result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract a validated mounting from a ring photo (Stage 0)."
    )
    parser.add_argument("image", help="Path to the ring photograph.")
    parser.add_argument("--stone-shape", required=True)
    parser.add_argument("--stone-length-mm", type=float, required=True)
    parser.add_argument("--stone-width-mm", type=float, required=True)
    parser.add_argument("--stone-depth-mm", type=float, required=True)
    parser.add_argument("--ring-size-us", type=float, required=True)
    parser.add_argument("--stone-carat-eq", type=float, default=None)
    parser.add_argument(
        "--metal-color",
        default=None,
        help="Typed metal colour family: yellow | white | rose (never read from the photo).",
    )
    parser.add_argument(
        "--metal-karat",
        default=None,
        help="Typed karat/purity: 14k | 18k | pt950.",
    )
    parser.add_argument("--model", default=DEFAULT_VISION_MODEL)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the Anthropic call and use the deterministic extractor.",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    image_bytes, media_type = load_image(args.image)
    report = extract_mounting(
        {
            "stone_shape": args.stone_shape,
            "stone_length_mm": args.stone_length_mm,
            "stone_width_mm": args.stone_width_mm,
            "stone_depth_mm": args.stone_depth_mm,
            "ring_size_us": args.ring_size_us,
            "stone_carat_eq": args.stone_carat_eq,
            "metal_color_family": args.metal_color,
            "metal_karat": args.metal_karat,
        },
        image_bytes=image_bytes,
        media_type=media_type,
        model=args.model,
        allow_llm=not args.offline,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
