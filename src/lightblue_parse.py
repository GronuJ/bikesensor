"""
Parse a LightBlue (Punch Through) BLE log export into a tidy sensor CSV.

LightBlue's CSV export typically has columns like:
    Time, UUID, Value (hex bytes), ...

You need to know the byte layout your ESP32 firmware writes into the
characteristic. This helper assumes the default MPU-6050 packing:

    int16_t ax, ay, az, gx, gy, gz   (little-endian, 12 bytes total)

with the standard ±2g / ±250 °/s ranges. Adapt SCALE_ACC / SCALE_GYR and
the unpack format if your firmware differs.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pandas as pd

SCALE_ACC = 1.0 / 16384.0  # LSB -> g    (±2g range)
SCALE_GYR = 1.0 / 131.0    # LSB -> °/s  (±250 °/s range)


def _hex_to_floats(hexstr: str) -> tuple[float, ...] | None:
    raw = bytes.fromhex(hexstr.replace(" ", "").replace("0x", ""))
    if len(raw) < 12:
        return None
    ax, ay, az, gx, gy, gz = struct.unpack("<hhhhhh", raw[:12])
    return (
        ax * SCALE_ACC, ay * SCALE_ACC, az * SCALE_ACC,
        gx * SCALE_GYR, gy * SCALE_GYR, gz * SCALE_GYR,
    )


def parse_lightblue_csv(path: str | Path) -> pd.DataFrame:
    """Read a LightBlue CSV export, decode hex payloads, return tidy DataFrame."""
    df = pd.read_csv(path)
    # LightBlue column names vary; pick the first plausible time + hex columns.
    time_col = next(c for c in df.columns if "time" in c.lower())
    hex_col = next(c for c in df.columns if "value" in c.lower() or "hex" in c.lower())

    decoded = df[hex_col].astype(str).map(_hex_to_floats)
    df = df[decoded.notna()].copy()
    df[["ax", "ay", "az", "gx", "gy", "gz"]] = pd.DataFrame(
        decoded.dropna().tolist(), index=df.index
    )
    df["timestamp"] = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"])[
        ["timestamp", "ax", "ay", "az", "gx", "gy", "gz"]
    ].reset_index(drop=True)


if __name__ == "__main__":
    import sys
    out = parse_lightblue_csv(sys.argv[1])
    out.to_csv(sys.argv[2], index=False)
    print(f"wrote {len(out)} rows -> {sys.argv[2]}")
