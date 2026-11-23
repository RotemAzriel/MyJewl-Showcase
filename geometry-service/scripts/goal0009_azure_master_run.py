#!/usr/bin/env python3
"""Bounded Azure Rhino.Compute run for the Goal 0009 preview master.

This operator-only helper starts the approved VM, uploads the generated GHX
and VM-side client, performs one solve, transfers only the resulting ZIP over
a temporary IP-restricted HTTP rule, removes the transfer service/rules, and
always deallocates and verifies the VM in ``finally``.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manual.tools.build_goal0009_preview_master_ghx import build  # noqa: E402


RESOURCE_GROUP = "myjewl-rhino-smoke-rg"
VM_NAME = "mj-rhino-smoke"
NSG_NAME = "mj-rhino-smokeNSG"
TRANSFER_RULE = "goal0009-preview-transfer"
TRANSFER_PORT = 8019
REMOTE_WORK = r"C:\MyJewlGoal0009"
REMOTE_OUTPUT = r"C:\MyJewlGenerated\goal0009_preview_master"
JOB_ID = "goal0009-reference-v2"


def run_command(args, *, timeout=900, check=True):
    completed = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout, check=False,
    )
    if check and completed.returncode:
        raise RuntimeError(
            "command failed ({}): {}".format(completed.returncode, completed.stderr[-1200:])
        )
    return completed


def az(*args, timeout=900, check=True):
    return run_command(("az",) + args, timeout=timeout, check=check)


def vm_powershell(script, *, timeout=900, check=True):
    return az(
        "vm", "run-command", "invoke",
        "-g", RESOURCE_GROUP, "-n", VM_NAME,
        "--command-id", "RunPowerShellScript",
        "--scripts", script,
        timeout=timeout,
        check=check,
    )


def upload_file(local_path: Path, remote_path: str):
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    remote_dir = remote_path.rsplit("\\", 1)[0]
    script = (
        "$ErrorActionPreference='Stop'; "
        "New-Item -ItemType Directory -Force -Path '{}' | Out-Null; "
        "[IO.File]::WriteAllBytes('{}',[Convert]::FromBase64String('{}')); "
        "Write-Output 'UPLOAD_OK'"
    ).format(remote_dir, remote_path, encoded)
    result = vm_powershell(script)
    if "UPLOAD_OK" not in result.stdout:
        raise RuntimeError("Azure upload did not return its success marker")


def power_state():
    result = az(
        "vm", "show", "-g", RESOURCE_GROUP, "-n", VM_NAME, "-d",
        "--query", "powerState", "-o", "tsv",
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument(
        "--reuse-existing", action="store_true",
        help="Package an already completed receipt-backed VM output without solving again.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "goal_0009_photoreal_solitaire_20260729" / "azure_v2",
    )
    args = parser.parse_args()
    if not args.operator_approved:
        print(json.dumps({
            "status": "approval_required",
            "resource_group": RESOURCE_GROUP,
            "vm_name": VM_NAME,
            "job_id": JOB_ID,
            "guarantees": [
                "one bounded Rhino.Compute solve",
                "temporary transfer rule restricted to operator public IP",
                "transfer cleanup",
                "VM deallocate and verify in finally",
            ],
        }, indent=2))
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ghx_path = args.output_dir / "goal0009_preview_master_v2.ghx"
    build(ghx_path)
    transfer_started = False
    nsg_created = False
    result = {
        "schema": "myjewl.goal0009.azure_master_run.v2",
        "status": "failed",
        "job_id": JOB_ID,
        "vm_final_state": "unknown",
    }
    archive_path = args.output_dir / (JOB_ID + ".zip")
    try:
        az("vm", "start", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200)
        result["vm_started_state"] = power_state()
        if args.reuse_existing:
            verify_existing = (
                "$ErrorActionPreference='Stop'; $dir='{root}\\{job}'; "
                "$required=@('solitaire_reference_master_v2.3dm','solitaire_reference_master_v2.obj','solitaire_reference_master_v2_receipt.json'); "
                "foreach($name in $required) {{ if (!(Test-Path ($dir+'\\'+$name))) {{ throw ('missing '+$name) }} }}; "
                "Write-Output 'EXISTING_RECEIPT_OK'"
            ).format(root=REMOTE_OUTPUT, job=JOB_ID)
            verified = vm_powershell(verify_existing)
            if "EXISTING_RECEIPT_OK" not in verified.stdout:
                raise RuntimeError("existing Azure master receipt could not be verified")
            result["solve_status"] = "reused_completed_azure_receipt"
        else:
            upload_file(ghx_path, REMOTE_WORK + r"\goal0009_preview_master_v2.ghx")
            upload_file(
                ROOT / "scripts" / "goal0009_compute_client.py",
                REMOTE_WORK + r"\goal0009_compute_client.py",
            )

            solve_script = (
                "$ErrorActionPreference='Stop'; "
                "$env:RHINO_COMPUTE_API_KEY=[Environment]::GetEnvironmentVariable('RHINO_COMPUTE_API_KEY','Machine'); "
                "if ([string]::IsNullOrWhiteSpace($env:RHINO_COMPUTE_API_KEY)) {{ throw 'VM API key unavailable' }}; "
                "python '{work}\\goal0009_compute_client.py' --definition '{work}\\goal0009_preview_master_v2.ghx' "
                "--job-id '{job}' --base-url 'http://localhost'"
            ).format(work=REMOTE_WORK, job=JOB_ID)
            solved = vm_powershell(solve_script, timeout=1200)
            completed_reason = "goal0009_azure_rhino_preview_master_exported"
            if '"execution_status": "completed"' not in solved.stdout and completed_reason not in solved.stdout:
                raise RuntimeError("Rhino.Compute solve did not complete: " + solved.stdout[-6000:])
            result["solve_status"] = "completed"

        zip_script = (
            "$ErrorActionPreference='Stop'; "
            "$source='{root}\\{job}'; $zip='{root}\\{job}.zip'; "
            "if (!(Test-Path $source)) {{ throw 'output directory missing' }}; "
            "Compress-Archive -Path ($source+'\\*') -DestinationPath $zip -Force; "
            "Write-Output 'ZIP_OK'"
        ).format(root=REMOTE_OUTPUT, job=JOB_ID)
        zipped = vm_powershell(zip_script)
        if "ZIP_OK" not in zipped.stdout:
            raise RuntimeError("Azure output archive was not created")

        operator_ip = urllib.request.urlopen("https://api.ipify.org", timeout=20).read().decode().strip()
        az(
            "network", "nsg", "rule", "create",
            "-g", RESOURCE_GROUP, "--nsg-name", NSG_NAME,
            "-n", TRANSFER_RULE, "--priority", "1020",
            "--direction", "Inbound", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", operator_ip + "/32",
            "--destination-port-ranges", str(TRANSFER_PORT),
        )
        nsg_created = True
        server_script = (
            "$ErrorActionPreference='Stop'; "
            "Get-NetFirewallRule -DisplayName 'Goal0009 Preview Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "New-NetFirewallRule -DisplayName 'Goal0009 Preview Transfer' -Direction Inbound -Action Allow "
            "-Protocol TCP -LocalPort {port} -Profile Any | Out-Null; "
            "$proc=Start-Process python -ArgumentList '-m','http.server','{port}','--bind','0.0.0.0','--directory','{root}' -PassThru; "
            "Set-Content -Path '{work}\\transfer.pid' -Value $proc.Id; Write-Output 'SERVER_OK'"
        ).format(port=TRANSFER_PORT, root=REMOTE_OUTPUT, work=REMOTE_WORK)
        server = vm_powershell(server_script)
        if "SERVER_OK" not in server.stdout:
            raise RuntimeError("temporary Azure transfer server failed to start")
        transfer_started = True

        public_ip = az(
            "vm", "show", "-d", "-g", RESOURCE_GROUP, "-n", VM_NAME,
            "--query", "publicIps", "-o", "tsv",
        ).stdout.strip()
        url = "http://{}:{}/{}.zip".format(public_ip, TRANSFER_PORT, JOB_ID)
        last_error = None
        for _ in range(12):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    archive_path.write_bytes(response.read())
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(3)
        if last_error:
            raise RuntimeError("artifact download failed: {}".format(last_error))
        result["archive"] = archive_path.name
        result["archive_bytes"] = archive_path.stat().st_size
        result["status"] = "completed"
    finally:
        if transfer_started:
            cleanup = (
                "$pidPath='{work}\\transfer.pid'; "
                "if (Test-Path $pidPath) {{ $serverPid=Get-Content $pidPath; Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue; Remove-Item $pidPath -Force }}; "
                "Get-NetFirewallRule -DisplayName 'Goal0009 Preview Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"
            ).format(work=REMOTE_WORK)
            vm_powershell(cleanup, check=False)
        if nsg_created:
            az(
                "network", "nsg", "rule", "delete",
                "-g", RESOURCE_GROUP, "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE,
                check=False,
            )
        az("vm", "deallocate", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200, check=False)
        result["vm_final_state"] = power_state()
        result["confirmed_deallocated"] = result["vm_final_state"].lower().endswith("deallocated")
        report_path = args.output_dir / "azure_master_run_report.json"
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" and result["confirmed_deallocated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
