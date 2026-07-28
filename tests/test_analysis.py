import numpy as np
import pandas as pd
from src.analysis import detect_curb_events, stft_features

def test_stft_features_sine_wave():
    fs = 250.0
    t = np.arange(0, 2.0, 1/fs)
    # Sine wave at 20 Hz, amplitude 1.0 (on the Z axis to mimic pure vibration)
    # Add 1.0 to mimic gravity on one axis so magnitude is roughly 1.0 + sine
    # Actually, the analysis code computes np.sqrt(ax^2+ay^2+az^2). 
    # Let's set ax=0, ay=0, az = 1.0 + 0.5 * sin(2*pi*20*t)
    az = 1.0 + 0.5 * np.sin(2 * np.pi * 20 * t)
    
    imu = pd.DataFrame({
        "timestamp": pd.to_datetime(t * 1e9, unit="ns", utc=True),
        "ax": np.zeros_like(t),
        "ay": np.zeros_like(t),
        "az": az
    })
    
    res = stft_features(imu)
    assert not res.empty
    
    # 20 Hz should fall into "band_mid_g" (10-30 Hz)
    assert res["band_mid_g"].mean() > res["band_low_g"].mean()
    assert res["band_mid_g"].mean() > res["band_high_g"].mean()
    
    # Peak Hz should be around 20 Hz
    # The resolution is 2 Hz (0.5s window)
    mean_peak = res["peak_hz"].mean()
    assert 18.0 <= mean_peak <= 22.0
    
    # RMS should be > 0
    assert res["rms_g"].mean() > 0.0


def test_stft_features_high_band_80hz():
    fs = 250.0
    t = np.arange(0, 2.0, 1 / fs)
    az = 1.0 + 0.35 * np.sin(2 * np.pi * 80 * t)

    imu = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(t * 1e9, unit="ns", utc=True),
            "ax": np.zeros_like(t),
            "ay": np.zeros_like(t),
            "az": az,
        }
    )
    res = stft_features(imu)

    assert not res.empty
    assert res["band_high_g"].mean() > res["band_mid_g"].mean()
    assert res["band_high_g"].mean() > res["band_low_g"].mean()


def test_detect_curb_events_deduplicates_nearby_windows():
    timestamps = pd.date_range("2026-01-01T10:00:00Z", periods=8, freq="100ms")
    windows = pd.DataFrame(
        {
            "timestamp": timestamps,
            "max_bump_g": [0.15, 1.72, 1.60, 0.32, 0.20, 0.18, 0.14, 0.12],
            "speed_kmh": [8.0] * 8,
            "cum_dist_m": [i * 0.6 for i in range(8)],
            "lat": [54.3] * 8,
            "lon": [10.1] * 8,
        }
    )

    events = detect_curb_events(
        windows,
        threshold_g=1.4,
        min_speed_kmh=1.0,
        max_speed_kmh=12.0,
        min_prominence_g=0.1,
        refractory_s=1.0,
        refractory_m=4.0,
    )
    assert len(events) == 1
    assert events["max_bump_g"].iloc[0] == 1.72


def test_detect_curb_events_keeps_separated_peaks():
    timestamps = pd.date_range("2026-01-01T10:00:00Z", periods=10, freq="1s")
    windows = pd.DataFrame(
        {
            "timestamp": timestamps,
            "max_bump_g": [0.2, 1.6, 0.2, 0.3, 0.25, 1.75, 0.25, 0.2, 0.15, 0.1],
            "speed_kmh": [9.0] * 10,
            "cum_dist_m": [i * 10.0 for i in range(10)],
            "lat": [54.3] * 10,
            "lon": [10.1] * 10,
        }
    )

    events = detect_curb_events(
        windows,
        threshold_g=1.4,
        min_speed_kmh=1.0,
        max_speed_kmh=12.0,
        min_prominence_g=0.1,
        refractory_s=1.0,
        refractory_m=4.0,
    )
    assert len(events) == 2
    assert list(events["event_id"]) == [1, 2]


def test_detect_curb_events_speed_gating_excludes_stationary():
    timestamps = pd.date_range("2026-01-01T10:00:00Z", periods=6, freq="500ms")
    windows = pd.DataFrame(
        {
            "timestamp": timestamps,
            "max_bump_g": [0.1, 1.8, 0.1, 0.1, 0.1, 0.1],
            "speed_kmh": [0.0] * 6,
            "cum_dist_m": [0.0] * 6,
            "lat": [54.3] * 6,
            "lon": [10.1] * 6,
        }
    )

    events = detect_curb_events(
        windows,
        threshold_g=1.4,
        min_speed_kmh=1.0,
        max_speed_kmh=12.0,
        min_prominence_g=0.1,
        refractory_s=1.0,
        refractory_m=4.0,
    )
    assert events.empty
