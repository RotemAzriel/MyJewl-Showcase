"""Rhino.Compute batch driver with config-level checkpoint/resume (G-7).

Drives the M4 geometry batch: for each ``parameters.json`` under a config
directory it runs the flat->canonical adapter (G-6) and the constraint filter,
POSTs accepted configs to Rhino.Compute (reusing
``geometry_adapter.RhinoComputeHttpAdapter``), and persists a per-config status
ledger so a long (≈48-hr) batch survives VM restarts / timeouts without
re-solving completed configs. Geometry-gate failures are logged to
``rejected_configs.jsonl`` and never crash the batch.

Budget (M4): ~2-5 min/config; ~8-21 hr for 1,000 configs on a D2s_v5 (2 vCPU);
size the wall-clock window for 48 hr. This module builds and unit-tests the
driver only — it does not start the live batch.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from jewelry_gaas_foundation import constraints
from jewelry_gaas_foundation.config_adapter import flat_to_canonical
from jewelry_gaas_foundation.geometry_adapter import (
    RhinoComputeGrasshopperAdapter,
    RhinoComputeHttpAdapter,
)

# The five geometry gates the Rhino.Compute definition reports on (mirrors
# validation.validate_job_spec's pending_geometry_checks).
PENDING_GEOMETRY_CHECKS = (
    "minimum_structural_floors",
    "stone_stone_collision",
    "stone_metal_collision",
    "mountability_and_setting_access",
    "mesh_closedness_and_manifold_suitability",
)

_TERMINAL_STATUSES = ("completed", "failed")


def discover_configs(config_dir: str | Path) -> list[Path]:
    """Return sorted paths of every ``parameters.json`` under ``config_dir``."""
    return sorted(Path(config_dir).rglob("parameters.json"))


class RhinoBatchRunner:
    """Resumable Rhino.Compute batch over a directory of flat configs.

    ``adapter`` is any object exposing ``evaluate_geometry(spec, pending_checks,
    output_targets) -> GeometryAdapterResult`` — production code passes a
    ``RhinoComputeHttpAdapter``; tests inject a fake client.
    """

    def __init__(
        self,
        config_dir: str | Path,
        adapter: Any,
        checkpoint_path: str | Path | None = None,
        rejected_path: str | Path | None = None,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.adapter = adapter
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else (
            self.config_dir / "batch_checkpoint.json"
        )
        self.rejected_path = Path(rejected_path) if rejected_path else (
            self.config_dir / "rejected_configs.jsonl"
        )
        self.ledger: dict[str, dict[str, Any]] = self._load_checkpoint()

    # -- checkpoint persistence --------------------------------------------

    def _load_checkpoint(self) -> dict[str, dict[str, Any]]:
        if self.checkpoint_path.is_file():
            return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        return {}

    def _save_checkpoint(self) -> None:
        """Atomically persist the ledger (temp file + rename) so a crash mid-write
        never corrupts the checkpoint."""
        tmp = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.checkpoint_path)

    def _record(self, config_id: str, status: str, *, reason: str = "", gate_id: str = "") -> None:
        self.ledger[config_id] = {
            "status": status,
            "reason": reason,
            "gate_id": gate_id,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save_checkpoint()

    def _reject(self, config_id: str, gate_id: str, reason: str) -> str:
        with self.rejected_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"config_id": config_id, "gate_id": gate_id, "reason": reason}) + "\n"
            )
        self._record(config_id, "failed", reason=reason, gate_id=gate_id)
        return "failed"

    def _already_done(self, config_id: str) -> bool:
        return self.ledger.get(config_id, {}).get("status") in _TERMINAL_STATUSES

    # -- per-config processing ---------------------------------------------

    def process_one(self, params_path: Path) -> str:
        """Process a single config; returns completed | failed | skipped."""
        flat = json.loads(Path(params_path).read_text(encoding="utf-8"))
        config_id = str(flat.get("config_id") or Path(params_path).parent.name)

        if self._already_done(config_id):
            return "skipped"

        # G-6: flat -> canonical (loud failure, no silent no-op).
        try:
            canonical = flat_to_canonical(flat)
        except ValueError as exc:
            return self._reject(config_id, "adapter", str(exc))

        # Constraint filter — reject HARD configs before paying for a Rhino solve.
        constraint_result = constraints.validate(canonical)
        if not constraint_result.accepted:
            rule = constraint_result.hard_violations[0]
            return self._reject(config_id, "constraint", f"{rule.rule_id}: {rule.message}")

        # POST to Rhino.Compute. Never let an adapter exception crash the batch.
        try:
            result = self.adapter.evaluate_geometry(
                canonical, PENDING_GEOMETRY_CHECKS, ("STL",)
            )
        except Exception as exc:  # noqa: BLE001 - batch resilience boundary
            return self._reject(config_id, "rhino", f"adapter_exception: {exc}")

        if result.execution_status != "completed":
            return self._reject(
                config_id, "rhino", result.reason_code or result.execution_status
            )

        for gate in result.geometry_gates:
            if gate.status == "failed":
                return self._reject(config_id, gate.code, gate.reason_code or "geometry_gate_failed")

        self._record(config_id, "completed")
        return "completed"

    # -- batch loop --------------------------------------------------------

    def run(self) -> dict[str, int]:
        summary = {"completed": 0, "failed": 0, "skipped": 0}
        for params_path in discover_configs(self.config_dir):
            outcome = self.process_one(params_path)
            summary[outcome] = summary.get(outcome, 0) + 1
        return summary


def build_rhino_adapter(
    base_url: str,
    api_key: str,
    definition_id: str,
    *,
    endpoint: str = "solve",
    definition_path: str | Path | None = None,
    grasshopper_input_name: str = "json_string",
    grasshopper_job_id_input_name: str | None = None,
    grasshopper_response_output: str = "response_json",
    timeout_seconds: float = 300.0,
) -> RhinoComputeHttpAdapter | RhinoComputeGrasshopperAdapter:
    """Construct the production Rhino.Compute adapter."""

    if endpoint == "solve":
        return RhinoComputeHttpAdapter(
            base_url=base_url,
            api_key=api_key,
            definition_id=definition_id,
            timeout_seconds=timeout_seconds,
        )

    if endpoint == "grasshopper":
        if definition_path is None:
            raise ValueError("--definition-path is required for --endpoint grasshopper")
        return RhinoComputeGrasshopperAdapter(
            base_url=base_url,
            api_key=api_key,
            definition_path=definition_path,
            definition_id=definition_id,
            input_name=grasshopper_input_name,
            job_id_input_name=grasshopper_job_id_input_name,
            response_output_name=grasshopper_response_output,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(f"unsupported Rhino.Compute endpoint: {endpoint}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rhino.Compute batch driver (G-7).")
    parser.add_argument("config_dir", help="Directory containing <config>/parameters.json")
    parser.add_argument("--base-url", default=os.environ.get("RHINO_COMPUTE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("RHINO_COMPUTE_API_KEY", ""))
    parser.add_argument("--definition-id", default=os.environ.get("RHINO_DEFINITION_ID", ""))
    parser.add_argument(
        "--endpoint",
        choices=("solve", "grasshopper"),
        default=os.environ.get("RHINO_COMPUTE_ENDPOINT", "solve"),
        help="Use adapter-compatible /solve or native Rhino.Compute /grasshopper.",
    )
    parser.add_argument(
        "--definition-path",
        default=os.environ.get("RHINO_DEFINITION_PATH"),
        help="Grasshopper .gh/.ghx file path for --endpoint grasshopper.",
    )
    parser.add_argument(
        "--grasshopper-input-name",
        default=os.environ.get("RHINO_GRASSHOPPER_INPUT_NAME", "json_string"),
        help="String input name that receives canonical spec JSON.",
    )
    parser.add_argument(
        "--grasshopper-job-id-input-name",
        default=os.environ.get("RHINO_GRASSHOPPER_JOB_ID_INPUT_NAME"),
        help="Optional string input name that receives the canonical job_id.",
    )
    parser.add_argument(
        "--grasshopper-response-output",
        default=os.environ.get("RHINO_GRASSHOPPER_RESPONSE_OUTPUT", "response_json"),
        help="String output name containing adapter-compatible JSON.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("RHINO_COMPUTE_TIMEOUT_SECONDS", "300")),
    )
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--rejected", default=None)
    args = parser.parse_args(argv)

    if not (args.base_url and args.api_key and args.definition_id):
        parser.error(
            "Rhino.Compute connection requires --base-url, --api-key, and --definition-id "
            "(or RHINO_COMPUTE_URL / RHINO_COMPUTE_API_KEY / RHINO_DEFINITION_ID env vars)."
        )
    if args.endpoint == "grasshopper" and not args.definition_path:
        parser.error(
            "--definition-path (or RHINO_DEFINITION_PATH) is required for "
            "--endpoint grasshopper."
        )

    runner = RhinoBatchRunner(
        config_dir=args.config_dir,
        adapter=build_rhino_adapter(
            args.base_url,
            args.api_key,
            args.definition_id,
            endpoint=args.endpoint,
            definition_path=args.definition_path,
            grasshopper_input_name=args.grasshopper_input_name,
            grasshopper_job_id_input_name=args.grasshopper_job_id_input_name,
            grasshopper_response_output=args.grasshopper_response_output,
            timeout_seconds=args.timeout_seconds,
        ),
        checkpoint_path=args.checkpoint,
        rejected_path=args.rejected,
    )
    summary = runner.run()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
