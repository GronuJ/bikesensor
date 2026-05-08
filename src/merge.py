"""
Merge a GPX track from your phone with vibration data from the ESP32/MPU-6050.

Strategy:
  - Load GPX -> DataFrame of (timestamp, lat, lon, elevation).
  - Compute speed (m/s) from successive GPS points (haversine / Δt).
  - Load vibration CSV (timestamp, ax, ay, az, gx, gy, gz).
  - Compute vibration magnitude = sqrt(ax² + ay² + az²) - 1g (gravity-removed),
    plus a rolling RMS as a smoother "roughness" signal.
  - Merge by nearest timestamp (vibration is high-rate, GPS ~1 Hz, so we
    align each vibration sample to its closest GPS fix within a tolerance).
  - Output one row per vibration sample with location, speed, vibration.

Why merge_asof + nearest: GPS and IMU clocks are independent and sampled
at different rates. `merge_asof(direction="nearest")` is the standard
pandas idiom for time-based joins; it's O(n+m) on sorted timestamps.
"""

from __future__ import annotations

from pathlib import Path

import gpxpy
import numpy as np
import pandas as pd


def load_gpx(path: str | Path) -> pd.DataFrame:
    with open(path) as f:
        gpx = gpxpy.parse(f)
    rows = [
        {"timestamp": p.time, "lat": p.latitude, "lon": p.longitude, "ele": p.elevation}
        for trk in gpx.tracks for seg in trk.segments for p in seg.points
    ]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def add_speed(gps: pd.DataFrame) -> pd.DataFrame:
    gps = gps.copy()
    dt = gps["timestamp"].diff().dt.total_seconds()
    dist = haversine_m(
        gps["lat"].shift(), gps["lon"].shift(), gps["lat"], gps["lon"]
    )
    gps["speed_mps"] = (dist / dt).fillna(0.0)
    gps["speed_kmh"] = gps["speed_mps"] * 3.6
    return gps


def add_vibration_features(vib: pd.DataFrame, rms_window: str = "500ms") -> pd.DataFrame:
    vib = vib.sort_values("timestamp").copy()
    accel_mag = np.sqrt(vib["ax"] ** 2 + vib["ay"] ** 2 + vib["az"] ** 2)
    vib["accel_mag_g"] = accel_mag
    # Gravity-removed AC component — what you actually feel as "vibration".
    vib["vib_g"] = (accel_mag - 1.0).abs()
    s = vib.set_index("timestamp")["vib_g"]
    vib["vib_rms_g"] = (
        s.pow(2).rolling(rms_window).mean().pow(0.5).to_numpy()
    )
    return vib


def merge_streams(
    gps: pd.DataFrame, vib: pd.DataFrame, tolerance: str = "2s"
) -> pd.DataFrame:
    gps = gps.sort_values("timestamp")
    vib = vib.sort_values("timestamp")
    merged = pd.merge_asof(
        vib, gps, on="timestamp", direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    return merged.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def build(
    gpx_path: str | Path, vibration_csv: str | Path, out_path: str | Path
) -> pd.DataFrame:
    gps = add_speed(load_gpx(gpx_path))
    vib = pd.read_csv(vibration_csv, parse_dates=["timestamp"])
    if vib["timestamp"].dt.tz is None:
        vib["timestamp"] = vib["timestamp"].dt.tz_localize("UTC")
    vib = add_vibration_features(vib)
    merged = merge_streams(gps, vib)
    merged.to_csv(out_path, index=False)
    print(f"merged {len(merged)} rows -> {out_path}")
    return merged


if __name__ == "__main__":
    import sys
    build(sys.argv[1], sys.argv[2], sys.argv[3])
