import numpy as np
import pandas as pd
from src.analysis import stft_features

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
