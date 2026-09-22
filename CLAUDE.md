# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`bikesensor` is a hardware + software pipeline for geo-tagged bike vibration / road-quality mapping. An ESP32-C3 SuperMini logs 200 Hz accelerometer data and 1 Hz GPS to a MicroSD card during a ride; on arriving home it joins Wi-Fi and uploads the CSVs over HTTPS.

The device posts each ride to `https://kiel.earth/api/bike/ingest` (see `SERVER_HOST` in `bikesensor.ino`). Storage, processing and the map live in the separate `kiel-earth` repo, not here. This repo is only the firmware, the carrier PCB and the enclosure. The original Raspberry Pi backend (FastAPI + Streamlit + Python DSP) was removed; recover it from git history (last present in `f155d5b`) if the old DSP is ever needed as a reference.

## Commands

Firmware (PlatformIO, env `esp32-c3-supermini`):

```bash
pio run -d firmware              # build
pio run -d firmware -t upload    # flash
pio device monitor -b 115200     # serial console (native USB-CDC)
```

`platformio.ini` sets `src_dir = bikesensor` so the `.ino` compiles in place. `firmware/diagnostic/diagnostic.ino` is a separate standalone bring-up sketch — it is *not* in the PlatformIO build; swap `src_dir` to compile it. It currently only walks the SD card through 400 kHz / 1 MHz / 4 MHz. Earlier revisions probed I2C and discriminated a stuck bus from a solder short; recover them from git history (`1953733` and its parents) if a future build misbehaves.

## Firmware ↔ kiel.earth contract

The on-SD CSV format is the contract with the website:

```
millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct,gps_time
```

`millis` is relative to ESP32 boot; GPS columns are only populated on ~1 Hz fix rows. `ax/ay/az` are **raw MPU-6050 counts** — the ±4 g scale (`1/8192`) is applied downstream, not on-device, so changing the firmware's `ACCEL_CONFIG` register means changing the scale in `kiel-earth` too.

Upload: `POST` with `Content-Type: text/csv`, `Authorization: Bearer <DEVICE_TOKEN>` and `X-Ride-Id` (the filename without extension). The firmware deletes a file from the SD card only on a 2xx, so the server must store the raw bytes before acknowledging, and must treat a repeated `X-Ride-Id` as a duplicate rather than a new ride.

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

- `firmware/bikesensor/private_credentials.h` (Wi-Fi SSID/pass, server host/port/path, device token) is gitignored; firmware falls back to placeholder defines via `__has_include`. Never commit it.
- `GEMINI.md` is a stale, gitignored context file describing the old BLE/LightBlue-only architecture. It should not be trusted.
