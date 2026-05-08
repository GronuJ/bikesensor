# bikesensor

Merge GPS tracks (GPX from phone) with vibration data from an ESP32 + MPU-6050,
logged over BLE via LightBlue. Visualize vibration along the ride on a map.

## Pipeline

1. **Record** a ride: GPX on the phone + MPU-6050 stream over BLE captured by LightBlue.
2. **Decode** the LightBlue CSV export (hex payloads → ax, ay, az, gx, gy, gz):
   ```
   uv run python src/lightblue_parse.py path/to/lightblue.csv data/vibration.csv
   ```
   Adjust packing format / scale factors in `src/lightblue_parse.py` to match your firmware.
3. **Merge** GPX + vibration into one CSV (nearest-timestamp join, adds speed & vibration RMS):
   ```
   uv run python src/merge.py path/to/track.gpx data/vibration.csv data/merged.csv
   ```
4. **Dashboard**:
   ```
   uv run streamlit run src/dashboard.py -- data/merged.csv
   ```

## Notes

- Vibration metric: `|‖a‖ − 1g|` (gravity removed) and a rolling RMS over 500 ms — that's the "roughness" signal.
- Speed comes from haversine distance between successive GPS points.
- The merge tolerance is 2 s; vibration samples without a nearby GPS fix are dropped.
- LightBlue exports vary; the parser assumes a 12-byte payload of 6 little-endian int16s (MPU-6050 raw). Tune in `lightblue_parse.py`.
