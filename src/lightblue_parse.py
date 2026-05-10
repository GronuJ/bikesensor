"""
Decode a LightBlue (iOS) CSV export into a per-sample IMU CSV with
reconstructed wallclock timestamps.

LightBlue export columns vary slightly by version; we look for any column
whose name contains "time" and any whose name contains "value"/"hex"/"data".

Wire protocol (see firmware/bikesensor/bikesensor.ino):
  SYNC: [0xA5][u32 sample_idx LE][u16 fs LE][u8 n_axes][u8 _]
  DATA: [0x5A][u32 first_sample_idx LE][u8 n_samples][N * 6*int16 BE]

Clock model: the phone records receive-time t_phone for each SYNC packet,
along with the firmware's sample_idx_now. We fit a linear model
    t_phone ≈ a + b * sample_idx
across all SYNCs in the session (least-squares). 'b' is the *measured*
sample period (≈ 1/fs ± crystal drift); 'a' anchors the phone wallclock.
We then assign every IMU sample a timestamp from that model. This kills
BLE jitter (≈50–200 ms) and absorbs any constant offset between the
ESP32 MCU clock and the phone clock.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd

ACC_SCALE = 1.0 / 8192.0   # ±4g  -> g
GYR_SCALE = 1.0 / 65.5     # ±500 -> °/s


import re

def _hex_bytes(s: str) -> bytes:
    """
    Robustly converts a variety of string-based hex formats into bytes.
    
    Handles formats like:
      - '<a5 00 00 00 00 f8 00 06 00>' (iOS LightBlue style)
      - '0x A5 00 00...'
      - 'A5:00:00...'
    """
    # Remove any surrounding angle brackets (common in iOS LightBlue exports)
    m = re.search(r'<([^>]+)>', s)
    if m:
        s = m.group(1)
    try:
        # Strip common delimiters and convert to raw bytes
        return bytes.fromhex(s.replace(" ", "").replace("0x", "").replace(":", ""))
    except ValueError:
        return b""


def parse(path: str | Path) -> pd.DataFrame:
    """
    Parses a LightBlue CSV export and reconstructs sample timestamps.
    
    The LightBlue app logs the 'receive time' of each BLE notification. However, 
    BLE jitter and OS scheduling mean these timestamps are noisy. To get high-
    precision timestamps, we use a 'Clock Model' that maps the firmware's 
    monotonically increasing sample index to the phone's wallclock time.
    
    Steps:
      1. Load CSV and identify columns containing 'time' and 'hex data'.
      2. Iterate through rows, decoding SYNC and DATA packets.
      3. Use SYNC packets (which contain the firmware's current index and 
         the nominal sample rate) as anchors for the clock model.
      4. Fit a linear model (t_phone = a + b * sample_idx) using least-squares.
         - 'b' is the actual duration of one sample (1/fs).
         - 'a' is the wallclock anchor.
      5. Apply the model to every DATA sample to get noise-free timestamps.
    """
    df = pd.read_csv(path, on_bad_lines="skip")
    
    # Heuristically find the time and hex/data columns
    tcol = next(c for c in df.columns if "time" in c.lower())
    vcol = next(c for c in df.columns
                if any(k in c.lower() for k in ("value", "hex", "data", "bytes", "logevent")))
    
    df = df[[tcol, vcol]].rename(columns={tcol: "t_phone", vcol: "hex"}).dropna()
    df["t_phone"] = pd.to_datetime(df["t_phone"], utc=True, format="ISO8601",
                                    errors="coerce")
    df = df.dropna(subset=["t_phone"]).reset_index(drop=True)

    syncs: list[tuple[pd.Timestamp, int, int, int]] = []   # (t_phone, sample_idx, fs, batt)
    sample_idx: list[int] = []
    raw: list[bytes] = []

    # State Machine: Parse raw hex bytes from the CSV
    for t_phone, hexstr in zip(df["t_phone"], df["hex"], strict=True):
        b = _hex_bytes(str(hexstr))
        if not b:
            continue
            
        # SYNC Packet: [0xA5][u32 sample_idx LE][u16 fs LE][u8 n_axes][u8 battery_percent]
        if b[0] == 0xA5 and len(b) >= 9:
            idx = struct.unpack_from("<I", b, 1)[0]
            fs = struct.unpack_from("<H", b, 5)[0]
            batt = b[8]
            syncs.append((t_phone, idx, fs, batt))
            
        # DATA Packet: [0x5A][u32 first_sample_idx LE][u8 n_samples][N * 12B payload]
        elif b[0] == 0x5A and len(b) >= 6:
            first = struct.unpack_from("<I", b, 1)[0]
            n = b[5]
            payload = b[6:6 + 12 * n]
            if len(payload) != 12 * n:
                # Malformed or truncated packet; skip.
                continue
            for k in range(n):
                sample_idx.append(first + k)
                raw.append(payload[12 * k:12 * (k + 1)])

    if not syncs:
        raise ValueError("no SYNC packets found — cannot fit clock model")

    # Decode raw IMU samples.
    # The firmware sends these as big-endian int16, directly from the MPU-6050 FIFO.
    arr = np.frombuffer(b"".join(raw), dtype=">i2").reshape(-1, 6)
    out = pd.DataFrame({
        "sample_idx": sample_idx,
        "ax": arr[:, 0] * ACC_SCALE, "ay": arr[:, 1] * ACC_SCALE, "az": arr[:, 2] * ACC_SCALE,
        "gx": arr[:, 3] * GYR_SCALE, "gy": arr[:, 4] * GYR_SCALE, "gz": arr[:, 5] * GYR_SCALE,
    }).drop_duplicates("sample_idx").sort_values("sample_idx").reset_index(drop=True)

    # Clock Model: t_phone (ns since epoch) = a + b * sample_idx.
    # We use a simple linear regression (polyfit) to find the best fit 'a' and 'b'.
    # This automatically corrects for differences in clock crystals between the ESP32 and phone.
    sync_t = np.array([t.value for t, _, _, _ in syncs], dtype=np.float64)  # ns
    sync_i = np.array([i for _, i, _, _ in syncs], dtype=np.float64)
    
    if len(syncs) >= 2:
        b, a = np.polyfit(sync_i, sync_t, 1)
    else:
        # If we only have one SYNC, we fall back to the nominal frequency sent by firmware.
        b = 1e9 / syncs[0][2]   # ns per sample
        a = sync_t[0] - b * sync_i[0]
        
    measured_fs = 1e9 / b
    
    # Calculate battery stats
    batt_levels = [batt for _, _, _, batt in syncs]
    batt_str = f"Batt: {batt_levels[0]}% -> {batt_levels[-1]}%" if batt_levels else "Batt: N/A"

    print(f"clock model: fs ≈ {measured_fs:.3f} Hz, "
          f"{len(syncs)} SYNCs, {len(out)} samples | {batt_str}")

    # Apply the linear model to compute reconstructed timestamps
    out["timestamp"] = pd.to_datetime(
        (a + b * out["sample_idx"].to_numpy()).astype("int64"), utc=True
    )
    
    # Interpolate battery levels across all samples based on sample_idx
    if len(syncs) > 0:
        sync_batt = np.array(batt_levels, dtype=np.float64)
        out["battery_pct"] = np.interp(out["sample_idx"], sync_i, sync_batt).round().astype(int)
    else:
        out["battery_pct"] = 0

    return out[["timestamp", "sample_idx", "battery_pct", "ax", "ay", "az", "gx", "gy", "gz"]]


if __name__ == "__main__":
    import sys
    parse(sys.argv[1]).to_csv(sys.argv[2], index=False)
    print(f"wrote {sys.argv[2]}")
