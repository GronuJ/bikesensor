"""
Merge GPX track + IMU samples + STFT windows into one geo-tagged dataset.

Spatial-accuracy strategy: phone GPX is ~1 Hz, so we *interpolate* the
track onto each FFT window's center timestamp rather than nearest-joining.
At constant speed this gives sub-meter along-track accuracy even though
absolute lat/lon is bounded by phone-GPS noise (~3–5 m).

Outputs:
  data/imu.csv         per-sample IMU (from lightblue_parse)
  data/windows.csv     one row per FFT window with location + band energies
  data/track.csv       interpolated GPS for plotting
"""

from __future__ import annotations

from pathlib import Path

import gpxpy
import numpy as np
import pandas as pd

from src.analysis import stft_features
from src.lightblue_parse import parse as parse_lightblue


def _load_gpx(path: str | Path) -> pd.DataFrame:
    with open(path) as f:
        gpx = gpxpy.parse(f)
    rows = [
        {"timestamp": p.time, "lat": p.latitude, "lon": p.longitude, "ele": p.elevation}
        for trk in gpx.tracks for seg in trk.segments for p in seg.points
    ]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def _haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _enrich_track(gps: pd.DataFrame) -> pd.DataFrame:
    gps = gps.copy()
    seg = _haversine_m(gps["lat"].shift(), gps["lon"].shift(),
                       gps["lat"], gps["lon"])
    seg = np.nan_to_num(seg, nan=0.0)
    gps["cum_dist_m"] = np.cumsum(seg)
    dt = gps["timestamp"].diff().dt.total_seconds().fillna(1.0)
    gps["speed_mps"] = (seg / dt).clip(lower=0)
    gps["speed_kmh"] = gps["speed_mps"] * 3.6
    return gps


def _interp_to(track: pd.DataFrame, target_ts: pd.Series) -> pd.DataFrame:
    """Linearly interpolate track columns onto target timestamps."""
    src_ns = track["timestamp"].astype("int64").to_numpy()
    tgt_ns = target_ts.astype("int64").to_numpy()
    out = pd.DataFrame({"timestamp": target_ts.values})
    for col in ("lat", "lon", "ele", "cum_dist_m", "speed_mps", "speed_kmh"):
        if col in track.columns:
            out[col] = np.interp(tgt_ns, src_ns, track[col].to_numpy(),
                                 left=np.nan, right=np.nan)
    return out


def build(gpx_path: str | Path, lightblue_csv: str | Path,
          out_dir: str | Path = "data") -> dict[str, Path]:
    out_dir = Path(out_dir); out_dir.mkdir(exist_ok=True)
    imu = parse_lightblue(lightblue_csv)
    track = _enrich_track(_load_gpx(gpx_path))

    windows = stft_features(imu)
    geo = _interp_to(track, windows["timestamp"])
    windows = pd.concat([windows.reset_index(drop=True),
                         geo.drop(columns="timestamp").reset_index(drop=True)], axis=1)
    windows = windows.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    paths = {
        "imu": out_dir / "imu.csv",
        "windows": out_dir / "windows.csv",
        "track": out_dir / "track.csv",
    }
    imu.to_csv(paths["imu"], index=False)
    windows.to_csv(paths["windows"], index=False)
    track.to_csv(paths["track"], index=False)
    print(f"imu={len(imu)}  windows={len(windows)}  track={len(track)}")
    return paths


if __name__ == "__main__":
    import sys
    build(sys.argv[1], sys.argv[2],
          sys.argv[3] if len(sys.argv) > 3 else "data")
