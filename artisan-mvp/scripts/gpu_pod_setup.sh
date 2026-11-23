#!/usr/bin/env bash
# gpu_pod_setup.sh — RunPod RTX 4090 pod provisioning for MyJewl M3
# Run once after SSH-ing into a fresh pod.
# Usage: bash gpu_pod_setup.sh [--hdri-count 200]
set -euo pipefail

HDRI_COUNT="${1:-200}"
WORKSPACE="/workspace"
HDRI_DIR="${WORKSPACE}/hdri"
SCRIPT_DIR="${WORKSPACE}/scripts"

echo "=== MyJewl M3 Pod Setup ==="
echo "Workspace: ${WORKSPACE}"
echo "HDRI target count: ${HDRI_COUNT}"
echo ""

# --- 1. Create workspace directories ---
mkdir -p "${HDRI_DIR}" "${SCRIPT_DIR}" \
  "${WORKSPACE}/stl" \
  "${WORKSPACE}/configs" \
  "${WORKSPACE}/output" \
  "${WORKSPACE}/logs"
echo "[1/5] Directories created."

# --- 2. Install Blender 4.2 LTS ---
echo "[2/5] Installing Blender 4.2 LTS via tarball..."
if command -v blender &>/dev/null && blender --version 2>/dev/null | grep -q "4\.2"; then
  echo "  Blender 4.2 already installed: $(blender --version 2>/dev/null | head -1)"
else
  BLENDER_VERSION="4.2.9"
  BLENDER_TAR="blender-${BLENDER_VERSION}-linux-x64.tar.xz"
  BLENDER_URL="https://download.blender.org/release/Blender4.2/${BLENDER_TAR}"
  BLENDER_INSTALL_DIR="/opt/blender-${BLENDER_VERSION}-linux-x64"
  echo "  Downloading Blender ${BLENDER_VERSION}..."
  wget -q --show-progress -O "/tmp/${BLENDER_TAR}" "${BLENDER_URL}"
  echo "  Extracting..."
  tar -xf "/tmp/${BLENDER_TAR}" -C /opt/
  rm "/tmp/${BLENDER_TAR}"
  ln -sf "${BLENDER_INSTALL_DIR}/blender" /usr/local/bin/blender
  echo "  Blender installed: $(blender --version 2>/dev/null | head -1)"
fi

# --- 3. Install Python packages (system Python for s3_upload.py) ---
echo "[3/5] Installing Python dependencies..."
pip3 install --quiet boto3 pyyaml requests

# --- 4. Download HDRIs from Poly Haven ---
echo "[4/5] Downloading ${HDRI_COUNT} HDRIs from Poly Haven..."

python3 - <<PYEOF
import requests, os, sys, time
from pathlib import Path

hdri_dir = Path("${HDRI_DIR}")
target_count = int("${HDRI_COUNT}")
api_url = "https://api.polyhaven.com/assets?t=hdris"

print(f"  Fetching HDRI asset list from Poly Haven API...")
try:
    resp = requests.get(api_url, timeout=30)
    resp.raise_for_status()
    assets = list(resp.json().keys())
except Exception as e:
    print(f"  ERROR: Could not fetch asset list: {e}")
    sys.exit(1)

print(f"  Found {len(assets)} HDRIs available. Downloading {target_count}...")
downloaded = 0
failed = 0

for name in assets[:target_count + 20]:  # fetch extra to account for failures
    if downloaded >= target_count:
        break
    dest = hdri_dir / f"{name}_2k.hdr"
    if dest.exists() and dest.stat().st_size > 100_000:
        downloaded += 1
        continue
    url = f"https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/2k/{name}_2k.hdr"
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        downloaded += 1
        if downloaded % 20 == 0:
            print(f"  Downloaded {downloaded}/{target_count}...")
    except Exception as e:
        failed += 1
        if dest.exists():
            dest.unlink()

actual = len(list(hdri_dir.glob("*.hdr")))
print(f"  HDRI download complete: {actual} files in {hdri_dir}")
if actual < int("${HDRI_COUNT}"):
    print(f"  WARNING: Only {actual} HDRIs downloaded (target: ${HDRI_COUNT})")
    sys.exit(1)
PYEOF

echo ""
ACTUAL_HDRI=$(ls "${HDRI_DIR}"/*.hdr 2>/dev/null | wc -l)
echo "  HDRI count: ${ACTUAL_HDRI}"

# --- 5. Verify GPU + Blender ---
echo "[5/5] Verifying GPU and Blender..."
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null \
  || echo "  WARNING: nvidia-smi not available"

blender --version 2>/dev/null | head -2 || echo "  WARNING: blender command failed"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. SCP the solitaire STL to: ${WORKSPACE}/stl/model.stl"
echo "  2. SCP scripts/blender_render_batch.py to: ${WORKSPACE}/scripts/"
echo "  3. Run Phase B configs via Mac, SCP parameters.json per config"
echo "  4. Run blender_render_batch.py per config"
