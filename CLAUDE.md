# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`bikesensor` is a hardware + software pipeline for geo-tagged bike vibration / road-quality mapping. An ESP32-C3 SuperMini logs 200 Hz accelerometer data and 1 Hz GPS to a MicroSD card during a ride; on arriving home it joins Wi-Fi and uploads the CSVs over HTTPS.

**The backend is mid-migration.** The firmware now posts to `https://kiel.earth/api/bike/ingest` (see `SERVER_HOST` in `bikesensor.ino`), where Cloudflare Pages Functions store the raw CSV in R2 and the DSP runs browser-side. The Python here — FastAPI server, Streamlit dashboard, `src/merge.py`, `src/analysis.py` — is the **original Raspberry Pi implementation**, still the reference for the DSP but no longer the device's upload target. The README still documents the Pi setup end to end; treat its §3–§6 as legacy.

## Commands

Python is managed exclusively with `uv` (requires Python >= 3.13).

```bash
uv sync                                          # install deps incl. dev group
uv run pytest                                    # all tests
uv run pytest tests/test_merge.py                # one file
uv run pytest tests/test_merge.py::test_merge_offline    # one test
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000
uv run streamlit run src/dashboard.py            # dashboard on :8501
uv run python src/merge.py --gpx <files|dirs> --csv <files|dirs> --out data   # legacy BLE path
uv run python src/db.py                          # create/verify SQLite schema
```

`BIKESENSOR_DEV_RELOAD=1` enables uvicorn autoreload when running `python src/server.py` directly. Tests use `pythonpath = ["."]` (set in `pyproject.toml`), so `src.*` imports resolve from the repo root — always run pytest from there.

Firmware (PlatformIO, env `esp32-c3-supermini`):

```bash
pio run -d firmware              # build
pio run -d firmware -t upload    # flash
pio device monitor -b 115200     # serial console (native USB-CDC)
```

`platformio.ini` sets `src_dir = bikesensor` so the `.ino` compiles in place. `firmware/diagnostic/diagnostic.ino` is a separate standalone bring-up sketch — it is *not* in the PlatformIO build; swap `src_dir` to compile it. It currently only walks the SD card through 400 kHz / 1 MHz / 4 MHz. Earlier revisions probed I2C and discriminated a stuck bus from a solder short; recover them from git history (`1953733` and its parents) if a future build misbehaves.

## Architecture

### Data flow

```
ESP32 (SD CSV) --POST--> src/server.py --> src/merge.py --> src/analysis.py --> data/rides/<ride_id>/*.csv
                                                                             --> src/db.py (data/rides.db)
                                                                             --> src/dashboard.py
```

The unified on-SD CSV format is the contract between firmware and backend:

```
millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct,gps_time
```

`millis` is relative to ESP32 boot (100 Hz rows); GPS columns are only populated on ~1 Hz fix rows and are interpolated server-side. `ax/ay/az` are **raw MPU-6050 counts** — the ±4 g scale (`1/8192`) is applied in `merge.py`, not on-device. Changing the firmware's `ACCEL_CONFIG` register requires changing `ACC_SCALE` in both `merge.py` and `lightblue_parse.py`.

### Ingestion modes (`src/server.py`)

`POST /api/upload-offline` sniffs the CSV header and branches:
- headers contain `lat` and `lon` → **unified mode**: saved to `data/rides/ride_auto_<utc>/raw_imu.csv`, then `process_unified_offline` runs in a FastAPI `BackgroundTasks` job (STFT is too slow to hold the ESP32's HTTP connection open).
- otherwise → **pending mode**: written to `data/rides/pending_vibrations/` for a later manual GPX merge.

`POST /api/upload` is the legacy BLE path (raw GPX + LightBlue hex packets in JSON) and calls `build()`. Filenames from the `X-Ride-Filename` header are validated against `SAFE_RIDE_FILENAME_PATTERN` before touching the filesystem — keep that guard on any new upload route.

### The three merge entry points (`src/merge.py`)

All three produce the same triple — `imu.csv` (per-sample), `windows.csv` (one row per STFT window, with interpolated lat/lon), `track.csv` (enriched GPS) — and differ only in how they establish an absolute clock:

- `build(gpx_paths, csv_paths, out_dir)` — legacy BLE: timestamps come from `lightblue_parse`'s least-squares clock model over SYNC packets.
- `merge_offline(gpx, csv, out_dir)` — SD log without GPS; anchors `millis` to the GPX start time.
- `process_unified_offline(csv, out_dir)` — current path; anchors `millis` to the first satellite `gps_time`, falling back to system time with a warning.

The core spatial trick throughout: the GPS track is ~1 Hz while STFT windows are 10 Hz, so `_interp_to` **linearly interpolates** lat/lon/distance/speed onto each window's center timestamp rather than nearest-joining. Assumes roughly constant velocity between fixes.

`process_unified_offline` **raises** on a log with fewer than 2 GPS fixes rather than substituting coordinates. It used to fall back to a hardcoded Kiel coordinate (54.3486, 10.1176), which produced a real-looking ride at a place the bike never went — indistinguishable from genuine data. Keep that guard; `test_process_unified_offline_no_gps` pins it.

### DSP (`src/analysis.py`)

`stft_features` detrends accel magnitude (subtracting 1 g gravity), Hann-windows 0.5 s segments at 0.1 s hop, and integrates the one-sided PSD into `rms_g` plus three fixed bands (`band_low_g` 1–10 Hz body/suspension, `band_mid_g` 10–30 Hz frame, `band_high_g` 30–120 Hz tire texture). `max_bump_g` comes from a separate 25 Hz Butterworth low-pass of the same signal. `WIN_S`/`HOP_S`/`BANDS` are module-level constants — window length sets frequency resolution (0.5 s → Δf = 2 Hz) and hop sets spatial resolution (~0.5 m at 5 m/s).

`detect_curb_events` is deliberately more than a threshold: threshold + speed gate + local-maximum + prominence over an 11-window rolling median + time/distance refractory suppression. The speed gate exists because stationary shocks and fast rides both produce false positives.

### Storage

`data/rides.db` (SQLite, schema in `src/db.py`) stores only ride metadata plus a `file_path` pointing at the ride directory; the actual time series live as CSVs on disk. The dashboard joins the two. All of `data/` is gitignored.

## Hardware

`custom_pcb/` is a passive carrier board. Two facts that are not visible in the files:

- **`production/` is stale.** Those gerbers are the pre-2026-09-17 board (72.75 × 167.43 mm). The
  current `.kicad_pcb` is 38.74 × 114.47 mm with different footprint spacing. Re-plot before ordering.
- **Two known open issues** are listed at the bottom of `custom_pcb/PIN_VERIFICATION.md`: `+3V3`
  does not reach C1 pin 1, and the CS pull-up (R3) is not in the design. Read that file before
  touching the board.

KiCad is not installed on this machine, so DRC cannot be run here. Connectivity can still be checked
by parsing the `.kicad_pcb` directly — the netlist is authoritative, the schematic's generic
`Conn_01x0N_Socket` symbols are not.

## Gotchas

- `README.md` §7.3 describes a dashboard GPX-upload UI for pending-mode rides. That UI is not currently in `src/dashboard.py` (which only imports `merge_build` and never calls it) — pending files accumulate unprocessed.
- Ride data under `data/` is irreplaceable and gitignored. Regenerate derived CSVs from `raw_imu.csv` rather than deleting ride directories. The dashboard's "Clear All Rides" button `rmtree`s every ride directory.
- `firmware/bikesensor/private_credentials.h` (Wi-Fi SSID/pass, server host/port/path) is gitignored; firmware falls back to placeholder defines via `__has_include`. Never commit it.
- Timestamps are UTC end to end; only the dashboard converts to `Europe/Berlin` for display.
- `GEMINI.md` is a stale context file describing the old BLE/LightBlue-only architecture. It predates the SD + Wi-Fi standalone design and should not be trusted.
