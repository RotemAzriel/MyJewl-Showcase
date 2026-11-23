"""Photo-to-spec pipeline with mounting-only ML retrieval.

Stone shape, stone dimensions, ring size, **and metal (colour + karat/purity)**
are confirmed typed inputs. Image analysis is restricted to mounting inference
and preprocessing for retrieval — it never reads metal from pixels, a filename,
or a retrieved neighbour, because metal is a cost/appearance overlay that must
not influence geometry matching (settled decision 6).
"""

from __future__ import annotations

import base64
import binascii
import io
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from fast_mvp import perception_lane
from fast_mvp.preprocessing import PreprocessedImage, preprocess_for_retrieval
from fast_mvp.retrieval import (
    DINOV3_MODEL_ID,
    MountingCandidate,
    MountingProvider,
    RunPodMountingProvider,
)
from fast_mvp.typed_inputs import (
    MetalSpec,
    apply_certificate,
    parse_metal_spec,
    preview_metal,
    quote_basis,
    stone_spec_source,
)
from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.validation import validate_job_spec


STONE_SHAPES = (
    "round",
    "oval",
    "cushion",
    "pear",
    "emerald",
    "radiant",
    "princess",
)
SETTING_CHOICES = ("prong", "bezel", "basket")
METAL_FAMILIES = ("yellow", "white", "rose")
MIN_BAND_WIDTH_MM = 1.8
MAX_BAND_WIDTH_MM = 4.0
MIN_BAND_THICKNESS_MM = 1.5


@dataclass(frozen=True)
class ImageMetrics:
    width: int
    height: int
    warm_score: float
    rose_score: float
    white_score: float


@dataclass(frozen=True)
class StoneInputs:
    shape: str
    length_mm: float
    width_mm: float
    depth_mm: float
    ring_size_us: float
    carat_eq: float | None


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: MountingCandidate
    flat: dict[str, Any]
    canonical: dict[str, Any]
    schema_accepted: bool
    constraint_result: constraints.ConstraintResult


def decode_image_data_url(value: str | None) -> bytes | None:
    """Decode a browser data URL into bytes."""
    if not value:
        return None
    if "," not in value:
        raise ValueError("image_data_url must be a browser data URL.")
    header, encoded = value.split(",", 1)
    if "base64" not in header:
        raise ValueError("image_data_url must be base64 encoded.")
    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ValueError("image_data_url contains invalid base64.") from exc


def analyze_photo_request(
    payload: dict[str, Any],
    *,
    mounting_provider: MountingProvider | None = None,
) -> dict[str, Any]:
    """Return a canonical spec and extraction report from an upload payload."""
    if not isinstance(payload, dict):
        raise ValueError("Extraction payload must be a JSON object.")

    payload, certificate = apply_certificate(payload)
    stone = _parse_stone_inputs(payload)
    metal = parse_metal_spec(payload)
    filename = _safe_filename(_str_or_none(payload.get("filename")) or "uploaded-ring")
    image_bytes = decode_image_data_url(_str_or_none(payload.get("image_data_url")))
    metrics = _extract_image_metrics(image_bytes) if image_bytes else None
    preprocessed = preprocess_for_retrieval(image_bytes) if image_bytes else None
    overrides = _parse_mounting_overrides(payload)

    provider_name = "heuristic_fallback"
    model_name: str | None = None
    fallback_reason: str | None = None
    perception_refusal: dict[str, Any] | None = None
    raw_candidates: tuple[MountingCandidate, ...]

    if overrides:
        provider_name = "typed"
        raw_candidates = (_typed_candidate(overrides, stone, metal),)
    elif preprocessed is None:
        fallback_reason = "No image was supplied; mounting defaults were used."
        raw_candidates = (_heuristic_candidate(stone, metal),)
    else:
        # Goal 0026. This is the perception lane's *second* door: with RunPod
        # credentials in the environment, an uploaded photo is matched by the same
        # paused Track C paradigm that ``/api/propose-from-image`` serves, and this
        # is the door /studio's upload path actually uses. It is gated by the same
        # acknowledgement. An explicitly injected provider is left alone: that is an
        # in-process decision by a caller who already has the object, not something
        # reachable over HTTP.
        gate = perception_lane.decide() if mounting_provider is None else None
        if gate is not None and not gate.available:
            perception_refusal = gate.to_json()
            provider_name = "perception_refused_typed_prior"
            fallback_reason = (
                f"Perception lane refused ({gate.reason}): {gate.detail} Mounting "
                "fell back to the typed-shape prior, which reads nothing from the "
                "photograph."
            )
            raw_candidates = (_heuristic_candidate(stone, metal),)
        else:
            provider = mounting_provider or RunPodMountingProvider()
            try:
                response = provider.query(
                    preprocessed,
                    stone_shape=stone.shape,
                    top_k=5,
                )
                raw_candidates = response.candidates
                provider_name = "dinov3_hnsw"
                model_name = response.model
                if gate is not None:
                    perception_refusal = gate.to_json()
            except Exception as exc:  # noqa: BLE001 - provider is an external boundary
                fallback_reason = str(exc)
                raw_candidates = (_heuristic_candidate(stone, metal),)

    evaluations = [
        _evaluate_candidate(candidate, stone, metal)
        for candidate in raw_candidates
    ]
    selected = next(
        (
            evaluation
            for evaluation in evaluations
            if evaluation.schema_accepted
            and evaluation.constraint_result.accepted
        ),
        None,
    )
    if selected is None and provider_name == "dinov3_hnsw":
        provider_name = "heuristic_fallback"
        fallback_reason = "All retrieved candidates failed schema or hard constraints."
        fallback = _evaluate_candidate(
            _heuristic_candidate(stone, metal),
            stone,
            metal,
        )
        evaluations.append(fallback)
        selected = fallback
    elif selected is None:
        selected = evaluations[0]

    flat = selected.flat
    canonical = selected.canonical
    canonical["metadata"] = {
        "source": "sketch_prefill",
        "notes": (
            "fast_mvp mounting-only extraction; stone geometry and ring size "
            "are confirmed typed inputs"
        ),
    }
    schema_validation = validate_job_spec(canonical)
    constraint_result = constraints.validate(canonical)
    warnings = [
        {"rule_id": issue.rule_id, "tier": issue.tier, "message": issue.message}
        for issue in constraint_result.soft_warnings + constraint_result.advisory_notes
    ]

    selected_candidate = selected.candidate
    similarity = selected_candidate.similarity
    compatibility_score = (
        _bounded_similarity(similarity)
        if similarity is not None
        else (1.0 if provider_name == "typed" else 0.58)
    )
    dimensions = {
        "length": round(stone.length_mm, 2),
        "width": round(stone.width_mm, 2),
        "depth": round(stone.depth_mm, 2),
    }

    return {
        "job_id": flat["config_id"],
        "mode": "fast_mvp",
        "canonical_spec": canonical,
        "flat_parameters": flat,
        "validation": {
            "schema_accepted": schema_validation.accepted,
            "constraint_accepted": constraint_result.accepted,
            "hard_violations": [
                {"rule_id": issue.rule_id, "message": issue.message}
                for issue in constraint_result.hard_violations
            ],
            "warnings": warnings[:8],
            "schema_gaps": constraint_result.schema_gaps[:8],
        },
        "extraction": {
            "filename": filename,
            "provider": provider_name,
            "model": model_name,
            "fallback_reason": fallback_reason,
            # Present whenever a photo was supplied and no provider was injected,
            # so "was perception allowed to look at this picture" is answerable
            # from the report itself rather than by inspecting the environment.
            "perception": perception_refusal,
            # Goal 0027. The same fact as `provider`, in the words the customer
            # is owed. /studio renders this; it does not compose its own.
            "mounting_source": _mounting_source(provider_name, perception_refusal),
            "score_kind": (
                "cosine_similarity_uncalibrated"
                if similarity is not None
                else "rule_based_compatibility"
            ),
            "confidence": round(compatibility_score, 3),
            "image": _image_report(metrics, preprocessed),
            "components": {
                "stone_shape": _typed_component(stone.shape),
                "stone_dimensions_mm": _typed_component(dimensions),
                "center_setting": _candidate_component(
                    selected_candidate.center_setting,
                    provider_name,
                    similarity,
                ),
                "prong_count": _candidate_component(
                    int(flat["prong_count"]),
                    provider_name,
                    similarity,
                ),
                # Metal is typed, so it is reported as a typed component with full
                # confidence — never as something the image contributed.
                "metal_color_family": _typed_component(metal.color_family),
                "metal_purity": _typed_component(metal.purity),
                "band_width_mm": _candidate_component(
                    float(flat["band_width_mm"]),
                    provider_name,
                    similarity,
                ),
                "band_thickness_mm": _candidate_component(
                    float(flat["band_thickness_mm"]),
                    provider_name,
                    similarity,
                ),
                "ring_size_us": _typed_component(stone.ring_size_us),
                "stone_carat_eq": _typed_component(stone.carat_eq),
            },
            "matches": [
                _evaluation_to_json(evaluation, selected is evaluation)
                for evaluation in evaluations
            ],
        },
        "typed_inputs": {
            "stone_shape": stone.shape,
            "stone_dimensions_mm": dimensions,
            "stone_carat_eq": stone.carat_eq,
            "ring_size_us": stone.ring_size_us,
            "stone_spec_source": stone_spec_source(certificate),
            "certificate": certificate.to_json() if certificate else None,
            "metal": metal.to_json(),
        },
        "preview": {
            "stone_shape": stone.shape,
            "stone_dimensions_mm": dimensions,
            "carat": stone.carat_eq,
            "metal": preview_metal(metal),
            # Legacy key the 3D viewport reads; same typed value.
            "metal_family": metal.color_family,
            "metal_purity": metal.purity,
            "metal_finish": metal.finish,
            "ring_size_us": stone.ring_size_us,
            "band_width_mm": round(float(flat["band_width_mm"]), 2),
            "band_thickness_mm": round(float(flat["band_thickness_mm"]), 2),
            "center_setting": selected_candidate.center_setting,
            "prong_count": int(flat["prong_count"]),
        },
        # Goal 0023: the volume is never taken from the request. It is measured
        # from the promoted asset's own export bytes and resolved by
        # ``quote_volumes`` at the persistence boundary, so this quote starts
        # ``pending_geometry`` and is narrowed there. The alloy spot price is a
        # commercial input the caller does legitimately own.
        "quote": quote_basis(
            metal,
            metal_price_per_gram=_optional_float(
                payload.get("metal_price_per_gram"), 0.0, 1e6
            ),
        ),
        "rhino": {
            "ready_for_handoff": schema_validation.accepted and constraint_result.accepted,
            "definition_contract": "canonical_spec_v0_1",
            "suggested_endpoint_env": [
                "RHINO_COMPUTE_URL",
                "RHINO_COMPUTE_API_KEY",
                "RHINO_DEFINITION_ID",
            ],
        },
    }


def _parse_stone_inputs(payload: dict[str, Any]) -> StoneInputs:
    shape_value = payload.get("stone_shape", payload.get("shape_hint"))
    shape = _required_choice(shape_value, STONE_SHAPES, "stone_shape")

    nested = payload.get("stone_dimensions_mm")
    nested_dims = nested if isinstance(nested, dict) else {}
    length = _required_float(
        payload.get("stone_length_mm", nested_dims.get("length")),
        0.5,
        20.0,
        "stone_length_mm",
    )
    width = _required_float(
        payload.get("stone_width_mm", nested_dims.get("width")),
        0.5,
        20.0,
        "stone_width_mm",
    )
    depth = _required_float(
        payload.get("stone_depth_mm", nested_dims.get("depth")),
        0.2,
        15.0,
        "stone_depth_mm",
    )
    ring_size = _required_float(
        payload.get("ring_size_us"),
        3.0,
        13.0,
        "ring_size_us",
    )
    raw_carat = payload.get("stone_carat_eq", payload.get("carat"))
    carat = (
        None
        if raw_carat in (None, "")
        else _required_float(raw_carat, 0.1, 10.0, "stone_carat_eq")
    )
    return StoneInputs(
        shape=shape,
        length_mm=length,
        width_mm=width,
        depth_mm=depth,
        ring_size_us=ring_size,
        carat_eq=carat,
    )


# Metal is a typed input, not a mounting override: picking a metal must never
# bypass mounting inference. ``typed_inputs.parse_metal_spec`` still reads the
# legacy ``mounting_overrides.metal_color_family`` spelling, so old clients keep
# working — the value just lands in the typed metal instead of here.
_NON_MOUNTING_OVERRIDE_KEYS = frozenset(
    {"metal_color_family", "metal_karat", "metal_purity", "metal_alloy"}
)


def _parse_mounting_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("mounting_overrides")
    overrides = dict(raw) if isinstance(raw, dict) else {}
    legacy_mapping = {
        "setting_hint": "center_setting",
        "band_width_mm": "band_width_mm",
        "band_thickness_mm": "band_thickness_mm",
        "prong_count": "prong_count",
    }
    for source, target in legacy_mapping.items():
        if payload.get(source) not in (None, "", "auto") and target not in overrides:
            overrides[target] = payload[source]
    return {
        key: value
        for key, value in overrides.items()
        if value not in (None, "") and key not in _NON_MOUNTING_OVERRIDE_KEYS
    }


def _typed_candidate(
    overrides: dict[str, Any],
    stone: StoneInputs,
    metal: MetalSpec,
) -> MountingCandidate:
    fallback = _heuristic_candidate(stone, metal)
    setting = _normalize_choice(
        overrides.get("center_setting"),
        SETTING_CHOICES,
    ) or fallback.center_setting
    band_width = _optional_float(
        overrides.get("band_width_mm"),
        MIN_BAND_WIDTH_MM,
        MAX_BAND_WIDTH_MM,
    )
    band_thickness = _optional_float(
        overrides.get("band_thickness_mm"),
        MIN_BAND_THICKNESS_MM,
        5.0,
    )
    prongs = _optional_int(overrides.get("prong_count"), 0, 12)
    if setting != "prong":
        prongs = 0
    elif prongs is None:
        prongs = fallback.prong_count
    return MountingCandidate(
        reference_id="typed_overrides",
        center_setting=setting,
        prong_count=prongs,
        metal_color_family=metal.color_family,
        band_width_ratio=(
            (band_width if band_width is not None else 2.0) / stone.width_mm
        ),
        band_thickness_ratio=(
            (band_thickness if band_thickness is not None else 1.6) / stone.width_mm
        ),
        similarity=None,
    )


# Goal 0027. Which of the four providers actually chose the mounting, said in the
# words a customer needs rather than a slug they have to look up.
#
# `extraction.provider` has been on the response since Goal 0026 and is precise,
# but "perception_refused_typed_prior" is an engineering term. This turns it into
# the one sentence the person who uploaded the photograph is owed, and it lives
# here -- beside where provider_name is decided -- so a new provider cannot be
# added without a matching sentence: _mounting_source raises on an unknown name
# rather than defaulting to a reassuring one.
_MOUNTING_SOURCE: dict[str, dict[str, Any]] = {
    "typed": {
        "read_your_photo": False,
        "headline": "You chose this mounting",
        "statement": (
            "The setting, prongs and band below are the values you selected. "
            "Nothing was inferred."
        ),
    },
    "heuristic_fallback": {
        "read_your_photo": False,
        "headline": "This mounting is a default for your stone",
        "statement": (
            "No photograph contributed to this mounting. The setting, prongs and "
            "band are MyJewl's standard proportions for the stone shape and size "
            "you typed."
        ),
    },
    "perception_refused_typed_prior": {
        "read_your_photo": False,
        "headline": "Your photo was not read",
        "statement": (
            "MyJewl did not look at your photograph. The setting, prongs and band "
            "below are MyJewl's standard proportions for the stone shape and size "
            "you typed -- the same ones any other photo would have produced."
        ),
    },
    "dinov3_hnsw": {
        "read_your_photo": True,
        "headline": "This mounting was suggested from your photo",
        "statement": (
            "MyJewl matched your photograph against its own catalog to suggest "
            "the setting, prongs and band proportions. The match is diagnostic "
            "and has not passed an accuracy gate, so check it before submitting."
        ),
    },
}


def request_path_disclosure() -> dict[str, Any]:
    """The disclosure a photo request would get, decided exactly as one is.

    Goal 0027, finding F12. ``/api/health`` asks ``perception_lane.lane_state``
    with an index loader, so on an acknowledged deployment it answers
    ``acknowledged_diagnostic`` / available. ``analyze_photo_request`` above asks
    ``perception_lane.decide()`` with **no** loader, so the same deployment
    refuses ``index_not_loaded``. Both are fail-closed and the behaviour is fine;
    what is not fine is that the two surfaces describe the same gate differently.

    That did not matter while the difference was only visible to an operator
    reading JSON. It matters now: /studio reads its pre-upload copy from
    /api/health, so the more permissive answer would have told a customer their
    photo was about to be read by a request that was going to refuse -- the same
    class of overstatement this goal exists to remove, pointing the other way.

    So the customer sentence is taken from *this* decision, made the way the
    request path makes it. The operator fields on /api/health are left alone: they
    answer a different and legitimate question (can this deployment serve
    perception at all), and narrowing them would hide the discrepancy rather than
    contain it.
    """
    return perception_lane.decide().to_json()["customer_disclosure"]


def _mounting_source(
    provider_name: str,
    perception_refusal: dict[str, Any] | None,
) -> dict[str, Any]:
    """Where this request's mounting values came from, for the customer.

    Raises on an unrecognised provider rather than falling back. A provider with
    no disclosure is a provider whose honesty nobody decided, and the failure
    should be loud at the point it is introduced -- not a quiet default that
    happens to read well.
    """
    try:
        source = _MOUNTING_SOURCE[provider_name]
    except KeyError:  # pragma: no cover - guarded by test
        raise ValueError(
            f"no customer disclosure is defined for mounting provider "
            f"{provider_name!r}; add one to _MOUNTING_SOURCE before shipping it"
        ) from None
    return {
        "provider": provider_name,
        **source,
        # Present only when the perception gate was consulted at all, so "was the
        # lane asked, and what did it say" stays answerable from this one block.
        "perception_reason": (
            None if perception_refusal is None else perception_refusal["reason"]
        ),
    }


def _heuristic_candidate(
    stone: StoneInputs,
    metal: MetalSpec,
) -> MountingCandidate:
    """Mounting prior from the typed shape. Nothing here reads the photograph."""
    prong_count = _default_prong_count(stone.shape, stone.width_mm)
    return MountingCandidate(
        reference_id="heuristic_fallback",
        center_setting="prong",
        prong_count=prong_count,
        metal_color_family=metal.color_family,
        band_width_ratio=2.0 / stone.width_mm,
        band_thickness_ratio=1.6 / stone.width_mm,
        similarity=None,
    )


def _evaluate_candidate(
    candidate: MountingCandidate,
    stone: StoneInputs,
    metal: MetalSpec,
) -> CandidateEvaluation:
    """Score one mounting candidate against the typed stone and typed metal.

    A candidate's own ``metal_color_family`` — whatever a retrieval provider
    returned — is discarded: metal comes from the typed input only.
    """
    setting = (
        candidate.center_setting
        if candidate.center_setting in SETTING_CHOICES
        else "prong"
    )
    prong_count = max(0, int(candidate.prong_count)) if setting == "prong" else 0
    band_width = min(
        MAX_BAND_WIDTH_MM,
        max(MIN_BAND_WIDTH_MM, stone.width_mm * candidate.band_width_ratio),
    )
    band_thickness = max(
        MIN_BAND_THICKNESS_MM,
        stone.width_mm * candidate.band_thickness_ratio,
    )
    config_id = _config_id(
        stone,
        setting,
        prong_count,
        band_width,
    )
    flat: dict[str, Any] = {
        "config_id": config_id,
        "archetype": "solitaire",
        "stone_shape": stone.shape,
        # Geometry-bearing typed measurements remain authoritative here. Any
        # display rounding belongs in ``dimensions``/``preview`` fields only;
        # rounding before canonicalization could alias distinct configurations
        # onto the same preview signature.
        "stone_length_mm": stone.length_mm,
        "stone_width_mm": stone.width_mm,
        "stone_depth_mm": stone.depth_mm,
        "center_setting": setting,
        "prong_count": prong_count,
        "metal_type": metal.metal_type,
        "metal_alloy": metal.alloy,
        "band_width_mm": round(band_width, 2),
        "band_thickness_mm": round(band_thickness, 2),
        "ring_size_us": stone.ring_size_us,
    }
    if stone.carat_eq is not None:
        flat["stone_carat_eq"] = round(stone.carat_eq, 2)
    canonical = flat_to_canonical(flat)
    schema_result = validate_job_spec(canonical)
    constraint_result = constraints.validate(canonical)
    return CandidateEvaluation(
        candidate=MountingCandidate(
            reference_id=candidate.reference_id,
            center_setting=setting,
            prong_count=prong_count,
            metal_color_family=metal.color_family,
            band_width_ratio=candidate.band_width_ratio,
            band_thickness_ratio=candidate.band_thickness_ratio,
            similarity=candidate.similarity,
        ),
        flat=flat,
        canonical=canonical,
        schema_accepted=schema_result.accepted,
        constraint_result=constraint_result,
    )


def _extract_image_metrics(image_bytes: bytes) -> ImageMetrics:
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        original_width, original_height = image.size
        image.thumbnail((360, 360))
        arr = np.asarray(image, dtype=np.float32) / 255.0

    lum = _luminance(arr)
    sat = arr.max(axis=2) - arr.min(axis=2)
    color_mask = (sat > 0.10) & (lum > 0.20) & (lum < 0.96)
    if int(color_mask.sum()) < 24:
        color_mask = np.ones_like(lum, dtype=bool)
    r = arr[:, :, 0][color_mask]
    g = arr[:, :, 1][color_mask]
    b = arr[:, :, 2][color_mask]
    sample_sat = sat[color_mask]
    warm_score = float(np.mean(np.maximum(r - b, 0) + np.maximum(g - b, 0) * 0.35))
    rose_score = float(np.mean(np.maximum(r - g, 0) + np.maximum(r - b, 0) * 0.35))
    white_score = float(1.0 - min(1.0, np.mean(sample_sat) * 3.2))
    return ImageMetrics(
        width=original_width,
        height=original_height,
        warm_score=warm_score,
        rose_score=rose_score,
        white_score=white_score,
    )


def _default_prong_count(shape: str, stone_width_mm: float) -> int:
    if shape == "pear":
        return 5
    if shape == "round" and stone_width_mm >= 7.0:
        return 6
    return 4


def _typed_component(value: Any) -> dict[str, Any]:
    return {"value": value, "confidence": 1.0, "source": "typed_input"}


def _candidate_component(
    value: Any,
    provider_name: str,
    similarity: float | None,
) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": (
            round(_bounded_similarity(similarity), 3)
            if similarity is not None
            else (1.0 if provider_name == "typed" else 0.58)
        ),
        "source": provider_name,
    }


def _evaluation_to_json(
    evaluation: CandidateEvaluation,
    selected: bool,
) -> dict[str, Any]:
    candidate = evaluation.candidate
    return {
        "reference_id": candidate.reference_id,
        "similarity": (
            None if candidate.similarity is None else round(candidate.similarity, 5)
        ),
        "selected": selected,
        "schema_accepted": evaluation.schema_accepted,
        "constraint_accepted": evaluation.constraint_result.accepted,
        "hard_violations": [
            {"rule_id": issue.rule_id, "message": issue.message}
            for issue in evaluation.constraint_result.hard_violations
        ],
        "mounting": {
            "center_setting": candidate.center_setting,
            "prong_count": candidate.prong_count,
            "metal_color_family": candidate.metal_color_family,
            "band_width_mm": evaluation.flat["band_width_mm"],
            "band_thickness_mm": evaluation.flat["band_thickness_mm"],
        },
    }


def _metrics_to_json(metrics: ImageMetrics | None) -> dict[str, Any] | None:
    if not metrics:
        return None
    return {
        "width": metrics.width,
        "height": metrics.height,
        "warm_score": round(metrics.warm_score, 3),
        "rose_score": round(metrics.rose_score, 3),
        "white_score": round(metrics.white_score, 3),
    }


def _image_report(
    metrics: ImageMetrics | None,
    preprocessed: PreprocessedImage | None,
) -> dict[str, Any] | None:
    report = _metrics_to_json(metrics)
    if report is None:
        return None
    report["preprocessing"] = (
        preprocessed.to_metadata() if preprocessed is not None else None
    )
    return report


def _bounded_similarity(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _required_choice(value: Any, choices: tuple[str, ...], field: str) -> str:
    normalized = _normalize_choice(value, choices)
    if normalized is None:
        raise ValueError(f"{field} is required and must be one of: {', '.join(choices)}.")
    return normalized


def _normalize_choice(value: Any, choices: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace(" ", "_")
    return normalized if normalized in choices and normalized != "auto" else None


def _required_float(
    value: Any,
    minimum: float,
    maximum: float,
    field: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} is required and must be a JSON number.")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} is required and must be a finite JSON number.")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}.")
    return parsed


def _optional_float(value: Any, minimum: float, maximum: float) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return max(minimum, min(maximum, parsed))


def _optional_int(value: Any, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or int(value) != value:
        return None
    parsed = int(value)
    return max(minimum, min(maximum, parsed))


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned[:96] or "uploaded-ring"


def _config_id(
    stone: StoneInputs,
    setting: str,
    prongs: int,
    band_width: float,
) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    nonce = uuid.uuid4().hex[:12]
    return (
        f"fast_{stamp}_{nonce}_{stone.shape}_"
        f"{stone.length_mm:.6g}x{stone.width_mm:.6g}x{stone.depth_mm:.6g}"
        f"_{setting}{prongs}_{band_width:.1f}mm_us{stone.ring_size_us:g}"
    ).replace(".", "p")


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _luminance(arr: np.ndarray) -> np.ndarray:
    return arr[:, :, 0] * 0.2126 + arr[:, :, 1] * 0.7152 + arr[:, :, 2] * 0.0722
