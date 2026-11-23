#!/usr/bin/env python3
"""Run the Goal 0010 candidate once on the approved Azure Rhino Core.

This runner is intentionally operator-gated. It reads the Rhino Compute key
from the VM machine environment through the VM-side client, transfers only a
receipt-backed ZIP over a temporary IP-restricted rule, removes the transfer
service/rule, and deallocates the VM in ``finally``.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manual.tools.build_goal0010_component_pilot_ghx import build  # noqa: E402


RESOURCE_GROUP = "myjewl-rhino-smoke-rg"
VM_NAME = "mj-rhino-smoke"
NSG_NAME = "mj-rhino-smokeNSG"
TRANSFER_RULE = "goal0010-component-pilot-transfer"
TRANSFER_PORT = 8020
REMOTE_WORK = r"C:\MyJewlGoal0010"
REMOTE_OUTPUT = r"C:\MyJewlGenerated\goal0010_component_pilot"
JOB_ID = "goal0010-component-pilot-v1"


def run_command(args, *, timeout=900, check=True):
    completed = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
    if check and completed.returncode:
        raise RuntimeError("command failed ({}): {}".format(completed.returncode, completed.stderr[-1200:]))
    return completed


def az(*args, timeout=900, check=True):
    return run_command(("az",) + args, timeout=timeout, check=check)


def vm_powershell(script, *, timeout=900, check=True):
    return az("vm", "run-command", "invoke", "-g", RESOURCE_GROUP, "-n", VM_NAME, "--command-id", "RunPowerShellScript", "--scripts", script, timeout=timeout, check=check)


def upload_file(local_path: Path, remote_path: str):
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    remote_dir = remote_path.rsplit("\\", 1)[0]
    script = ("$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force -Path '{}' | Out-Null; "
              "[IO.File]::WriteAllBytes('{}',[Convert]::FromBase64String('{}')); Write-Output 'UPLOAD_OK'").format(remote_dir, remote_path, encoded)
    if "UPLOAD_OK" not in vm_powershell(script).stdout:
        raise RuntimeError("Azure upload did not return its success marker")


def power_state():
    return az("vm", "show", "-g", RESOURCE_GROUP, "-n", VM_NAME, "-d", "--query", "powerState", "-o", "tsv").stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument("--reuse-existing", action="store_true", help="Package the receipt from the completed solve without solving again")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "goal_0010_component_pilot_20260730" / "azure")
    args = parser.parse_args()
    if not args.operator_approved:
        print(json.dumps({"status": "approval_required", "resource_group": RESOURCE_GROUP, "vm_name": VM_NAME, "job_id": JOB_ID, "guarantees": ["one bounded Rhino.Compute solve", "temporary transfer rule restricted to current public IP", "transfer cleanup", "VM deallocate and verify in finally"]}, indent=2))
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ghx_path = args.output_dir / "goal0010_component_pilot_v1.ghx"
    build(ghx_path)
    transfer_started = False
    nsg_created = False
    result = {"schema": "myjewl.goal0010.azure_component_pilot_run.v1", "status": "failed", "job_id": JOB_ID, "vm_final_state": "unknown"}
    archive_path = args.output_dir / (JOB_ID + ".zip")
    try:
        az("vm", "start", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200)
        result["vm_started_state"] = power_state()
        if args.reuse_existing:
            verify_script = ("$ErrorActionPreference='Stop'; $dir='{root}\{job}'; "
                             "$required=@('solitaire_component_pilot_v1_receipt.json','solitaire_component_pilot_v1.obj'); "
                             "foreach($name in $required) {{ if (!(Test-Path ($dir+'\\'+$name))) {{ throw ('missing '+$name) }} }}; "
                             "Write-Output 'EXISTING_RECEIPT_OK'").format(root=REMOTE_OUTPUT, job=JOB_ID)
            verified = vm_powershell(verify_script)
            if "EXISTING_RECEIPT_OK" not in verified.stdout:
                raise RuntimeError("completed Goal 0010 receipt could not be verified")
            result["solve_status"] = "reused_completed_azure_receipt"
        else:
            upload_file(ghx_path, REMOTE_WORK + r"\goal0010_component_pilot_v1.ghx")
            upload_file(ROOT / "scripts" / "goal0010_compute_client.py", REMOTE_WORK + r"\goal0010_compute_client.py")
            solve_script = ("$ErrorActionPreference='Stop'; $env:RHINO_COMPUTE_API_KEY=[Environment]::GetEnvironmentVariable('RHINO_COMPUTE_API_KEY','Machine'); "
                            "if ([string]::IsNullOrWhiteSpace($env:RHINO_COMPUTE_API_KEY)) {{ throw 'VM API key unavailable' }}; "
                            "python '{work}\goal0010_compute_client.py' --definition '{work}\goal0010_component_pilot_v1.ghx' --job-id '{job}' --base-url 'http://localhost'").format(work=REMOTE_WORK, job=JOB_ID)
            solved = vm_powershell(solve_script, timeout=1200)
            if '"execution_status": "completed"' not in solved.stdout:
                raise RuntimeError("Rhino.Compute candidate solve did not complete: " + solved.stdout[-6000:])
            result["solve_status"] = "completed"

        zip_script = ("$ErrorActionPreference='Stop'; $source='{root}\{job}'; $zip='{root}\{job}.zip'; "
                      "if (!(Test-Path $source)) {{ throw 'output directory missing' }}; Compress-Archive -Path ($source+'\*') -DestinationPath $zip -Force; Write-Output 'ZIP_OK'").format(root=REMOTE_OUTPUT, job=JOB_ID)
        if "ZIP_OK" not in vm_powershell(zip_script).stdout:
            raise RuntimeError("Azure output archive was not created")

        operator_ip = urllib.request.urlopen("https://api.ipify.org", timeout=20).read().decode().strip()
        az("network", "nsg", "rule", "create", "-g", RESOURCE_GROUP, "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, "--priority", "1021", "--direction", "Inbound", "--access", "Allow", "--protocol", "Tcp", "--source-address-prefixes", operator_ip + "/32", "--destination-port-ranges", str(TRANSFER_PORT))
        nsg_created = True
        server_script = ("$ErrorActionPreference='Stop'; Get-NetFirewallRule -DisplayName 'Goal0010 Component Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
                         "New-NetFirewallRule -DisplayName 'Goal0010 Component Transfer' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {port} -Profile Any | Out-Null; "
                         "$python=(Get-Command python -ErrorAction Stop).Source; $proc=Start-Process -FilePath $python -WorkingDirectory '{root}' -ArgumentList @('-m','http.server','{port}','--bind','0.0.0.0') -PassThru -WindowStyle Hidden; "
                         "Start-Sleep -Seconds 2; if ($proc.HasExited) {{ throw 'temporary Python server exited' }}; Set-Content -Path '{work}\\transfer.pid' -Value $proc.Id; Write-Output 'SERVER_OK'").format(port=TRANSFER_PORT, root=REMOTE_OUTPUT, work=REMOTE_WORK)
        server_result = vm_powershell(server_script, check=False)
        if "SERVER_OK" not in server_result.stdout:
            diagnostics = (server_result.stdout + "\n" + server_result.stderr)[-2400:]
            raise RuntimeError("temporary Azure transfer server failed to start: " + diagnostics)
        transfer_started = True
        public_ip = az("vm", "show", "-d", "-g", RESOURCE_GROUP, "-n", VM_NAME, "--query", "publicIps", "-o", "tsv").stdout.strip()
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
        received = args.output_dir / "received"
        received.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(received)
        result["archive"] = archive_path.name
        result["archive_bytes"] = archive_path.stat().st_size
        result["received_dir"] = str(received)
        result["status"] = "completed"
    finally:
        if transfer_started:
            cleanup = ("$pidPath='{work}\\transfer.pid'; if (Test-Path $pidPath) {{ $serverPid=Get-Content $pidPath; Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue; Remove-Item $pidPath -Force }}; Get-NetFirewallRule -DisplayName 'Goal0010 Component Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule").format(work=REMOTE_WORK)
            vm_powershell(cleanup, check=False)
        if nsg_created:
            az("network", "nsg", "rule", "delete", "-g", RESOURCE_GROUP, "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, check=False)
        az("vm", "deallocate", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200, check=False)
        result["vm_final_state"] = power_state()
        result["confirmed_deallocated"] = result["vm_final_state"].lower().endswith("deallocated")
        report_path = args.output_dir / "azure_component_pilot_run_report.json"
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" and result["confirmed_deallocated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
