"""
Short-time Fourier analysis of vibration, one row per overlapping window.

Defaults: 0.5 s window (Δf = 2 Hz), 0.1 s hop. At ~5 m/s this gives
~0.5 m hop spacing — well below the 2 m target — and each window
spatially blurs over ~2.5 m. Tune WIN_S / HOP_S if you ride faster.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import butter, detrend, filtfilt, get_window

WIN_S = 0.5
HOP_S = 0.1
BANDS = {                      # Hz; tuned for road / bike vibration
    "band_low_g":  (1, 10),    # body / suspension
    "band_mid_g":  (10, 30),   # frame / fork
    "band_high_g": (30, 120),  # tire-surface texture
}


@dataclass
class StftConfig:
    fs: float
    win_n: int
    hop_n: int


def _config(timestamps: pd.Series) -> StftConfig:
    """
    Determines the sampling rate and window parameters based on the data.
    
    The sampling rate (fs) is estimated from the median time delta between samples.
    Window and hop sizes are converted from seconds to number of samples.
    """
    dt = np.median(np.diff(timestamps.astype("int64"))) / 1e9
    fs = 1.0 / dt
    win_n = max(8, int(round(WIN_S * fs)))
    hop_n = max(1, int(round(HOP_S * fs)))
    return StftConfig(fs=fs, win_n=win_n, hop_n=hop_n)


def stft_features(imu: pd.DataFrame) -> pd.DataFrame:
    """
    Computes per-window band energies and broadband RMS from acceleration magnitude.
    
    This function performs a Short-Time Fourier Transform (STFT) on the 
    acceleration magnitude signal. The 'features' extracted for each window 
    include the overall RMS vibration (g) and the RMS vibration within 
    specific frequency bands (low, mid, high).
    """
    cfg = _config(imu["timestamp"])
    
    # Calculate acceleration magnitude (scalar)
    accel_mag = np.sqrt(imu["ax"] ** 2 + imu["ay"] ** 2 + imu["az"] ** 2).to_numpy()
    
    # Preprocessing: Remove gravity (1.0g) and slow-moving drift. 
    # The FFT analysis should only consider the 'AC' vibration component.
    sig = detrend(accel_mag - 1.0, type="constant")
    
    # Setup Hanning window for spectral analysis
    win = get_window("hann", cfg.win_n)
    win_energy = (win ** 2).sum()
    freqs = np.fft.rfftfreq(cfg.win_n, d=1.0 / cfg.fs)

    # Pre-calculate 25Hz low-pass filtered signal for bump severity
    cutoff_hz = 25.0
    nyq = 0.5 * cfg.fs
    normal_cutoff = cutoff_hz / nyq
    b, a = butter(4, normal_cutoff, btype='low', analog=False)
    sig_filtered = filtfilt(b, a, sig)

    # Slide the window across the signal with a fixed hop size
    starts = np.arange(0, len(sig) - cfg.win_n + 1, cfg.hop_n)
    rows = []
    ts_ns = imu["timestamp"].astype("int64").to_numpy()

    for s in starts:
        # Extract and window the segment
        seg = sig[s:s + cfg.win_n] * win
        
        # Power spectral density (PSD) calculation
        # We use the periodogram estimate with Welch-style scaling for a single segment.
        psd = (np.abs(np.fft.rfft(seg)) ** 2) / (cfg.fs * win_energy)
        psd[1:-1] *= 2  # Double the power for one-sided FFT (except DC and Nyquist)

        # RMS is the square root of the integral of the PSD across the frequency range.
        row = {
            "t_center_ns": int(ts_ns[s + cfg.win_n // 2]),
            "rms_g": float(np.sqrt(np.trapezoid(psd, freqs))),
            "max_bump_g": float(np.max(np.abs(sig_filtered[s:s + cfg.win_n]))),
        }
        
        # Integrate PSD within defined frequency bands to get band-specific RMS.
        for name, (lo, hi) in BANDS.items():
            m = (freqs >= lo) & (freqs < hi)
            row[name] = float(np.sqrt(np.trapezoid(psd[m], freqs[m]))) if m.any() else 0.0
            
        # Identify the peak frequency (dominant vibration mode).
        # We ignore frequencies below 1Hz as they often contain residual low-frequency noise.
        m = freqs >= 1.0
        row["peak_hz"] = float(freqs[m][np.argmax(psd[m])]) if m.any() else 0.0
        rows.append(row)

    out = pd.DataFrame(rows)
    out["timestamp"] = pd.to_datetime(out["t_center_ns"], utc=True)
    out["win_n"] = cfg.win_n
    out["hop_n"] = cfg.hop_n
    out["fs_hz"] = cfg.fs
    return out.drop(columns="t_center_ns")
