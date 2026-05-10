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
    ["max_bump_g", "band_mid_g", "band_low_g", "band_high_g", "rms_g", "speed_kmh", "peak_hz"],
    index=0,
)
radius = st.sidebar.slider("Heatmap radius (px)", 4, 30, 10)

# --- Section 3: KPIs (Key Performance Indicators) ---
duration_min = (windows["timestamp"].max() - windows["timestamp"].min()).total_seconds() / 60
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Duration (min)", f"{duration_min:.1f}")
c2.metric("Distance (km)", f"{windows['cum_dist_m'].max() / 1000:.2f}")
c3.metric("Avg speed (km/h)", f"{windows['speed_kmh'].mean():.1f}")
c4.metric(f"Mean {metric}", f"{windows[metric].mean():.3f}")
if "battery_pct" in windows.columns:
    batt_start = windows["battery_pct"].iloc[0]
    batt_end = windows["battery_pct"].iloc[-1]
    c5.metric("Battery", f"{batt_end:.0f}%", f"{batt_end - batt_start:.0f}%", delta_color="normal")
else:
    c5.metric("Battery", "N/A")

# --- Section 4: Main Layout (Tabs & Columns) ---
tab_map, tab_route = st.tabs(["🗺️ Map Analysis", "📈 Route Overview"])

with tab_map:
    map_col, plot_col = st.columns([1.5, 1], gap="large")
    
    with map_col:
        st.subheader("Vibration Heatmap")
        st.markdown("Click anywhere on the route to see local vibration details.")
        mid_lat, mid_lon = windows["lat"].mean(), windows["lon"].mean()
        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=14, tiles="CartoDB positron")
        
        # Draw the ride track as a faint line
        folium.PolyLine(
            list(zip(windows["lat"], windows["lon"], strict=True)),
            weight=3, opacity=0.5, color="#3b82f6",
        ).add_to(m)
        
        # Heatmap Layer
        v = windows[metric].to_numpy()
        lo, hi = np.nanpercentile(v, [5, 95])
        w = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
        HeatMap(
            list(zip(windows["lat"], windows["lon"], w, strict=True)),
            radius=radius, blur=radius, min_opacity=0.3,
        ).add_to(m)
        
        # Clickable markers
        stride = max(1, len(windows) // 400)
        for _, row in windows.iloc[::stride].iterrows():
            folium.CircleMarker(
                location=(row["lat"], row["lon"]), radius=4,
                color=None, fill=True, fill_opacity=0.0,
                tooltip=f"{row['timestamp'].strftime('%H:%M:%S')}<br>{metric}={row[metric]:.3f}<br>peak={row['peak_hz']:.1f} Hz",
            ).add_to(m)
        
        # Render map and capture click events
        event = st_folium(m, height=700, width=None, returned_objects=["last_object_clicked"], use_container_width=True)

    with plot_col:
        # --- Section 5: Spectral Analysis (Context-Sensitive) ---
        clicked = event.get("last_object_clicked") if event else None
        if clicked:
            d = (windows["lat"] - clicked["lat"]) ** 2 + (windows["lon"] - clicked["lng"]) ** 2
            sel = windows.loc[d.idxmin()]
        else:
            sel = windows.loc[windows[metric].idxmax()]
        
        st.subheader("Local Analysis")
        st.markdown(f"**Time:** {sel['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} &nbsp; | &nbsp; **{metric}:** {sel[metric]:.3f} g")
        
        # Extract the raw IMU samples for the selected window
        fs = float(sel["fs_hz"])
        win_n = int(sel["win_n"])
        center = pd.to_datetime(sel["timestamp"], utc=True)
        half = pd.Timedelta(seconds=win_n / fs / 2)
        seg_imu = imu[(imu["timestamp"] >= center - half) & (imu["timestamp"] <= center + half)]
        
        if len(seg_imu) >= 8:
            # Recalculate PSD
            sig = np.sqrt(seg_imu["ax"] ** 2 + seg_imu["ay"] ** 2 + seg_imu["az"] ** 2).to_numpy() - 1.0
            sig = detrend(sig, type="constant")
            n = len(sig)
            win = get_window("hann", n)
            psd = (np.abs(np.fft.rfft(sig * win)) ** 2) / (fs * (win ** 2).sum())
            psd[1:-1] *= 2
            freqs = np.fft.rfftfreq(n, d=1.0 / fs)
            
            # Frequency Domain Plot
            fig_freq = px.line(x=freqs, y=np.sqrt(psd),
                               labels={"x": "Frequency (Hz)", "y": "g / √Hz"},
                               log_y=True, title="Frequency Spectrum (PSD)",
                               color_discrete_sequence=["#8b5cf6"])
            fig_freq.update_layout(margin=dict(l=0, r=0, t=40, b=0), height=300)
            st.plotly_chart(fig_freq, use_container_width=True)
        
            # Time-Domain Plot with Butterworth Filter
            from scipy.signal import butter, filtfilt
            cutoff_hz = 25.0
            nyq = 0.5 * fs
            normal_cutoff = cutoff_hz / nyq
            b, a = butter(4, normal_cutoff, btype='low', analog=False)
            sig_filtered = filtfilt(b, a, sig)
            
            time_arr = (seg_imu["timestamp"] - seg_imu["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
            
            df_time = pd.DataFrame({
                "Time (s)": np.concatenate([time_arr, time_arr]),
                "Acceleration (g)": np.concatenate([sig, sig_filtered]),
                "Signal": ["Raw (Detrended)"] * len(time_arr) + ["Filtered (25Hz LP)"] * len(time_arr)
            })
            
            fig_time = px.line(df_time, x="Time (s)", y="Acceleration (g)", color="Signal",
                               title="Time-Domain Signal (Bumps)",
                               color_discrete_sequence=["#cbd5e1", "#ef4444"])
            fig_time.update_layout(margin=dict(l=0, r=0, t=40, b=0), height=300, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_time, use_container_width=True)
        
        else:
            st.info("Not enough IMU samples around this point.")

with tab_route:
    st.subheader("Vibration & Speed Along Route")
    
    # --- Section 6: Distance Plots ---
    route_col1, route_col2 = st.columns(2, gap="large")
    
    with route_col1:
        fig_vib = px.line(windows, x="cum_dist_m",
                          y=["band_low_g", "band_mid_g", "band_high_g"],
                          labels={"cum_dist_m": "Distance (m)", "value": "g RMS", "variable": "Frequency Band"},
                          title="Vibration Intensity by Distance")
        fig_vib.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig_vib, use_container_width=True)
        
    with route_col2:
        fig_speed = px.line(windows, x="cum_dist_m", y="speed_kmh",
                            labels={"cum_dist_m": "Distance (m)", "speed_kmh": "Speed (km/h)"},
                            title="Speed by Distance",
                            color_discrete_sequence=["#10b981"])
        st.plotly_chart(fig_speed, use_container_width=True)
