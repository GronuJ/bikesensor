# bikesensor

Geo-tagged bike vibration analysis. ESP32 + MPU-6050 over BLE, GPX from phone,
short-time FFTs aligned to ~2 m of road via clock-model + GPS interpolation.

## Hardware

- ESP32 (any dev board) + MPU-6050 over I²C (SDA=21, SCL=22, AD0→GND).
- iOS phone running [LightBlue](https://punchthrough.com/lightblue/) for BLE log capture.
- Phone records a GPX track in parallel (any GPX recorder app).

## Wire protocol

Service `0xFFE0`, characteristic `0xFFE1` (notify). Two packet types,
distinguished by first byte:

| Type | Bytes | Layout |
| --- | --- | --- |
| SYNC `0xA5` | 9 | `[0xA5][u32 sample_idx LE][u16 fs LE][u8 n_axes][u8 _]` |
| DATA `0x5A` | 6+12·N | `[0x5A][u32 first_sample_idx LE][u8 N][N × 6 × int16 BE]` |

IMU bytes are forwarded raw from the MPU-6050 FIFO (big-endian).
Defaults: fs = 250 Hz, N = 10 samples/packet, ±4 g, ±500 °/s.

## Spatial-accuracy strategy

1. **Sample timestamps** come from a linear fit `t_phone = a + b·sample_idx`
   over all SYNC packets — kills BLE jitter (~50–200 ms) and absorbs the
   ESP32-vs-phone clock offset.
2. **Position per FFT window** is **interpolated** from the 1 Hz GPX track
   onto each window's center timestamp — sub-meter along-track at constant
   speed, even though absolute lat/lon is bounded by phone-GPS noise (3–5 m).
3. **STFT** with 0.5 s Hann window, 0.1 s hop → 0.5 m hop spacing at 5 m/s.

## Pipeline

```bash
# 1. Flash firmware/bikesensor/bikesensor.ino to the ESP32.
# 2. Record a ride: GPX on phone, LightBlue logging characteristic 0xFFE1.
# 3. Export LightBlue log as CSV. Save GPX. Then:

uv run python src/merge.py path/to/track.gpx path/to/lightblue.csv data
uv run streamlit run src/dashboard.py
```

`merge.py` writes `data/imu.csv`, `data/windows.csv`, `data/track.csv`.
The dashboard shows a heatmap, a clickable map (click a point → see its
spectrum), and band energies along the route.
