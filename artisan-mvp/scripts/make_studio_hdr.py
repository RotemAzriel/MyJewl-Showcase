"""Write a small jewelry-studio HDR environment (Radiance .hdr, equirectangular).

Used by the demo's fallback 3D viewer (model-viewer) so metal and stone pick up
crisp softbox reflections instead of the generic "neutral" lighting. Synthetic,
so there is no licence to track: a soft grey sweep, a large overhead softbox,
two front strip boxes, a back rim light and a dim floor bounce.

    python3 scripts/make_studio_hdr.py demo/static/env/studio_softbox.hdr
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

WIDTH, HEIGHT = 512, 256

# (azimuth deg, elevation deg, half-width deg, half-height deg, intensity)
SOFTBOXES = (
    (0.0, 75.0, 60.0, 12.0, 6.0),  # overhead, wraps the top of the ring
    (-40.0, 20.0, 9.0, 22.0, 14.0),  # front-left strip
    (40.0, 20.0, 9.0, 22.0, 14.0),  # front-right strip
    (180.0, 25.0, 30.0, 10.0, 8.0),  # back rim
    (95.0, 5.0, 6.0, 30.0, 5.0),  # side kicker (sparkle)
    (-95.0, 5.0, 6.0, 30.0, 5.0),
)


def radiance(azimuth: float, elevation: float) -> tuple[float, float, float]:
    # Grey studio sweep: brighter above the horizon, darker floor.
    t = (elevation + 90.0) / 180.0
    base = 0.06 + 0.32 * t**1.6
    value = base
    for az, el, hw, hh, power in SOFTBOXES:
        d_az = (azimuth - az + 180.0) % 360.0 - 180.0
        dx = abs(d_az) / hw
        dy = abs(elevation - el) / hh
        if dx < 1.0 and dy < 1.0:
            edge = min(1.0, (1.0 - max(dx, dy)) * 6.0)  # soft edge
            value += power * edge
    warm = (1.0, 0.985, 0.96)
    return value * warm[0], value * warm[1], value * warm[2]


def rgbe(r: float, g: float, b: float) -> bytes:
    v = max(r, g, b)
    if v < 1e-32:
        return b"\x00\x00\x00\x00"
    mantissa, exponent = math.frexp(v)
    scale = mantissa * 256.0 / v
    return bytes((int(r * scale), int(g * scale), int(b * scale), exponent + 128))


def write_hdr(path: Path) -> None:
    rows = []
    for y in range(HEIGHT):
        elevation = 90.0 - (y + 0.5) * 180.0 / HEIGHT
        row = bytearray()
        for x in range(WIDTH):
            azimuth = (x + 0.5) * 360.0 / WIDTH - 180.0
            row += rgbe(*radiance(azimuth, elevation))
        rows.append(bytes(row))
    header = f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {HEIGHT} +X {WIDTH}\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(rows))


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/static/env/studio_softbox.hdr")
    write_hdr(target)
    print(f"wrote {target} ({target.stat().st_size // 1024} KB)")
