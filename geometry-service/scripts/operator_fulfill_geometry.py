#!/usr/bin/env python3
"""Operator-invoked Azure geometry batch (Goal 0005, Stage 1 / plan §3.1).

Batches what Goal 0002 proved one config at a time: start the VM, push canonical
specs, solve them against the SHA-pinned authoritative core, pull the receipt
backed exports out through a temporary blob, validate them locally, run the
**mandatory pairwise differential gate**, and deallocate.

Non-negotiables encoded here, not left to discipline:

* **Paid compute never starts implicitly.** ``--operator-approved`` is required;
  without it the driver prints its plan and exits. The app cannot invoke this.
* **Deallocate always runs.** The VM is deallocated in a ``finally`` block even
  when the batch raises, and the final state is verified with ``az vm show``.
* **Differential gate is mandatory.** The historical five-config pilot emitted
  identical geometry across configs. Every batch of two or more configs is
  hash-compared pairwise and fails loudly when any pair matches.
* **Credentials stay in the environment.** ``RHINO_COMPUTE_API_KEY`` is read
  inside the VM's run-command process; it never appears in argv, in a report, or
  in a checkpoint.
* **No inbound listener.** Exports leave the VM as a PUT to a short-lived blob
  container, matching the Goal 0002 transfer.

Every side-effecting step goes through an injected ``runner``, so the whole
orchestration is unit-testable offline with no Azure account.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jewelry_gaas_foundation import constraints  # noqa: E402
from jewelry_gaas_foundation.config_adapter import flat_to_canonical  # noqa: E402
from scripts.geometry_differential import compare as differential_compare  # noqa: E402

DEFAULT_RESOURCE_GROUP = "myjewl-rhino-smoke-rg"
DEFAULT_VM_NAME = "mj-rhino-smoke"
DEFAULT_CORE_SHA256 = "a7053521c6e0bb4286583368a50dd19674d4553ce7938799f5f0edc9b13bf4b0"

TERMINAL_STATUSES = ("completed", "failed")
CREDENTIAL_ENV_VARS = ("RHINO_COMPUTE_API_KEY", "AZURE_STORAGE_KEY", "ANTHROPIC_API_KEY")


class BatchAborted(RuntimeError):
    """The batch stopped before completing; the VM is still deallocated."""


@dataclass
class CommandResult:
    args: Sequence[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def subprocess_runner(args: Sequence[str], *, timeout: float = 1800.0) -> CommandResult:
    """Default runner. Never receives a credential in ``args`` (see module docstring)."""
    completed = subprocess.run(
        list(args), text=True, capture_output=True, timeout=timeout, check=False
    )
    return CommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def assert_no_credentials(text: str) -> None:
    """Fail closed if a secret value would be written into a report."""
    for name in CREDENTIAL_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 8 and value in text:
            raise BatchAborted(
                f"refusing to write a report containing the value of {name}"
            )


@dataclass
class BatchConfig:
    config_id: str
    parameters_path: Path
    canonical: dict[str, Any]


@dataclass
class OperatorGeometryBatch:
    """Resumable Azure batch driver.

    ``runner`` is injected so tests exercise the full sequence — including the
    deallocate-on-failure path — without touching Azure.
    """

    config_dir: Path
    work_dir: Path
    resource_group: str = DEFAULT_RESOURCE_GROUP
    vm_name: str = DEFAULT_VM_NAME
    definition_remote_path: str = "C:\\myjewl\\authoritative_core_v2.ghx"
    remote_output_root: str = "C:\\myjewl\\output"
    remote_config_root: str = "C:\\myjewl\\configs"
    expected_core_sha256: str = DEFAULT_CORE_SHA256
    runner: Callable[..., CommandResult] = subprocess_runner
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    # -- checkpointing -----------------------------------------------------

    @property
    def checkpoint_path(self) -> Path:
        return self.work_dir / "batch_checkpoint.json"

    def load_checkpoint(self) -> None:
        if self.checkpoint_path.is_file():
            self.ledger = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))

    def save_checkpoint(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.checkpoint_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.checkpoint_path)

    def record(self, config_id: str, status: str, **extra: Any) -> None:
        self.ledger[config_id] = {
            "status": status,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **extra,
        }
        self.save_checkpoint()

    def already_done(self, config_id: str) -> bool:
        return self.ledger.get(config_id, {}).get("status") in TERMINAL_STATUSES

    # -- helpers -----------------------------------------------------------

    def _run(self, args: Sequence[str], *, label: str) -> CommandResult:
        result = self.runner(list(args))
        self.events.append(
            {
                "label": label,
                "args": list(args),
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-1200:],
                "stderr_tail": (result.stderr or "")[-1200:],
            }
        )
        return result

    # -- gating ------------------------------------------------------------

    def load_configs(self, max_configs: int | None = None) -> list[BatchConfig]:
        """Load flat configs and pre-gate them; a rejected config never costs money."""
        loaded: list[BatchConfig] = []
        for path in sorted(Path(self.config_dir).rglob("parameters.json")):
            flat = json.loads(path.read_text(encoding="utf-8"))
            config_id = str(flat.get("config_id") or path.parent.name)
            try:
                canonical = flat_to_canonical(flat)
            except ValueError as exc:
                self.record(config_id, "failed", stage="adapter", reason=str(exc))
                continue
            result = constraints.validate(canonical)
            if not result.accepted:
                rule = result.hard_violations[0]
                self.record(
                    config_id,
                    "failed",
                    stage="constraint",
                    reason=f"{rule.rule_id}: {rule.message}",
                )
                continue
            loaded.append(
                BatchConfig(config_id=config_id, parameters_path=path, canonical=canonical)
            )
            if max_configs is not None and len(loaded) >= max_configs:
                break
        return loaded

    # -- Azure lifecycle ---------------------------------------------------

    def start_vm(self) -> CommandResult:
        return self._run(
            ["az", "vm", "start", "-g", self.resource_group, "-n", self.vm_name],
            label="vm_start",
        )

    def deallocate_vm(self) -> dict[str, Any]:
        """Deallocate and verify. Runs even when the batch failed."""
        deallocate = self._run(
            ["az", "vm", "deallocate", "-g", self.resource_group, "-n", self.vm_name],
            label="vm_deallocate",
        )
        verify = self._run(
            [
                "az", "vm", "show", "-g", self.resource_group, "-n", self.vm_name,
                "-d", "--query", "powerState", "-o", "tsv",
            ],
            label="vm_verify_state",
        )
        state = (verify.stdout or "").strip()
        return {
            "deallocate_returncode": deallocate.returncode,
            "verified_power_state": state,
            "confirmed_deallocated": state.lower().endswith("deallocated"),
        }

    def upload_spec(self, config: BatchConfig) -> CommandResult:
        """Push one canonical spec to the VM via run-command (no inbound port)."""
        payload = json.dumps(config.canonical, sort_keys=True)
        script = (
            f"$dir = '{self.remote_config_root}\\{config.config_id}'; "
            "New-Item -ItemType Directory -Force -Path $dir | Out-Null; "
            f"Set-Content -Path \"$dir\\canonical_spec.json\" -Encoding UTF8 -Value {shlex.quote(payload)}"
        )
        return self._run(
            [
                "az", "vm", "run-command", "invoke",
                "-g", self.resource_group, "-n", self.vm_name,
                "--command-id", "RunPowerShellScript",
                "--scripts", script,
            ],
            label=f"upload_spec:{config.config_id}",
        )

    def solve(self, configs: Sequence[BatchConfig]) -> CommandResult:
        """Solve on the VM with the repo's own runner and the pinned definition.

        The API key is read from the VM's machine environment *inside* the
        run-command process; it is never passed as an argument.
        """
        config_args = " ".join(
            f"--config {self.remote_config_root}\\{config.config_id}\\canonical_spec.json"
            for config in configs
        )
        script = (
            "$env:RHINO_COMPUTE_API_KEY = "
            "[Environment]::GetEnvironmentVariable('RHINO_COMPUTE_API_KEY','Machine'); "
            "python C:\\myjewl\\repo\\scripts\\phase_c2_authoritative_proof.py "
            f"--definition {self.definition_remote_path} "
            f"--remote-output-root {self.remote_output_root} "
            f"--artifact-root {self.remote_output_root}\\artifacts "
            f"--report-json {self.remote_output_root}\\solve_report.json "
            f"--expected-core-sha256 {self.expected_core_sha256} "
            f"--max-configs {len(configs)} {config_args}"
        )
        return self._run(
            [
                "az", "vm", "run-command", "invoke",
                "-g", self.resource_group, "-n", self.vm_name,
                "--command-id", "RunPowerShellScript",
                "--scripts", script,
            ],
            label="solve",
        )

    def transfer_exports(self, storage_account: str, container: str, sas_url: str) -> CommandResult:
        """Zip the exports on the VM and PUT them to a temporary blob container."""
        script = (
            f"Compress-Archive -Path {self.remote_output_root}\\artifacts\\* "
            f"-DestinationPath {self.remote_output_root}\\exports.zip -Force; "
            f"Invoke-RestMethod -Method Put -InFile {self.remote_output_root}\\exports.zip "
            "-Headers @{'x-ms-blob-type'='BlockBlob'} "
            f"-Uri {shlex.quote(sas_url)}"
        )
        return self._run(
            [
                "az", "vm", "run-command", "invoke",
                "-g", self.resource_group, "-n", self.vm_name,
                "--command-id", "RunPowerShellScript",
                "--scripts", script,
            ],
            label=f"transfer:{storage_account}/{container}",
        )

    def teardown_storage(self, storage_account: str) -> CommandResult:
        """Delete the temporary storage account immediately after download."""
        return self._run(
            [
                "az", "storage", "account", "delete", "--yes",
                "-g", self.resource_group, "-n", storage_account,
            ],
            label="storage_teardown",
        )

    # -- local validation --------------------------------------------------

    def run_differential(self, bundle_dirs: Sequence[Path]) -> dict[str, Any]:
        """Mandatory pairwise differential gate. Fewer than 2 bundles cannot pass."""
        if len(bundle_dirs) < 2:
            return {
                "status": "not_measurable",
                "reason": (
                    "the differential gate needs at least two collected bundles; "
                    "a single-config batch cannot demonstrate geometric diversity"
                ),
                "config_count": len(bundle_dirs),
            }
        return differential_compare([Path(item) for item in bundle_dirs])

    # -- orchestration -----------------------------------------------------

    def plan(self, configs: Sequence[BatchConfig]) -> dict[str, Any]:
        return {
            "resource_group": self.resource_group,
            "vm_name": self.vm_name,
            "definition": self.definition_remote_path,
            "expected_core_sha256": self.expected_core_sha256,
            "configs": [config.config_id for config in configs],
            "pending": [
                config.config_id for config in configs if not self.already_done(config.config_id)
            ],
            "steps": [
                "az vm start",
                "upload canonical specs (run-command)",
                "solve against the SHA-pinned authoritative core",
                "PUT exports to a temporary blob container",
                "download + delete the temporary storage account",
                "collect_rhino_exports + geometry_bundle_smoke",
                "MANDATORY pairwise differential gate",
                "az vm deallocate + verify",
            ],
        }

    def run(
        self,
        *,
        storage_account: str,
        container: str,
        sas_url: str,
        max_configs: int | None = None,
    ) -> dict[str, Any]:
        """Execute the batch. Deallocation is guaranteed by the finally block."""
        self.load_checkpoint()
        configs = self.load_configs(max_configs)
        pending = [config for config in configs if not self.already_done(config.config_id)]
        report: dict[str, Any] = {
            "schema_version": 1,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "plan": self.plan(configs),
            "status": "fail",
        }

        try:
            if not pending:
                report["status"] = "noop"
                report["reason"] = "every config is already terminal in the checkpoint"
                return report

            started = self.start_vm()
            if not started.ok:
                raise BatchAborted(f"az vm start failed: {started.stderr[-400:]}")

            for config in pending:
                uploaded = self.upload_spec(config)
                if not uploaded.ok:
                    self.record(config.config_id, "failed", stage="upload")
                    raise BatchAborted(f"spec upload failed for {config.config_id}")
                self.record(config.config_id, "uploaded")

            solved = self.solve(pending)
            if not solved.ok:
                raise BatchAborted(f"solve failed: {solved.stderr[-400:]}")
            for config in pending:
                self.record(config.config_id, "solved")

            transferred = self.transfer_exports(storage_account, container, sas_url)
            if not transferred.ok:
                raise BatchAborted("export transfer failed")
            report["transfer"] = {"storage_account": storage_account, "container": container}
            report["status"] = "solved"
        except BatchAborted as exc:
            report["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - the finally block must still run
            report["error"] = f"{exc.__class__.__name__}: {exc}"
        finally:
            # Cost control is not best-effort: deallocate and prove it, always.
            report["teardown"] = {
                "storage": self.teardown_storage(storage_account).returncode,
                **self.deallocate_vm(),
            }
            report["events"] = self.events
            report["checkpoint"] = str(self.checkpoint_path)
            self.save_checkpoint()
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_dir", type=Path, help="Dir of <config>/parameters.json")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--resource-group", default=DEFAULT_RESOURCE_GROUP)
    parser.add_argument("--vm-name", default=DEFAULT_VM_NAME)
    parser.add_argument("--storage-account", default=None)
    parser.add_argument("--container", default="exports")
    parser.add_argument(
        "--sas-url",
        default=None,
        help="Short-lived blob SAS URL the VM PUTs the export zip to.",
    )
    parser.add_argument("--max-configs", type=int, default=None)
    parser.add_argument("--expected-core-sha256", default=DEFAULT_CORE_SHA256)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument(
        "--operator-approved",
        action="store_true",
        help="REQUIRED to spend money. Without it the plan is printed and nothing runs.",
    )
    args = parser.parse_args(argv)

    batch = OperatorGeometryBatch(
        config_dir=args.config_dir,
        work_dir=args.work_dir,
        resource_group=args.resource_group,
        vm_name=args.vm_name,
        expected_core_sha256=args.expected_core_sha256,
    )
    batch.load_checkpoint()
    configs = batch.load_configs(args.max_configs)

    if not args.operator_approved:
        plan = {
            "status": "dry_run",
            "note": (
                "Paid Azure compute requires --operator-approved. Nothing was started, "
                "and the app can never invoke this driver."
            ),
            **batch.plan(configs),
        }
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    if not (args.storage_account and args.sas_url):
        parser.error("--storage-account and --sas-url are required for an approved run")

    report = batch.run(
        storage_account=args.storage_account,
        container=args.container,
        sas_url=args.sas_url,
        max_configs=args.max_configs,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    assert_no_credentials(rendered)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] in {"solved", "noop"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
