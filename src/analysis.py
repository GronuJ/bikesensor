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
from scipy.signal import detrend, get_window

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
    dt = np.median(np.diff(timestamps.astype("int64"))) / 1e9
    fs = 1.0 / dt
    win_n = max(8, int(round(WIN_S * fs)))
    hop_n = max(1, int(round(HOP_S * fs)))
    return StftConfig(fs=fs, win_n=win_n, hop_n=hop_n)


def stft_features(imu: pd.DataFrame) -> pd.DataFrame:
    """Compute per-window band energies and broadband RMS from accel magnitude."""
    cfg = _config(imu["timestamp"])
    accel_mag = np.sqrt(imu["ax"] ** 2 + imu["ay"] ** 2 + imu["az"] ** 2).to_numpy()
    # Remove gravity + slow drift; FFT cares about the AC component.
    sig = detrend(accel_mag - 1.0, type="constant")
    win = get_window("hann", cfg.win_n)
    win_energy = (win ** 2).sum()
    freqs = np.fft.rfftfreq(cfg.win_n, d=1.0 / cfg.fs)

    starts = np.arange(0, len(sig) - cfg.win_n + 1, cfg.hop_n)
    rows = []
    ts_ns = imu["timestamp"].astype("int64").to_numpy()

    for s in starts:
        seg = sig[s:s + cfg.win_n] * win
        # Power spectral density (Welch-style scaling, single segment).
        psd = (np.abs(np.fft.rfft(seg)) ** 2) / (cfg.fs * win_energy)
        psd[1:-1] *= 2  # one-sided

        row = {
            "t_center_ns": int(ts_ns[s + cfg.win_n // 2]),
            "rms_g": float(np.sqrt(np.trapezoid(psd, freqs))),
        }
        for name, (lo, hi) in BANDS.items():
            m = (freqs >= lo) & (freqs < hi)
            row[name] = float(np.sqrt(np.trapezoid(psd[m], freqs[m]))) if m.any() else 0.0
        # Dominant frequency (ignore <1 Hz which is mostly residual gravity drift).
        m = freqs >= 1.0
        row["peak_hz"] = float(freqs[m][np.argmax(psd[m])]) if m.any() else 0.0
        rows.append(row)

    out = pd.DataFrame(rows)
    out["timestamp"] = pd.to_datetime(out["t_center_ns"], utc=True)
    out["win_n"] = cfg.win_n
    out["hop_n"] = cfg.hop_n
    out["fs_hz"] = cfg.fs
    return out.drop(columns="t_center_ns")
