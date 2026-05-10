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
