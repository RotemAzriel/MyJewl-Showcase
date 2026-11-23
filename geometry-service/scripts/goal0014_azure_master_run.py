#!/usr/bin/env python3
"""Run the operator-approved Goal 0014 config-differential batch on Azure Rhino Core.

Three configurations in one VM session, spanning the typed axes:

  small    US 4.0 / 5.00 mm
  control  US 6.5 / 6.50 mm  (the approved Goal 0011 V4 shape)
  large    US 9.0 / 8.00 mm

Few configurations on purpose: this run tests input responsiveness, not catalog
coverage. The runner is inert without ``--operator-approved``. It keeps the Rhino
Compute key inside the VM environment, transfers receipt-backed archives through a
temporary IP-restricted rule, removes transfer access, and deallocates the VM in
``finally``.

The acceptance instrument for this run is ``scripts/geometry_differential.py``
(hashes across configs), never ``scripts/goal0011_differential.py``, which
compares two definition versions of one fixed configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jewelry_gaas_foundation.master_parameters import DIFFERENTIAL_BATCH  # noqa: E402
from manual.tools.build_goal0014_parametric_master_ghx import (  # noqa: E402
    TEMPLATE_ID,
    build as build_v5,
)
from scripts.goal0011_azure_master_run import (  # noqa: E402
    NSG_NAME,
    RESOURCE_GROUP,
    VM_NAME,
    _safe_extract,
    az,
    power_state,
    powershell_messages,
    upload_file,
    vm_powershell,
)

TRANSFER_RULE = "goal0014-parametric-master-transfer"
TRANSFER_PORT = 8022
REMOTE_WORK_ROOT = r"C:\MyJewlGoal0014"
REMOTE_OUTPUT = r"C:\MyJewlGenerated\goal0014_parametric_master"
GHX_NAME = "goal0014_parametric_master_v5.ghx"
MASTER_STEM = "solitaire_parametric_master_v5"


def job_id_for(name: str) -> str:
    return "goal0014-parametric-master-" + name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    if args.output_dir is None:
        args.output_dir = (
            ROOT / "output" / "goal_0014_parametric_master_20260804" / "azure_v5"
        )

    configs = {
        name: {
            "job_id": job_id_for(name),
            "request": dict(request, template_id=TEMPLATE_ID),
        }
        for name, request in DIFFERENTIAL_BATCH.items()
    }

    if not args.operator_approved:
        print(
            json.dumps(
                {
                    "status": "approval_required",
                    "resource_group": RESOURCE_GROUP,
                    "vm_name": VM_NAME,
                    "template_id": TEMPLATE_ID,
                    "config_count": len(configs),
                    "configs": {
                        name: {
                            "job_id": item["job_id"],
                            "ring_size_us": item["request"]["canonical_spec"]["ring"][
                                "size"
                            ],
                            "stone_mm": item["request"]["canonical_spec"]["stones"][0][
                                "dimensions_mm"
                            ],
                        }
                        for name, item in configs.items()
                    },
                    "guarantees": [
                        "three bounded Rhino.Compute solves in one VM session",
                        "temporary transfer rule restricted to current public IP",
                        "transfer cleanup",
                        "VM deallocate and verify in finally",
                        "API key read only from the VM machine environment",
                    ],
                    "acceptance_instrument": "scripts/geometry_differential.py",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ghx_path = args.output_dir / GHX_NAME
    build_v5(ghx_path)

    transfer_started = False
    nsg_created = False
    report_path = args.output_dir / "azure_master_run_report.json"
    result: dict = {
        "schema": "myjewl.goal0014.azure_master_run.v1",
        "status": "failed",
        "template_id": TEMPLATE_ID,
        "configs": {},
        "vm_final_state": "unknown",
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }

    try:
        az("vm", "start", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200)
        result["vm_started_state"] = power_state()

        upload_file(ghx_path, REMOTE_WORK_ROOT + "\\" + GHX_NAME)
        upload_file(
            ROOT / "scripts" / "goal0011_compute_client.py",
            REMOTE_WORK_ROOT + r"\goal0011_compute_client.py",
        )
        upload_file(
            ROOT / "scripts" / "goal0014_compute_client.py",
            REMOTE_WORK_ROOT + r"\goal0014_compute_client.py",
        )

        for name, item in sorted(configs.items()):
            job_id = item["job_id"]
            request_local = args.output_dir / (name + "_request.json")
            request_local.write_text(
                json.dumps(item["request"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            upload_file(
                request_local, REMOTE_WORK_ROOT + "\\" + name + "_request.json"
            )
            solve_script = (
                "$ErrorActionPreference='Stop'; "
                "$env:RHINO_COMPUTE_API_KEY=[Environment]::GetEnvironmentVariable("
                "'RHINO_COMPUTE_API_KEY','Machine'); "
                "if ([string]::IsNullOrWhiteSpace($env:RHINO_COMPUTE_API_KEY)) "
                "{{ throw 'VM API key unavailable' }}; "
                r"python '{work}\goal0014_compute_client.py' --definition '{work}\{ghx}' "
                r"--request-file '{work}\{name}_request.json' --job-id '{job}' "
                "--base-url 'http://localhost'"
            ).format(work=REMOTE_WORK_ROOT, ghx=GHX_NAME, name=name, job=job_id)
            solved = vm_powershell(solve_script, timeout=1200)
            messages = powershell_messages(solved.stdout)
            if not any('"execution_status": "completed"' in m for m in messages):
                raise RuntimeError(
                    "Rhino.Compute solve did not complete for "
                    + name
                    + ": "
                    + "\n".join(messages)[-6000:]
                )
            result["configs"][name] = {"job_id": job_id, "solve_status": "completed"}

        zip_script = (
            r"$ErrorActionPreference='Stop'; $root='{root}'; $zip='{root}.zip'; "
            "if (!(Test-Path $root)) {{ throw 'output root missing' }}; "
            "if (Test-Path $zip) {{ Remove-Item $zip -Force }}; "
            r"Compress-Archive -Path ($root+'\*') -DestinationPath $zip -Force; "
            "Write-Output 'ZIP_OK'"
        ).format(root=REMOTE_OUTPUT)
        if "ZIP_OK" not in vm_powershell(zip_script).stdout:
            raise RuntimeError("Azure output archive was not created")

        operator_ip = (
            urllib.request.urlopen("https://api.ipify.org", timeout=20)
            .read()
            .decode()
            .strip()
        )
        az(
            "network", "nsg", "rule", "create", "-g", RESOURCE_GROUP,
            "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, "--priority", "1023",
            "--direction", "Inbound", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", operator_ip + "/32",
            "--destination-port-ranges", str(TRANSFER_PORT),
        )
        nsg_created = True
        server_script = (
            "$ErrorActionPreference='Stop'; "
            "$listeners=Get-NetTCPConnection -LocalPort {port} -State Listen "
            "-ErrorAction SilentlyContinue; "
            "foreach($listener in $listeners) {{ Stop-Process -Id "
            "$listener.OwningProcess -Force -ErrorAction SilentlyContinue }}; "
            "Get-NetFirewallRule -DisplayName 'Goal0014 Parametric Master Transfer' "
            "-ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "New-NetFirewallRule -DisplayName 'Goal0014 Parametric Master Transfer' "
            "-Direction Inbound -Action Allow -Protocol TCP -LocalPort {port} "
            "-Profile Any | Out-Null; "
            "$python=(Get-Command python -ErrorAction Stop).Source; "
            "$proc=Start-Process -FilePath $python -WorkingDirectory "
            "'{parent}' -ArgumentList @('-m','http.server','{port}','--bind','0.0.0.0') "
            "-PassThru -WindowStyle Hidden; "
            "Start-Sleep -Seconds 2; if ($proc.HasExited) "
            "{{ throw 'temporary Python server exited' }}; "
            r"Set-Content -Path '{work}\transfer.pid' -Value $proc.Id; "
            "Write-Output 'SERVER_OK'"
        ).format(
            port=TRANSFER_PORT,
            parent=REMOTE_OUTPUT.rsplit("\\", 1)[0],
            work=REMOTE_WORK_ROOT,
        )
        transfer_started = True
        server_result = vm_powershell(server_script, check=False)
        server_messages = powershell_messages(server_result.stdout)
        if not any("SERVER_OK" in message for message in server_messages):
            diagnostics = (
                "\n".join(server_messages) + "\n" + server_result.stderr
            )[-2400:]
            raise RuntimeError(
                "temporary Azure transfer server failed to start: " + diagnostics
            )

        public_ip = az(
            "vm", "show", "-d", "-g", RESOURCE_GROUP, "-n", VM_NAME,
            "--query", "publicIps", "-o", "tsv",
        ).stdout.strip()
        archive_name = REMOTE_OUTPUT.rsplit("\\", 1)[-1] + ".zip"
        url = "http://{}:{}/{}".format(public_ip, TRANSFER_PORT, archive_name)
        archive_path = args.output_dir / archive_name
        last_error = None
        for _ in range(12):
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    archive_path.write_bytes(response.read())
                last_error = None
                break
            except Exception as exc:  # bounded retry before guaranteed cleanup
                last_error = exc
                time.sleep(3)
        if last_error:
            raise RuntimeError("artifact download failed: {}".format(last_error))

        received = args.output_dir / "received"
        received.mkdir(parents=True, exist_ok=True)
        _safe_extract(archive_path, received)
        result["archive"] = archive_path.name
        result["archive_bytes"] = archive_path.stat().st_size
        result["received_dir"] = str(received)
        result["status"] = "completed"
    finally:
        if transfer_started:
            cleanup = (
                r"$pidPath='{work}\transfer.pid'; if (Test-Path $pidPath) {{ "
                "$serverPid=Get-Content $pidPath; Stop-Process -Id $serverPid -Force "
                "-ErrorAction SilentlyContinue; Remove-Item $pidPath -Force }}; "
                "$listeners=Get-NetTCPConnection -LocalPort {port} -State Listen "
                "-ErrorAction SilentlyContinue; "
                "foreach($listener in $listeners) {{ Stop-Process -Id "
                "$listener.OwningProcess -Force -ErrorAction SilentlyContinue }}; "
                "Get-NetFirewallRule -DisplayName 'Goal0014 Parametric Master Transfer' "
                "-ErrorAction SilentlyContinue | Remove-NetFirewallRule"
            ).format(work=REMOTE_WORK_ROOT, port=TRANSFER_PORT)
            vm_powershell(cleanup, check=False)
        if nsg_created:
            az(
                "network", "nsg", "rule", "delete", "-g", RESOURCE_GROUP,
                "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, check=False,
            )
        az(
            "vm", "deallocate", "-g", RESOURCE_GROUP, "-n", VM_NAME,
            timeout=1200, check=False,
        )
        result["vm_final_state"] = power_state()
        result["confirmed_deallocated"] = (
            result["vm_final_state"].lower().endswith("deallocated")
        )
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" and result["confirmed_deallocated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
