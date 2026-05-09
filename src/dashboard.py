"""
Dashboard: map heatmap of vibration band energy + click-to-see-spectrum.

    uv run streamlit run src/dashboard.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to sys.path so we can import from src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import folium
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import HeatMap
from scipy.signal import detrend, get_window
from streamlit_folium import st_folium

from src.merge import build as merge_build

DATA = Path("data")
RAW_DATA = Path("raw_data")

st.set_page_config(page_title="Bike Sensor", layout="wide")
st.title("Bike vibration map")

# --- Section 1: Data Ingestion (Sidebar) ---
# Allows the user to upload raw GPX and LightBlue CSV files.
# The files are saved to raw_data/ and then processed by the merge pipeline.
with st.sidebar.expander("Upload New Ride Data", expanded=not (DATA / "windows.csv").exists()):
    st.markdown("Upload GPX tracks and LightBlue logs (CSV/TXT) here.")
    uploaded_files = st.file_uploader(
        "Select files", accept_multiple_files=True, type=["gpx", "csv", "txt"]
    )
    if st.button("Process Uploaded Files") and uploaded_files:
        RAW_DATA.mkdir(exist_ok=True)
        gpx_paths = []
        csv_paths = []
        
        for f in uploaded_files:
            file_path = RAW_DATA / f.name
            with open(file_path, "wb") as out_f:
                out_f.write(f.getbuffer())
            
            if f.name.lower().endswith(".gpx"):
                gpx_paths.append(file_path)
            elif f.name.lower().endswith((".csv", ".txt")):
                csv_paths.append(file_path)
        
        if not gpx_paths or not csv_paths:
            st.error("Please upload at least one GPX and one CSV/TXT file.")
        else:
            with st.spinner("Processing ride data..."):
                try:
                    # Run the merge.py pipeline
                    merge_build(gpx_paths, csv_paths, DATA)
                    st.success("Processing complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error during processing: {e}")

if not (DATA / "windows.csv").exists():
    st.info("No processed data found. Please upload your ride files using the sidebar to get started.")
    st.stop()

# --- Section 2: Load Processed Data ---
windows = pd.read_csv(DATA / "windows.csv", parse_dates=["timestamp"])
imu = pd.read_csv(DATA / "imu.csv", parse_dates=["timestamp"])

# Sidebar controls for map visualization
metric = st.sidebar.selectbox(
    "Map metric",
    ["band_mid_g", "band_low_g", "band_high_g", "rms_g", "speed_kmh", "peak_hz"],
    index=0,
)
radius = st.sidebar.slider("Heatmap radius (px)", 4, 30, 10)

# --- Section 3: KPIs (Key Performance Indicators) ---
duration_min = (windows["timestamp"].max() - windows["timestamp"].min()).total_seconds() / 60
c1, c2, c3, c4 = st.columns(4)
c1.metric("Duration (min)", f"{duration_min:.1f}")
c2.metric("Distance (km)", f"{windows['cum_dist_m'].max() / 1000:.2f}")
c3.metric("Avg speed (km/h)", f"{windows['speed_kmh'].mean():.1f}")
c4.metric(f"Mean {metric}", f"{windows[metric].mean():.3f}")

# --- Section 4: Interactive Map ---
mid_lat, mid_lon = windows["lat"].mean(), windows["lon"].mean()
m = folium.Map(location=[mid_lat, mid_lon], zoom_start=15, tiles="OpenStreetMap")

# Draw the ride track as a faint line
folium.PolyLine(
    list(zip(windows["lat"], windows["lon"], strict=True)),
    weight=2, opacity=0.4, color="#444",
).add_to(m)

# Heatmap Layer:
# We normalize the chosen metric to a [0, 1] range based on the 5th and 95th 
# percentiles to ensure the heatmap colors are meaningful even if there are outliers.
v = windows[metric].to_numpy()
lo, hi = np.nanpercentile(v, [5, 95])
w = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
HeatMap(
    list(zip(windows["lat"], windows["lon"], w, strict=True)),
    radius=radius, blur=radius, min_opacity=0.3,
).add_to(m)

# Clickable markers:
# We add invisible markers every N samples to allow the user to click 
# on the map and see the specific spectrum for that location.
stride = max(1, len(windows) // 400)
for _, row in windows.iloc[::stride].iterrows():
    folium.CircleMarker(
        location=(row["lat"], row["lon"]), radius=3,
        color=None, fill=True, fill_opacity=0.0,
        tooltip=f"{row['timestamp']}<br>{metric}={row[metric]:.3f}<br>peak={row['peak_hz']:.1f} Hz",
        popup=folium.Popup(f"{row['timestamp']}", show=False),
    ).add_to(m)

st.subheader("Map")
# Render map and capture click events
event = st_folium(m, height=550, width=None, returned_objects=["last_object_clicked"])

# --- Section 5: Spectral Analysis (Context-Sensitive) ---
# If a point on the map is clicked, we show the spectrum for that exact point.
# Otherwise, we default to the window with the maximum value of the selected metric.
clicked = event.get("last_object_clicked") if event else None
if clicked:
    # Find the window closest to the click coordinates
    d = (windows["lat"] - clicked["lat"]) ** 2 + (windows["lon"] - clicked["lng"]) ** 2
    sel = windows.loc[d.idxmin()]
else:
    # Default selection
    sel = windows.loc[windows[metric].idxmax()]

st.subheader(f"Spectrum @ {sel['timestamp']} ({metric}={sel[metric]:.3f})")

# Extract the raw IMU samples for the selected window
fs = float(sel["fs_hz"])
win_n = int(sel["win_n"])
center = pd.to_datetime(sel["timestamp"], utc=True)
half = pd.Timedelta(seconds=win_n / fs / 2)
seg_imu = imu[(imu["timestamp"] >= center - half) & (imu["timestamp"] <= center + half)]

if len(seg_imu) >= 8:
    # Recalculate PSD for the selected window for visualization
    sig = np.sqrt(seg_imu["ax"] ** 2 + seg_imu["ay"] ** 2 + seg_imu["az"] ** 2).to_numpy() - 1.0
    sig = detrend(sig, type="constant")
    n = len(sig)
    win = get_window("hann", n)
    psd = (np.abs(np.fft.rfft(sig * win)) ** 2) / (fs * (win ** 2).sum())
    psd[1:-1] *= 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    fig = px.line(x=freqs, y=np.sqrt(psd),
                  labels={"x": "Frequency (Hz)", "y": "g / √Hz"},
                  log_y=True)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough IMU samples around this point.")

# --- Section 6: Distance Plots ---
# Shows how vibration and speed change along the course of the ride.
st.subheader("Along the route")
fig = px.line(windows, x="cum_dist_m",
              y=["band_low_g", "band_mid_g", "band_high_g"],
              labels={"cum_dist_m": "Distance (m)", "value": "g RMS"})
st.plotly_chart(fig, use_container_width=True)

fig2 = px.line(windows, x="cum_dist_m", y="speed_kmh",
               labels={"cum_dist_m": "Distance (m)", "speed_kmh": "Speed (km/h)"})
st.plotly_chart(fig2, use_container_width=True)
