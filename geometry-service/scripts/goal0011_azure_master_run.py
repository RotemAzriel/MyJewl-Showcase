#!/usr/bin/env python3
"""Run one operator-approved Goal 0011 master solve on Azure Rhino Core.

The runner is intentionally inert without ``--operator-approved``.  It keeps
the Rhino Compute key inside the VM environment, transfers a receipt-backed
archive through a temporary IP-restricted rule, removes transfer access, and
deallocates the VM in ``finally``.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manual.tools.build_goal0011_realism_master_ghx import build as build_v1  # noqa: E402
from manual.tools.build_goal0011_realism_master_v2_ghx import build as build_v2  # noqa: E402
from manual.tools.build_goal0011_realism_master_v4_ghx import build as build_v4  # noqa: E402


RESOURCE_GROUP = "myjewl-rhino-smoke-rg"
VM_NAME = "mj-rhino-smoke"
NSG_NAME = "mj-rhino-smokeNSG"
TRANSFER_RULE = "goal0011-realism-master-transfer"
TRANSFER_PORT = 8021
REMOTE_WORK_ROOT = r"C:\MyJewlGoal0011"
REMOTE_OUTPUT = r"C:\MyJewlGenerated\goal0011_realism_master"
REVISION_CONFIG = {
    "v1": {
        "builder": build_v1,
        "template_id": "goal0011-realism-first-round-solitaire-master-v1",
        "job_id": "goal0011-realism-master-v1",
        "ghx_name": "goal0011_realism_master_v1.ghx",
        "master_stem": "solitaire_realism_master_v1",
    },
    "v2": {
        "builder": build_v2,
        "template_id": "goal0011-realism-first-round-solitaire-master-v2",
        "job_id": "goal0011-realism-master-v2",
        "ghx_name": "goal0011_realism_master_v2.ghx",
        "master_stem": "solitaire_realism_master_v2",
    },
    "v4": {
        "builder": build_v4,
        "template_id": "goal0011-realism-first-round-solitaire-master-v4",
        "job_id": "goal0011-realism-master-v4",
        "ghx_name": "goal0011_realism_master_v4.ghx",
        "master_stem": "solitaire_realism_master_v4",
    },
}


def run_command(args, *, timeout=900, check=True):
    completed = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
    if check and completed.returncode:
        raise RuntimeError("command failed ({}): {}".format(completed.returncode, completed.stderr[-1200:]))
    return completed


def az(*args, timeout=900, check=True):
    return run_command(("az",) + args, timeout=timeout, check=check)


def vm_powershell(script, *, timeout=900, check=True):
    return az(
        "vm", "run-command", "invoke", "-g", RESOURCE_GROUP, "-n", VM_NAME,
        "--command-id", "RunPowerShellScript", "--scripts", script,
        timeout=timeout, check=check,
    )


def upload_file(local_path: Path, remote_path: str):
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    remote_dir = remote_path.rsplit("\\", 1)[0]
    script = (
        "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force -Path '{}' | Out-Null; "
        "[IO.File]::WriteAllBytes('{}',[Convert]::FromBase64String('{}')); Write-Output 'UPLOAD_OK'"
    ).format(remote_dir, remote_path, encoded)
    if "UPLOAD_OK" not in vm_powershell(script).stdout:
        raise RuntimeError("Azure upload did not return its success marker")


def power_state():
    return az(
        "vm", "show", "-g", RESOURCE_GROUP, "-n", VM_NAME, "-d",
        "--query", "powerState", "-o", "tsv",
    ).stdout.strip()


def powershell_messages(stdout: str) -> list[str]:
    """Extract run-command message bodies from Azure CLI wrapper JSON."""
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError:
        return [stdout]
    values = body.get("value") if isinstance(body, dict) else None
    if not isinstance(values, list):
        return [stdout]
    return [
        str(item.get("message", ""))
        for item in values
        if isinstance(item, dict)
    ]


def normalize_archive_member(name: str) -> str:
    """Return a POSIX-separated relative path for a zip member.

    PowerShell's ``Compress-Archive`` writes Windows backslash separators into the
    archive, against the ZIP spec (APPNOTE 4.4.17.1: forward slashes only).
    Python's ``zipfile`` takes the spec at its word, so a member recorded as
    ``job-name\\part.stl`` extracts as a single flat file whose *name* contains a
    literal backslash -- the directory tree silently collapses. It has cost real
    time twice, on Goal 0014 (2026-08-04) and Goal 0018 (2026-08-12), where 75
    flat filenames needed renaming by hand before any bundle tooling could read
    the transfer. Normalising here means every caller of ``_safe_extract`` gets
    the fix, and nothing downstream has to know the archive came from PowerShell.

    Leading separators and Windows drive prefixes are stripped so the result is
    always relative; ``_safe_extract`` still checks containment afterwards.
    """
    normalized = name.replace("\\", "/")
    if len(normalized) > 1 and normalized[1] == ":":  # "C:/..." from a drive path
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _safe_extract(archive_path: Path, destination: Path):
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            name = normalize_archive_member(member.filename)
            if not name:
                continue
            target = (destination / name).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise RuntimeError(
                    "unsafe archive member: {}".format(member.filename)
                ) from exc
            if member.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-approved", action="store_true")
    parser.add_argument("--revision", choices=tuple(REVISION_CONFIG), default="v4")
    parser.add_argument(
        "--reuse-existing", action="store_true",
        help="Transfer the already completed receipt without executing another solve",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="Defaults to a revision-specific Goal 0011 Azure evidence directory",
    )
    args = parser.parse_args(argv)
    revision = REVISION_CONFIG[args.revision]
    job_id = revision["job_id"]
    template_id = revision["template_id"]
    master_stem = revision["master_stem"]
    ghx_name = revision["ghx_name"]
    remote_work = REMOTE_WORK_ROOT + "\\" + args.revision
    if args.output_dir is None:
        args.output_dir = (
            ROOT / "output" / "goal_0011_realism_master_20260730" / ("azure_" + args.revision)
        )
    if not args.operator_approved:
        print(json.dumps({
            "status": "approval_required",
            "resource_group": RESOURCE_GROUP,
            "vm_name": VM_NAME,
            "revision": args.revision,
            "template_id": template_id,
            "job_id": job_id,
            "guarantees": [
                "one bounded Rhino.Compute solve",
                "temporary transfer rule restricted to current public IP",
                "transfer cleanup",
                "VM deallocate and verify in finally",
            ],
        }, indent=2))
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ghx_path = args.output_dir / ghx_name
    revision["builder"](ghx_path)
    transfer_started = False
    nsg_created = False
    report_path = args.output_dir / "azure_master_run_report.json"
    previous_attempt = None
    if report_path.is_file():
        try:
            previous_attempt = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_attempt = {"status": "unreadable_previous_report"}
    result = {
        "schema": "myjewl.goal0011.azure_master_run.v1",
        "status": "failed",
        "revision": args.revision,
        "template_id": template_id,
        "job_id": job_id,
        "vm_final_state": "unknown",
        "manufacturing_gates": "pending_not_run_needs_calibration",
    }
    if previous_attempt is not None:
        result["previous_attempt"] = previous_attempt
    archive_path = args.output_dir / (job_id + ".zip")
    try:
        az("vm", "start", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200)
        result["vm_started_state"] = power_state()
        if args.reuse_existing:
            verify_script = (
                r"$ErrorActionPreference='Stop'; $dir='{root}\{job}'; "
                "$required=@('{stem}_receipt.json','{stem}.obj'); "
                r"foreach($name in $required) {{ if (!(Test-Path ($dir+'\'+$name))) {{ throw ('missing '+$name) }} }}; "
                "Write-Output 'EXISTING_RECEIPT_OK'"
            ).format(root=REMOTE_OUTPUT, job=job_id, stem=master_stem)
            verified = vm_powershell(verify_script)
            if not any("EXISTING_RECEIPT_OK" in message for message in powershell_messages(verified.stdout)):
                raise RuntimeError("completed Goal 0011 receipt could not be verified")
            result["solve_status"] = "reused_completed_azure_receipt_no_second_solve"
        else:
            upload_file(ghx_path, remote_work + "\\" + ghx_name)
            upload_file(ROOT / "scripts" / "goal0011_compute_client.py", remote_work + r"\goal0011_compute_client.py")
            solve_script = (
                "$ErrorActionPreference='Stop'; "
                "$env:RHINO_COMPUTE_API_KEY=[Environment]::GetEnvironmentVariable('RHINO_COMPUTE_API_KEY','Machine'); "
                "if ([string]::IsNullOrWhiteSpace($env:RHINO_COMPUTE_API_KEY)) {{ throw 'VM API key unavailable' }}; "
                r"python '{work}\goal0011_compute_client.py' --definition '{work}\{ghx}' "
                "--template-id '{template}' --job-id '{job}' --base-url 'http://localhost'"
            ).format(work=remote_work, ghx=ghx_name, template=template_id, job=job_id)
            solved = vm_powershell(solve_script, timeout=1200)
            messages = powershell_messages(solved.stdout)
            if not any('"execution_status": "completed"' in message for message in messages):
                raise RuntimeError("Rhino.Compute master solve did not complete: " + "\n".join(messages)[-6000:])
            result["solve_status"] = "completed"

        zip_script = (
            r"$ErrorActionPreference='Stop'; $source='{root}\{job}'; $zip='{root}\{job}.zip'; "
            "if (!(Test-Path $source)) {{ throw 'output directory missing' }}; "
            r"Compress-Archive -Path ($source+'\*') -DestinationPath $zip -Force; Write-Output 'ZIP_OK'"
        ).format(root=REMOTE_OUTPUT, job=job_id)
        if "ZIP_OK" not in vm_powershell(zip_script).stdout:
            raise RuntimeError("Azure output archive was not created")

        operator_ip = urllib.request.urlopen("https://api.ipify.org", timeout=20).read().decode().strip()
        az(
            "network", "nsg", "rule", "create", "-g", RESOURCE_GROUP,
            "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, "--priority", "1022",
            "--direction", "Inbound", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", operator_ip + "/32",
            "--destination-port-ranges", str(TRANSFER_PORT),
        )
        nsg_created = True
        server_script = (
            "$ErrorActionPreference='Stop'; "
            "$listeners=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
            "foreach($listener in $listeners) {{ Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue }}; "
            "Get-NetFirewallRule -DisplayName 'Goal0011 Realism Master Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "New-NetFirewallRule -DisplayName 'Goal0011 Realism Master Transfer' -Direction Inbound -Action Allow "
            "-Protocol TCP -LocalPort {port} -Profile Any | Out-Null; "
            "$python=(Get-Command python -ErrorAction Stop).Source; "
            "$proc=Start-Process -FilePath $python -WorkingDirectory '{root}' "
            "-ArgumentList @('-m','http.server','{port}','--bind','0.0.0.0') -PassThru -WindowStyle Hidden; "
            "Start-Sleep -Seconds 2; if ($proc.HasExited) {{ throw 'temporary Python server exited' }}; "
            r"Set-Content -Path '{work}\transfer.pid' -Value $proc.Id; Write-Output 'SERVER_OK'"
        ).format(port=TRANSFER_PORT, root=REMOTE_OUTPUT, work=remote_work)
        # Mark cleanup required before invocation because PowerShell can start
        # the listener and then fail before writing the PID marker.
        transfer_started = True
        server_result = vm_powershell(server_script, check=False)
        server_messages = powershell_messages(server_result.stdout)
        if not any("SERVER_OK" in message for message in server_messages):
            diagnostics = ("\n".join(server_messages) + "\n" + server_result.stderr)[-2400:]
            raise RuntimeError("temporary Azure transfer server failed to start: " + diagnostics)
        public_ip = az(
            "vm", "show", "-d", "-g", RESOURCE_GROUP, "-n", VM_NAME,
            "--query", "publicIps", "-o", "tsv",
        ).stdout.strip()
        url = "http://{}:{}/{}.zip".format(public_ip, TRANSFER_PORT, job_id)
        last_error = None
        for _ in range(12):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
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
                "$serverPid=Get-Content $pidPath; Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue; "
                "Remove-Item $pidPath -Force }}; "
                "$listeners=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
                "foreach($listener in $listeners) {{ Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue }}; "
                "Get-NetFirewallRule -DisplayName 'Goal0011 Realism Master Transfer' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"
            ).format(work=remote_work, port=TRANSFER_PORT)
            vm_powershell(cleanup, check=False)
        if nsg_created:
            az(
                "network", "nsg", "rule", "delete", "-g", RESOURCE_GROUP,
                "--nsg-name", NSG_NAME, "-n", TRANSFER_RULE, check=False,
            )
        az("vm", "deallocate", "-g", RESOURCE_GROUP, "-n", VM_NAME, timeout=1200, check=False)
        result["vm_final_state"] = power_state()
        result["confirmed_deallocated"] = result["vm_final_state"].lower().endswith("deallocated")
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" and result["confirmed_deallocated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
