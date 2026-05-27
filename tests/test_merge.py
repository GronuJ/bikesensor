import numpy as np
import pandas as pd
from src.merge import _haversine_m, _enrich_track, _interp_to

def test_haversine_m():
    # San Francisco to Los Angeles roughly
    d = _haversine_m(37.7749, -122.4194, 34.0522, -118.2437)
    assert 550000 < d < 570000

def test_enrich_track():
    times = pd.date_range("2023-01-01", periods=3, freq="1s", tz="UTC")
    # Move north by roughly 1 degree (111 km) per second -> extremely fast
    gps = pd.DataFrame({
        "timestamp": times,
        "lat": [0.0, 1.0, 2.0],
        "lon": [0.0, 0.0, 0.0],
        "ele": [10.0, 10.0, 10.0]
    })
    enriched = _enrich_track(gps)
    assert "cum_dist_m" in enriched.columns
    assert "speed_mps" in enriched.columns
    
    # First point has dist 0
    assert enriched["cum_dist_m"].iloc[0] == 0.0
    assert enriched["speed_mps"].iloc[0] == 0.0
    
    # Second point distance
    d = enriched["cum_dist_m"].iloc[1]
    assert 111000 < d < 112000
    assert enriched["speed_mps"].iloc[1] == d # 1 second diff

def test_interp_to():
    track = pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01T00:00:00", periods=3, freq="2s", tz="UTC"),
        "lat": [0.0, 2.0, 4.0],
        "speed_mps": [0.0, 10.0, 20.0]
    })
    # Target in between the first and second point (at 1s)
    targets = pd.Series(pd.date_range("2023-01-01T00:00:01", periods=1, tz="UTC"))
    interp = _interp_to(track, targets)
    
    assert len(interp) == 1
    assert interp["lat"].iloc[0] == 1.0
    assert interp["speed_mps"].iloc[0] == 5.0

def test_merge_offline():
    import tempfile
    import shutil
    from pathlib import Path
    from src.merge import merge_offline

    # 1. Create a dummy GPX file
    gpx_content = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
      <trk>
        <trkseg>
          <trkpt lat="54.3" lon="10.1"><time>2023-01-01T12:00:00Z</time></trkpt>
          <trkpt lat="54.4" lon="10.2"><time>2023-01-01T12:00:10Z</time></trkpt>
        </trkseg>
      </trk>
    </gpx>"""

    # 2. Create a dummy offline CSV file (needs at least 125 samples for a 0.5s STFT @ 250Hz nominal)
    # We will pretend it's 100Hz so we need at least 50 samples (0.5s win_n = 50). Let's generate 100 rows.
    csv_rows = ["millis,ax,ay,az"]
    for i in range(100):
        # 10ms increments (100Hz)
        csv_rows.append(f"{i*10},0,0,8192")
    csv_content = "\n".join(csv_rows)

    with tempfile.NamedTemporaryFile(suffix=".gpx", mode="w", delete=False) as gpx_f:
        gpx_f.write(gpx_content)
        gpx_path = gpx_f.name

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as csv_f:
        csv_f.write(csv_content)
        csv_path = csv_f.name

    out_dir = Path(tempfile.mkdtemp())

    try:
        paths = merge_offline(gpx_path, csv_path, out_dir)
        
        assert (out_dir / "imu.csv").exists()
        assert (out_dir / "windows.csv").exists()
        assert (out_dir / "track.csv").exists()

        imu_df = pd.read_csv(paths["imu"])
        assert len(imu_df) == 100
        assert imu_df["timestamp"].iloc[0] == "2023-01-01 12:00:00+00:00"
        
        # Verify scaling: 8192 / 8192 = 1.0g
        assert imu_df["az"].iloc[0] == 1.0

    finally:
        Path(gpx_path).unlink()
        Path(csv_path).unlink()
        shutil.rmtree(out_dir)

def test_process_unified_offline():
    import tempfile
    import shutil
    from pathlib import Path
    from src.merge import process_unified_offline

    # Create dummy in-band GPS + Accel CSV data (100 rows)
    csv_rows = ["millis,ax,ay,az,lat,lon,ele,speed_kmh"]
    for i in range(100):
        # 10ms increments (100Hz)
        millis = i * 10
        if i == 0:
            csv_rows.append(f"{millis},0,0,8192,54.3,10.1,12.0,15.0")
        elif i == 99:
            csv_rows.append(f"{millis},0,0,8192,54.4,10.2,12.0,15.0")
        else:
            csv_rows.append(f"{millis},0,0,8192,,,,")
    csv_content = "\n".join(csv_rows)

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as csv_f:
        csv_f.write(csv_content)
        csv_path = csv_f.name

    out_dir = Path(tempfile.mkdtemp())

    try:
        paths = process_unified_offline(csv_path, out_dir)
        
        assert (out_dir / "imu.csv").exists()
        assert (out_dir / "windows.csv").exists()
        assert (out_dir / "track.csv").exists()

        imu_df = pd.read_csv(paths["imu"])
        assert len(imu_df) == 100
        
        # Verify GPS interpolation worked: row 50 should have an interpolated lat/lon
        assert not pd.isna(imu_df["lat"].iloc[50])
        assert 54.3 < imu_df["lat"].iloc[50] < 54.4
        
        # Verify raw accel was scaled by 8192: 8192 / 8192 = 1g
        assert imu_df["az"].iloc[0] == 1.0

        # Verify GPS track subset was extracted
        track_df = pd.read_csv(paths["track"])
        assert len(track_df) == 2  # The 2 points we gave it
        assert track_df["lat"].iloc[0] == 54.3
        assert track_df["lat"].iloc[1] == 54.4

    finally:
        Path(csv_path).unlink()
        shutil.rmtree(out_dir)


