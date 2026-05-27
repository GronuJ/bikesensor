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
    """
    Parses a GPX file and extracts timestamped GPS points.
    
    Returns a DataFrame with columns [timestamp, lat, lon, ele] sorted by time.
    """
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
    """
    Computes the great-circle distance between two points on Earth in meters.
    
    Uses the Haversine formula, which is robust for small distances and 
    assumes a spherical Earth with radius R = 6371km.
    """
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _enrich_track(gps: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates cumulative distance, speed, and speed in km/h from raw GPS points.
    
    This provides the necessary spatial context for the vibration data, 
    allowing us to map vibration to distance along the track.
    """
    gps = gps.copy()
    # Calculate distance between consecutive points
    seg = _haversine_m(gps["lat"].shift(), gps["lon"].shift(),
                       gps["lat"], gps["lon"])
    seg = np.nan_to_num(seg, nan=0.0)
    gps["cum_dist_m"] = np.cumsum(seg)
    
    # Calculate speed (mps and kmh)
    dt = gps["timestamp"].diff().dt.total_seconds().fillna(1.0)
    gps["speed_mps"] = (seg / dt).clip(lower=0)
    gps["speed_kmh"] = gps["speed_mps"] * 3.6
    return gps


def _interp_to(track: pd.DataFrame, target_ts: pd.Series) -> pd.DataFrame:
    """
    Linearly interpolate track columns onto target timestamps.
    
    Since the GPX data is typically sampled at 1Hz and the FFT windows are 
    calculated more frequently (e.g., 10Hz), we interpolate the GPS coordinates 
    and distance metrics to the center of each FFT window. 
    
    This 'up-sampling' of the GPS track assumes a constant velocity between 
    GPS fixes, providing a much smoother spatial mapping of vibration.
    """
    # Convert timestamps to nanoseconds for numpy interpolation
    src_ns = pd.to_datetime(track["timestamp"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    tgt_ns = pd.to_datetime(target_ts, utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    
    out = pd.DataFrame({"timestamp": target_ts.values})
    for col in ("lat", "lon", "ele", "cum_dist_m", "speed_mps", "speed_kmh"):
        if col in track.columns:
            # Interpolate each metric onto the target timestamps
            out[col] = np.interp(tgt_ns, src_ns, track[col].to_numpy(),
                                 left=np.nan, right=np.nan)
    return out


def build(gpx_paths: list[str | Path], csv_paths: list[str | Path],
          out_dir: str | Path = "data") -> dict[str, Path]:
    """
    The main pipeline for merging raw sensor data and GPS tracks.
    
    1. Parses all LightBlue IMU CSVs and computes STFT features.
    2. Parses all GPX tracks and calculates cumulative distance.
    3. Interpolates the GPS track onto the STFT window center timestamps.
    4. Saves the results as three CSV files: imu.csv, windows.csv, and track.csv.
    """
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


def merge_offline(gpx_path: str | Path, offline_csv_path: str | Path,
                  out_dir: str | Path = "data") -> dict[str, Path]:
    """
    Integrates a raw offline SD card vibration log (recorded in relative milliseconds)
    with a GPX track file.
    
    1. Loads the GPX track to find the absolute start time.
    2. Loads the offline SD card CSV and maps its relative 'millis' column to absolute timestamps.
    3. Scales the raw MPU-6050 accelerometer counts to standard g units.
    4. Computes STFT features and interpolates the GPS track onto the vibration time windows.
    5. Saves the results as completed imu.csv, windows.csv, and track.csv.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True, parents=True)

    # 1. Load and enrich the GPX track
    print(f"Loading GPX Track: {gpx_path}")
    raw_track = _load_gpx(gpx_path)
    if raw_track.empty:
        raise ValueError("Provided GPX track is empty.")
    
    track_concat = _enrich_track(raw_track)
    gpx_start_time = track_concat["timestamp"].min()

    # 2. Load the offline sensor data
    print(f"Loading Offline Vibration Data: {offline_csv_path}")
    imu = pd.read_csv(offline_csv_path)
    if imu.empty:
        raise ValueError("Provided offline vibration CSV is empty.")

    # 3. Align the relative millis timeline with the absolute GPX start time
    first_ms = imu["millis"].iloc[0]
    dt_sec = (imu["millis"] - first_ms) / 1000.0
    imu["timestamp"] = gpx_start_time + pd.to_timedelta(dt_sec, unit="s")

    # 4. Scale raw sensor values to standard gravity (g)
    ACC_SCALE = 1.0 / 8192.0 # ±4g range scale
    imu["ax"] = imu["ax"] * ACC_SCALE
    imu["ay"] = imu["ay"] * ACC_SCALE
    imu["az"] = imu["az"] * ACC_SCALE
    
    # Offline logger saves space by skipping Gyro, fill with zeros
    imu["gx"] = 0.0
    imu["gy"] = 0.0
    imu["gz"] = 0.0
    imu["sample_idx"] = range(len(imu))

    # 5. Run STFT DSP windowing
    print("Executing STFT window analytics...")
    win_concat = stft_features(imu)

    # 6. Interpolate spatial coordinate points onto vibration timeframes
    print("Interpolating GPS track coordinates to timeframes...")
    geo = _interp_to(track_concat, win_concat["timestamp"])
    windows = pd.concat([win_concat.reset_index(drop=True),
                         geo.drop(columns="timestamp").reset_index(drop=True)], axis=1)
    windows = windows.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    paths = {
        "imu": out_dir / "imu.csv",
        "windows": out_dir / "windows.csv",
        "track": out_dir / "track.csv",
    }
    imu.to_csv(paths["imu"], index=False)
    windows.to_csv(paths["windows"], index=False)
    track_concat.to_csv(paths["track"], index=False)
    
    print(f"Total offline merged: imu={len(imu)}  windows={len(windows)}  track={len(track_concat)}")
    return paths


def process_unified_offline(offline_csv_path: str | Path,
                            out_dir: str | Path = "data") -> dict[str, Path]:
    """
    Processes a unified, pre-synced offline ride vibration log (which already contains
    integrated GPS coordinates logged in-band by the NEO-6M module).
    
    1. Loads the CSV, scaling raw MPU-6050 counts to g's.
    2. Interpolates the sparse 1Hz GPS coordinates onto the 100Hz vibration rows.
    3. Extracts and enriches the GPS track for distance/speed calculations.
    4. Computes STFT features and aligns coordinate sets to the timeframes.
    5. Saves the output dataset files.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True, parents=True)

    print(f"Loading Unified Standalone Ride Log: {offline_csv_path}")
    imu = pd.read_csv(offline_csv_path)
    if "lat" not in imu.columns or "lon" not in imu.columns:
        raise ValueError("Provided CSV does not contain integrated GPS 'lat' and 'lon' columns.")

    # 1. Assign absolute timestamps starting from current time
    start_time = pd.Timestamp.now(tz="UTC")
    first_ms = imu["millis"].iloc[0]
    dt_sec = (imu["millis"] - first_ms) / 1000.0
    imu["timestamp"] = start_time + pd.to_timedelta(dt_sec, unit="s")

    # 2. Scale raw accelerometer counts
    ACC_SCALE = 1.0 / 8192.0 # ±4g range scale
    imu["ax"] = imu["ax"] * ACC_SCALE
    imu["ay"] = imu["ay"] * ACC_SCALE
    imu["az"] = imu["az"] * ACC_SCALE
    
    # Offline logger saves space by skipping Gyro, fill with zeros
    imu["gx"] = 0.0
    imu["gy"] = 0.0
    imu["gz"] = 0.0
    imu["sample_idx"] = range(len(imu))

    # 3. Extract and enrich the GPS track subset
    gps_fixes = imu.dropna(subset=["lat"]).copy()
    if len(gps_fixes) < 2:
         raise ValueError("Unified ride log does not contain enough valid GPS fixes (need at least 2).")
    
    gps_raw = gps_fixes[["timestamp", "lat", "lon", "ele"]].copy()
    track_concat = _enrich_track(gps_raw)

    # 4. In-band linear interpolation for empty coordinates in the high-frequency IMU rows
    imu[["lat", "lon", "ele", "speed_kmh"]] = imu[["lat", "lon", "ele", "speed_kmh"]].interpolate(method="linear").ffill().bfill()

    # 5. Run STFT DSP windowing
    print("Executing STFT window analytics...")
    win_concat = stft_features(imu)

    # 6. Interpolate GPS track metrics onto timeframes
    print("Interpolating GPS track coordinates to timeframes...")
    geo = _interp_to(track_concat, win_concat["timestamp"])
    windows = pd.concat([win_concat.reset_index(drop=True),
                         geo.drop(columns="timestamp").reset_index(drop=True)], axis=1)
    windows = windows.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    paths = {
        "imu": out_dir / "imu.csv",
        "windows": out_dir / "windows.csv",
        "track": out_dir / "track.csv",
    }
    imu.to_csv(paths["imu"], index=False)
    windows.to_csv(paths["windows"], index=False)
    track_concat.to_csv(paths["track"], index=False)
    
    print(f"Total unified offline processed: imu={len(imu)}  windows={len(windows)}  track={len(track_concat)}")
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
