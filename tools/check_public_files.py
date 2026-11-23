#!/usr/bin/env python3
"""Check tracked publication paths and high-confidence credential patterns without printing values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
paths = [ROOT / p.decode() for p in result.stdout.split(b"\0") if p] if result.returncode == 0 else []
if not paths:
    paths = [p for p in ROOT.rglob("*") if p.is_file() and not any(x in p.relative_to(ROOT).parts for x in (".git", "node_modules", "__pycache__", ".pytest_cache", "dist", "out", "output", "runs", "data"))]
patterns = {
    "private key material": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{70,}",
    "AWS access key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "GitHub token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b",
    "provider key": rb"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{25,}",
    "Slack webhook": rb"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
}
issues = []
for path in paths:
    rel = path.relative_to(ROOT)
    if path.is_symlink():
        issues.append((str(rel), "symlink"))
        continue
    if (path.name.startswith(".env") and path.name != ".env.example") or path.suffix in (".pem", ".key", ".p12", ".pfx", ".log", ".db", ".sqlite", ".sqlite3") or any(x in rel.parts for x in (".venv", "node_modules", "__pycache__", "runs", "out", "output")) or ("data" in rel.parts and "tests" not in rel.parts):
        issues.append((str(rel), "private or generated path"))
        continue
    if path.suffix in (".png", ".jpg", ".jpeg", ".hdr", ".meshbin"):
        continue
    for label, expression in patterns.items():
        if re.search(expression, path.read_bytes()):
            issues.append((str(rel), label))
for path, issue in issues:
    print(f"{path}: {issue}", file=sys.stderr)
print(f"Checked {len(paths)} publication files; {len(issues)} blocking findings.")
raise SystemExit(bool(issues))
