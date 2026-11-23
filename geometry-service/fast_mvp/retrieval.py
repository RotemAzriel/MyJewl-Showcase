"""Mounting-retrieval providers for the fast MVP."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from fast_mvp.preprocessing import PreprocessedImage


DINOV3_MODEL_ID = "facebook/dinov3-vitl16-pretrain-lvd1689m"


@dataclass(frozen=True)
class MountingCandidate:
    reference_id: str
    center_setting: str
    prong_count: int
    metal_color_family: str
    band_width_ratio: float
    band_thickness_ratio: float
    similarity: float | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "MountingCandidate":
        try:
            return cls(
                reference_id=str(value["reference_id"]),
                center_setting=str(value["center_setting"]).strip().lower(),
                prong_count=int(value.get("prong_count") or 0),
                metal_color_family=str(value["metal_color_family"]).strip().lower(),
                band_width_ratio=float(value["band_width_ratio"]),
                band_thickness_ratio=float(value["band_thickness_ratio"]),
                similarity=(
                    None
                    if value.get("similarity") is None
                    else float(value["similarity"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("RunPod returned an invalid mounting candidate.") from exc


@dataclass(frozen=True)
class RetrievalResponse:
    candidates: tuple[MountingCandidate, ...]
    model: str


class MountingProvider(Protocol):
    def query(
        self,
        image: PreprocessedImage,
        *,
        stone_shape: str,
        top_k: int = 5,
    ) -> RetrievalResponse: ...


class RunPodMountingProvider:
    """Synchronous RunPod Serverless adapter with a bounded timeout."""

    def __init__(
        self,
        endpoint_id: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.endpoint_id = endpoint_id or os.environ.get("RUNPOD_ENDPOINT_ID", "")
        self.api_key = api_key or os.environ.get("RUNPOD_API_KEY", "")
        env_timeout = os.environ.get("RUNPOD_TIMEOUT_SECONDS", "8")
        self.timeout_seconds = (
            float(timeout_seconds) if timeout_seconds is not None else float(env_timeout)
        )

    @property
    def configured(self) -> bool:
        return bool(self.endpoint_id and self.api_key)

    def query(
        self,
        image: PreprocessedImage,
        *,
        stone_shape: str,
        top_k: int = 5,
    ) -> RetrievalResponse:
        if not self.configured:
            raise RuntimeError("RunPod credentials are not configured.")

        url = f"https://api.runpod.ai/v2/{self.endpoint_id}/runsync"
        body = json.dumps(
            {
                "input": {
                    "operation": "query",
                    "stone_shape": stone_shape,
                    "top_k": top_k,
                    "image_base64": base64.b64encode(image.png_bytes).decode("ascii"),
                }
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed RunPod API host
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"RunPod request failed with HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("RunPod request timed out or could not connect.") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("RunPod returned malformed JSON.") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("RunPod returned an invalid response.")
        status = str(payload.get("status") or "").upper()
        if status not in {"COMPLETED", ""}:
            raise RuntimeError(f"RunPod job did not complete: {status.lower()}.")
        output = payload.get("output", payload)
        if not isinstance(output, dict):
            raise RuntimeError("RunPod response is missing output.")
        raw_candidates = output.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise RuntimeError("RunPod returned no mounting candidates.")
        candidates = tuple(
            MountingCandidate.from_mapping(item)
            for item in raw_candidates
            if isinstance(item, dict)
        )
        if not candidates:
            raise RuntimeError("RunPod returned no valid mounting candidates.")
        return RetrievalResponse(
            candidates=candidates,
            model=str(output.get("model") or DINOV3_MODEL_ID),
        )
