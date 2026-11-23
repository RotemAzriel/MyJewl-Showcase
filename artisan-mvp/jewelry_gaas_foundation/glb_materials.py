"""Give an Artisan-exported GLB real jewelry materials.

RhinoArtisan's GLB export writes one default material (flat red) shared by every
mesh. Its meshes keep their layer names, though: ``Metal 01..03`` and
``Gem 01..``. ``style_glb`` rewrites only the glTF JSON chunk (the binary buffer is
untouched): a PBR metal per alloy family, and a clear, refractive diamond (one material per gem mesh) using
``KHR_materials_transmission`` + ``KHR_materials_ior`` (viewers without those
extensions fall back to a glossy white stone).

Display only: this changes how the GLB looks, never its geometry.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942

# Linear base colours (F0) for polished metals — physically based reference values
# (gold/silver after the glTF/Filament material tables; rose/white gold blended).
METAL_BASE_COLORS: dict[str, tuple[float, float, float]] = {
    "GOLD_YELLOW": (1.000, 0.766, 0.336),
    "GOLD_ROSE": (0.960, 0.640, 0.540),
    "GOLD_WHITE": (0.860, 0.855, 0.835),
    "GOLD_GREEN": (0.880, 0.800, 0.460),
    "GOLD_24": (1.000, 0.735, 0.270),
    "PLATINUM": (0.835, 0.825, 0.805),
    "PALLADIUM": (0.830, 0.820, 0.800),
    "SILVER": (0.972, 0.960, 0.915),
}
_DEFAULT_METAL = (0.86, 0.86, 0.84)
DIAMOND_IOR = 2.42


def metal_base_color(artisan_metal: str) -> tuple[float, float, float]:
    name = artisan_metal.upper()
    for prefix, color in METAL_BASE_COLORS.items():
        if name.startswith(prefix):
            return color
    return _DEFAULT_METAL


def _read_glb(data: bytes) -> tuple[dict, bytes | None]:
    if data[:4] != _GLB_MAGIC:
        raise ValueError("not a GLB file")
    json_len, json_type = struct.unpack("<II", data[12:20])
    if json_type != _JSON_CHUNK:
        raise ValueError("first GLB chunk is not JSON")
    doc = json.loads(data[20 : 20 + json_len])
    offset = 20 + json_len
    binary = None
    if offset + 8 <= len(data):
        bin_len, bin_type = struct.unpack("<II", data[offset : offset + 8])
        if bin_type == _BIN_CHUNK:
            binary = data[offset + 8 : offset + 8 + bin_len]
    return doc, binary


def _write_glb(doc: dict, binary: bytes | None) -> bytes:
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    chunks = struct.pack("<II", len(json_bytes), _JSON_CHUNK) + json_bytes
    if binary is not None:
        padded = binary + b"\x00" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(padded), _BIN_CHUNK) + padded
    return _GLB_MAGIC + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def style_glb(path: str | Path, artisan_metal: str) -> dict[str, int]:
    """Assign metal / diamond materials by mesh name, in place. Returns counts."""
    path = Path(path)
    doc, binary = _read_glb(path.read_bytes())
    r, g, b = metal_base_color(artisan_metal)
    diamond = {
        "pbrMetallicRoughness": {
            "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.0,
        },
        "extensions": {
            "KHR_materials_transmission": {"transmissionFactor": 1.0},
            "KHR_materials_ior": {"ior": DIAMOND_IOR},
        },
        "doubleSided": True,
    }
    materials = [
        {
            "name": f"MyJewl metal {artisan_metal}",
            "pbrMetallicRoughness": {
                "baseColorFactor": [r, g, b, 1.0],
                "metallicFactor": 1.0,
                "roughnessFactor": 0.16,
            },
            "doubleSided": True,
        },
    ]
    # One diamond material PER gem mesh. A ray-traced diamond shader (iJewel/WebGi)
    # bakes the geometry of the first mesh using a material into it, so a shared
    # material gave the center stone the melee mesh's map and it rendered black.
    counts = {"metal": 0, "gem": 0}
    for mesh in doc.get("meshes", []):
        name = str(mesh.get("name", ""))
        is_gem = name.lower().startswith("gem")
        if is_gem:
            materials.append({"name": f"MyJewl diamond {name}".strip(), **json.loads(json.dumps(diamond))})
            index = len(materials) - 1
        else:
            index = 0
        for primitive in mesh.get("primitives", []):
            primitive["material"] = index
        counts["gem" if is_gem else "metal"] += 1
    doc["materials"] = materials
    used = set(doc.get("extensionsUsed", []))
    used.update({"KHR_materials_transmission", "KHR_materials_ior"})
    doc["extensionsUsed"] = sorted(used)
    path.write_bytes(_write_glb(doc, binary))
    return counts
