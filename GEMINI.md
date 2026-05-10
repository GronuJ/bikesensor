# bikesensor - Gemini AI Context

This file provides context for the Gemini AI agent to understand the `bikesensor` project.

## Project Overview

**bikesensor** is a hardware and software project for geo-tagged bike vibration analysis. It pairs an ESP32-C3 SuperMini and an MPU-6050 IMU communicating over BLE with a phone. The phone records a GPX track while logging BLE data via the LightBlue app. The Python pipeline then merges the BLE IMU data with the GPX track, applying short-time Fourier transforms (STFT) aligned via a clock-model and GPS interpolation to analyze road vibrations.

### Key Technologies
*   **Hardware:** ESP32-C3 SuperMini, MPU-6050 IMU.
*   **Firmware:** C++/Arduino managed with PlatformIO.
*   **Data Pipeline & Analysis:** Python, `uv` for dependency management.
*   **Data Processing:** `pandas`, `numpy`, `scipy`, `gpxpy`.
*   **Visualization:** Streamlit, `folium`, `plotly`.
*   **Mobile Apps (Third-Party):** LightBlue (BLE logging), any GPX recorder.

## Directory Structure & Architecture

*   **`firmware/`**: Contains the PlatformIO project for the ESP32 firmware.
    *   `platformio.ini`: PlatformIO configuration.
    *   `bikesensor/bikesensor.ino`: The main Arduino sketch.
*   **`src/`**: Python source code for data processing and visualization.
    *   `merge.py`: Merges GPX track data and BLE CSV data, generating output CSVs in the `data/` directory.
    *   `dashboard.py`: Streamlit application for visualizing the merged data.
    *   `lightblue_parse.py`: Parses the raw CSV output from the LightBlue app.
    *   `analysis.py`: Contains data analysis functions (e.g., FFT).
*   **`data/`**: Target directory for processed output CSV files (`imu.csv`, `windows.csv`, `track.csv`).
*   **`pyproject.toml` / `uv.lock`**: Python dependency definitions (using `uv`).

## Building and Running

### Firmware (PlatformIO)

To work with the firmware, make sure you have PlatformIO installed.

*   **Build:**
    ```bash
    pio run -d firmware
    ```
*   **Flash (Upload):**
    ```bash
    pio run -d firmware -t upload
    ```
*   **Serial Monitor:**
    ```bash
    pio device monitor -b 115200
    ```

### Python Data Pipeline

The Python environment is managed exclusively by `uv`.

1.  **Process Data:**
    Merge the recorded GPX track and LightBlue CSV. This generates processed CSVs in the specified output directory (`data/`).
    ```bash
    uv run python src/merge.py path/to/track.gpx path/to/lightblue.csv data
    ```

2.  **Run Dashboard:**
    Launch the Streamlit interactive dashboard.
    ```bash
    uv run streamlit run src/dashboard.py
    ```

## Development Conventions

*   **Python Package Manager:** The user *exclusively* uses `uv` for Python projects. Do not use `pip`, `poetry`, or other managers unless strictly necessary or requested.
*   **Firmware Structure:** The `.ino` file is kept in `firmware/bikesensor/` with `src_dir` pointed there in `platformio.ini` to allow PlatformIO to compile it without renaming.
*   **Data Formats:** Ensure BLE packet definitions align with the structures described in `README.md` (SYNC and DATA packets). Data endianness must be respected (Little-Endian for metadata, Big-Endian for raw MPU-6050 FIFO data).
