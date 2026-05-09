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

import argparse
import sys
from pathlib import Path

# Add project root to sys.path so we can import from src.* when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gpxpy
import numpy as np
import pandas as pd

from src.analysis import stft_features
from src.lightblue_parse import parse as parse_lightblue


def _load_gpx(path: str | Path) -> pd.DataFrame:
    with open(path) as f:
        gpx = gpxpy.parse(f)
    rows = [
        {"timestamp": p.time, "lat": float(p.latitude) if p.latitude is not None else np.nan, "lon": float(p.longitude) if p.longitude is not None else np.nan, "ele": float(p.elevation) if p.elevation is not None else np.nan}
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
    src_ns = pd.to_datetime(track["timestamp"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    tgt_ns = pd.to_datetime(target_ts, utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    out = pd.DataFrame({"timestamp": target_ts.values})
    for col in ("lat", "lon", "ele", "cum_dist_m", "speed_mps", "speed_kmh"):
        if col in track.columns:
            out[col] = np.interp(tgt_ns, src_ns, track[col].to_numpy(),
                                 left=np.nan, right=np.nan)
    return out


def build(gpx_paths: list[str | Path], csv_paths: list[str | Path],
          out_dir: str | Path = "data") -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True, parents=True)
    
    if not gpx_paths:
        raise ValueError("No GPX files provided.")
    if not csv_paths:
        raise ValueError("No CSV files provided.")

    # Process all IMU CSVs
    imus = []
    windows_list = []
    for p in csv_paths:
        print(f"Parsing LightBlue CSV: {p}")
        imu = parse_lightblue(p)
        imus.append(imu)
        windows_list.append(stft_features(imu))
    
    imu_concat = pd.concat(imus).sort_values("timestamp").reset_index(drop=True)
    win_concat = pd.concat(windows_list).sort_values("timestamp").reset_index(drop=True)

    # Process all GPX tracks
    tracks = []
    dist_offset = 0.0
    for p in gpx_paths:
        print(f"Parsing GPX: {p}")
        trk = _enrich_track(_load_gpx(p))
        if not trk.empty:
            trk["cum_dist_m"] += dist_offset
            dist_offset = trk["cum_dist_m"].max()
            tracks.append(trk)
            
    if not tracks:
        raise ValueError("No valid GPS points found in GPX files.")

    track_concat = pd.concat(tracks).sort_values("timestamp").reset_index(drop=True)

    # Interpolate track to windows
    geo = _interp_to(track_concat, win_concat["timestamp"])
    windows = pd.concat([win_concat.reset_index(drop=True),
                         geo.drop(columns="timestamp").reset_index(drop=True)], axis=1)
    windows = windows.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    paths = {
        "imu": out_dir / "imu.csv",
        "windows": out_dir / "windows.csv",
        "track": out_dir / "track.csv",
    }
    imu_concat.to_csv(paths["imu"], index=False)
    windows.to_csv(paths["windows"], index=False)
    track_concat.to_csv(paths["track"], index=False)
    print(f"Total merged: imu={len(imu_concat)}  windows={len(windows)}  track={len(track_concat)}")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge GPX and LightBlue IMU CSV files.")
    parser.add_argument("--gpx", nargs="+", required=True, help="One or more GPX files, or directories containing GPX files.")
    parser.add_argument("--csv", nargs="+", required=True, help="One or more LightBlue CSV files, or directories containing them.")
    parser.add_argument("--out", default="data", help="Output directory (default: data)")
    args = parser.parse_args()

    # Expand directories
    gpx_files = []
    for p in args.gpx:
        path = Path(p)
        if path.is_dir():
            gpx_files.extend(path.rglob("*.gpx"))
        else:
            gpx_files.append(path)
            
    csv_files = []
    for p in args.csv:
        path = Path(p)
        if path.is_dir():
            csv_files.extend(path.rglob("*.csv"))
            csv_files.extend(path.rglob("*.txt"))
        else:
            csv_files.append(path)

    build(gpx_files, csv_files, args.out)
