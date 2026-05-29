"""
Multi-Ride Streamlit Dashboard: Aggregated maps, road quality heatmaps, and curb detection.

Usage:
    uv run streamlit run src/dashboard.py
"""

from __future__ import annotations

import os
import sys
import sqlite3
import datetime
import struct
import shutil
from pathlib import Path

# Add project root to sys.path so we can import from src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import folium
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import HeatMap
from scipy.signal import detrend, get_window, butter, filtfilt
from streamlit_folium import st_folium

from src.merge import build as merge_build
from src.db import get_all_rides, DB_PATH, add_ride, init_db, clear_db

# Initialize DB on start
init_db()

st.set_page_config(page_title="Bikesensor IoT Dashboard", layout="wide", page_icon="🚴")

# Modern Styling
st.markdown("""
<style>
    .reportview-container { background: #0f172a; }
    .stMetric { border: 1px solid #1e293b; padding: 15px; border-radius: 10px; background-color: #1e293b; }
    div[data-testid="metric-container"] { color: #f8fafc; }
</style>
""", unsafe_allow_html=True)

st.title("🚴 Bikesensor IoT Dashboard")
st.markdown("Analyze road surface quality, explore bike vibration frequencies, and locate high curbs across your rides.")

# --- Helper: Create Mock Data for Seamless Testing ---
def generate_mock_ride(ride_date: datetime.datetime, prefix: str = "mock"):
    """Generates a realistic mock ride and inserts it into the database."""
    ride_id = f"ride_{prefix}_{ride_date.strftime('%Y%m%d_%H%M%S')}"
    ride_dir = Path(__file__).resolve().parent.parent / "data" / "rides" / ride_id
    ride_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Create a mock GPX track wandering through Kiel/CAU Campus
    gpx_tpl = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx version="1.1" creator="bikesensor_mock" xmlns="http://www.topografix.com/GPX/1/1">
      <trk>
        <trkseg>
          {points}
        </trkseg>
      </trk>
    </gpx>
    """
    pts = []
    base_lat = 54.348 + np.random.uniform(-0.01, 0.01)
    base_lon = 10.125 + np.random.uniform(-0.01, 0.01)
    
    num_samples = 250
    for i in range(num_samples):
        # Move in a slight diagonal
        lat = base_lat + (i * 0.00005)
        lon = base_lon + (i * 0.00008)
        ele = 12.0 + np.sin(i / 10.0) * 2.0
        ts = (ride_date + datetime.timedelta(seconds=i * 0.2)).isoformat() + "Z"
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><time>{ts}</time><ele>{ele:.1f}</ele></trkpt>')
        
    gpx_data = gpx_tpl.format(points="\n".join(pts))
    gpx_path = ride_dir / "raw_gps.gpx"
    gpx_path.write_text(gpx_data, encoding="utf-8")
    
    # 2. Create raw BLE packets (with simulated vibrations and a couple of curbs)
    # SYNC 1
    sync1 = struct.pack("<BIHBB", 0xA5, 0, 250, 6, 0)
    ble_packets = [{"Timestamp": ride_date.isoformat() + "Z", "Value": sync1.hex()}]
    
    for i in range(num_samples * 50): # ~10 samples per GPS tick, at 250Hz nominal
        t_now = ride_date + datetime.timedelta(seconds=i * 0.004)
        
        # Simulate normal vibration + add a few sharp curb spikes (around index 2000 and 6000)
        # Normal vibration around 0.1g, curbs around 2.2g
        amp = 1500  # ~0.18g
        if 2000 <= i <= 2010 or 6000 <= i <= 6010:
            amp = 18000 # ~2.2g (Curb Spike!)
            
        # Z-axis standard gravity (8192 LSB) + AC vibration
        az = int(np.clip(8192 + np.random.normal(0, amp), -32768, 32767))
        ax = int(np.clip(np.random.normal(0, amp / 2), -32768, 32767))
        ay = int(np.clip(np.random.normal(0, amp / 2), -32768, 32767))
        
        data = struct.pack("<BIB", 0x5A, i, 1) + struct.pack(">hhhhhh", ax, ay, az, 0, 0, 0)
        ble_packets.append({"Timestamp": t_now.isoformat() + "Z", "Value": data.hex()})
        
    # SYNC 2
    sync2 = struct.pack("<BIHBB", 0xA5, num_samples * 50, 250, 6, 95) # 95% battery
    ble_packets.append({"Timestamp": (ride_date + datetime.timedelta(seconds=num_samples * 0.2)).isoformat() + "Z", "Value": sync2.hex()})
    
    csv_path = ride_dir / "raw_imu.csv"
    pd.DataFrame(ble_packets).to_csv(csv_path, index=False)
    
    # Run merge build
    merge_build([gpx_path], [csv_path], ride_dir)
    
    # Add to DB
    track_df = pd.read_csv(ride_dir / "track.csv")
    track_df["timestamp"] = pd.to_datetime(track_df["timestamp"])
    start_time = track_df["timestamp"].min().isoformat()
    end_time = track_df["timestamp"].max().isoformat()
    duration_s = (track_df["timestamp"].max() - track_df["timestamp"].min()).total_seconds()
    distance_m = float(track_df["cum_dist_m"].max())
    avg_speed_kmh = float(track_df["speed_kmh"].mean())
    
    add_ride(
        start_time=start_time,
        end_time=end_time,
        distance_m=distance_m,
        duration_s=duration_s,
        avg_speed_kmh=avg_speed_kmh,
        file_path=str(ride_dir)
    )

# --- Sidebar: Multi-Ride Loading ---
st.sidebar.header("📂 Ride Data Manager")

# Retrieve rides
rides = get_all_rides()

# Load mock data if requested or database is empty
if len(rides) == 0:
    st.sidebar.info("No rides found in your database. Click below to load some realistic mock rides!")
    if st.sidebar.button("✨ Load Mock Rides"):
        with st.spinner("Generating mock rides..."):
            generate_mock_ride(datetime.datetime.now() - datetime.timedelta(days=1), "kiel_east")
            generate_mock_ride(datetime.datetime.now(), "kiel_uni")
            st.rerun()

# --- Sidebar: Reset & Cleanup Manager ---
if len(rides) > 0:
    st.sidebar.markdown("---")
    if st.sidebar.button("🗑️ Clear All Rides", help="Permanently delete all ride files and records from the database.", use_container_width=True):
        with st.spinner("Clearing all rides..."):
            try:
                # 1. Clear database
                clear_db()
                
                # 2. Delete all ride directories under data/rides/
                rides_dir = Path(__file__).resolve().parent.parent / "data" / "rides"
                if rides_dir.exists():
                    for item in rides_dir.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        elif item.is_file() and item.name != ".gitkeep":
                            item.unlink()
                            
                # 3. Delete any files in pending_vibrations if any exist
                pending_dir = Path(__file__).resolve().parent.parent / "data" / "rides" / "pending_vibrations"
                if pending_dir.exists():
                    shutil.rmtree(pending_dir)
                    
                st.success("Database cleared successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Error resetting database: {e}")

# Select ride dropdown
ride_options = ["🌍 All Rides Combined"] + [
    f"📅 {pd.to_datetime(r['start_time']).strftime('%Y-%m-%d %H:%M')} | {r['distance_m']/1000:.2f} km"
    for r in rides
]
selected_ride_idx = st.sidebar.selectbox("Select Ride to Analyze", range(len(ride_options)), index=0)

# Settings for map
metric_options = {
    "📉 Road Roughness (Overall Vibration - RMS)": "rms_g",
    "💥 Peak Impact Intensity (Max Pothole/Crack Shock)": "max_bump_g",
    "〰️ Sway & Large Dips (Low Frequency 1-10 Hz)": "band_low_g",
    "🧱 Cobblestone & Gravel (Mid Frequency 10-30 Hz)": "band_mid_g",
    "🔊 Asphalt Micro-Texture (High Frequency 30-50 Hz)": "band_high_g",
    "⚡ Riding Speed (km/h)": "speed_kmh"
}

selected_metric_name = st.sidebar.selectbox(
    "Vibration Heatmap Metric",
    options=list(metric_options.keys()),
    index=0,
)
metric = metric_options[selected_metric_name]
radius = st.sidebar.slider("Heatmap radius (px)", 4, 30, 12)

# Curb configuration
st.sidebar.markdown("---")
st.sidebar.header("⚠️ Curb Detection Settings")
curb_threshold = st.sidebar.slider("Curb Shock Threshold (g)", 0.8, 3.0, 1.4, step=0.1, help="Sudden vertical/vector shock threshold to identify curbs.")
max_curb_speed = st.sidebar.slider("Max Speed for Curb (km/h)", 5, 25, 12, help="To avoid mistaking fast bumps for curbs, set the speed threshold below which a shock is flagged.")

# Load Data based on selection
if len(rides) == 0:
    st.info("💡 **No rides found in your database yet!**\n\nPower on your ESP32 mapping box within range of your home Wi-Fi and it will automatically sync your rides. Alternatively, click **'✨ Load Mock Rides'** in the left sidebar to populate the dashboard with realistic test data!")
    st.stop()

@st.cache_data
def load_all_rides_data(selected_idx: int, rides_list: list):
    """Loads and aggregates data frames based on multi or single ride selection."""
    if selected_idx == 0:
        # Load all rides
        windows_df_list = []
        track_df_list = []
        imu_df_list = []
        
        for r in rides_list:
            r_path = Path(r["file_path"])
            if (r_path / "windows.csv").exists():
                win = pd.read_csv(r_path / "windows.csv", parse_dates=["timestamp"])
                win["ride_id"] = r["id"]
                windows_df_list.append(win)
            if (r_path / "track.csv").exists():
                trk = pd.read_csv(r_path / "track.csv", parse_dates=["timestamp"])
                trk["ride_id"] = r["id"]
                track_df_list.append(trk)
            if (r_path / "imu.csv").exists():
                imu = pd.read_csv(r_path / "imu.csv", parse_dates=["timestamp"])
                imu["ride_id"] = r["id"]
                imu_df_list.append(imu)
                
        return (
            pd.concat(windows_df_list).sort_values("timestamp").reset_index(drop=True),
            pd.concat(track_df_list).sort_values("timestamp").reset_index(drop=True),
            pd.concat(imu_df_list).sort_values("timestamp").reset_index(drop=True)
        )
    else:
        # Load single ride
        r = rides_list[selected_idx - 1]
        r_path = Path(r["file_path"])
        win = pd.read_csv(r_path / "windows.csv", parse_dates=["timestamp"])
        trk = pd.read_csv(r_path / "track.csv", parse_dates=["timestamp"])
        imu = pd.read_csv(r_path / "imu.csv", parse_dates=["timestamp"])
        win["ride_id"] = r["id"]
        trk["ride_id"] = r["id"]
        imu["ride_id"] = r["id"]
        return win, trk, imu

windows, track, imu = load_all_rides_data(selected_ride_idx, rides)

# --- KPIs (Key Performance Indicators) ---
st.markdown("### 📊 Metrics")
show_battery = "battery_pct" in windows.columns and not windows["battery_pct"].isna().all()

if show_battery:
    c1, c2, c3, c4, c5 = st.columns(5)
else:
    c1, c2, c3, c4 = st.columns(4)

total_dist_km = windows["cum_dist_m"].max() / 1000 if selected_ride_idx != 0 else sum([r["distance_m"] for r in rides]) / 1000
total_dur_min = (windows["timestamp"].max() - windows["timestamp"].min()).total_seconds() / 60 if selected_ride_idx != 0 else sum([r["duration_s"] for r in rides]) / 60
avg_speed = windows["speed_kmh"].mean()
peak_vibe = windows["max_bump_g"].max()

c1.metric("Total Distance Ridden", f"{total_dist_km:.2f} km")
c2.metric("Total Duration", f"{total_dur_min:.1f} min")
c3.metric("Average Speed", f"{avg_speed:.1f} km/h")
c4.metric(f"Peak Vibration (g)", f"{peak_vibe:.2f} g")

if show_battery:
    c5.metric("End Battery Level", f"{int(windows['battery_pct'].iloc[-1])}%")

# --- Layout: Tabs ---
tab_map, tab_analytics = st.tabs(["🗺️ Unified Heatmap & Curb Map", "📈 Ride Analytics"])

with tab_map:
    map_col, plot_col = st.columns([1.6, 1], gap="large")
    
    with map_col:
        st.subheader("Interactive Map Analysis")
        st.markdown("Colors represent road surface vibration levels. **Red warning markers indicate detected high curbs**.")
        
        # Center map
        mid_lat, mid_lon = windows["lat"].mean(), windows["lon"].mean()
        m = folium.Map(location=[mid_lat, mid_lon], zoom_start=14, tiles="CartoDB positron")
        
        # Plot Ride Tracks
        # For multiple rides, group and draw separate lines
        for ride_id, grp in windows.groupby("ride_id"):
            folium.PolyLine(
                list(zip(grp["lat"], grp["lon"], strict=True)),
                weight=3, opacity=0.4, color="#3b82f6",
                tooltip=f"Ride ID: {ride_id}"
            ).add_to(m)
            
        # Draw Heatmap Layer
        v = windows[metric].to_numpy()
        lo, hi = np.nanpercentile(v, [5, 95])
        w = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
        
        HeatMap(
            list(zip(windows["lat"], windows["lon"], w, strict=True)),
            radius=radius, blur=radius, min_opacity=0.3,
        ).add_to(m)
        
        # Clickable Route Markers for Local Context (strided to avoid lag)
        stride = max(1, len(windows) // 300)
        for _, row in windows.iloc[::stride].iterrows():
            unit = "km/h" if metric == "speed_kmh" else "g"
            val_fmt = f"{row[metric]:.1f}" if metric == "speed_kmh" else f"{row[metric]:.2f}"
            folium.CircleMarker(
                location=(row["lat"], row["lon"]), radius=3,
                color=None, fill=True, fill_opacity=0.0,
                tooltip=f"Time: {row['timestamp'].strftime('%H:%M:%S')}<br><b>{selected_metric_name}</b>: {val_fmt} {unit}",
            ).add_to(m)
            
        # --- Curb Detection Implementation ---
        # A curb is characterized by:
        # 1. max_bump_g exceeds curb_threshold
        # 2. vehicle speed is low (speed_kmh <= max_curb_speed)
        curbs = windows[(windows["max_bump_g"] >= curb_threshold) & (windows["speed_kmh"] <= max_curb_speed)]
        
        # To avoid putting a marker on contiguous windows for the same curb, we group close coordinates
        if not curbs.empty:
            st.sidebar.success(f"Detected {len(curbs)} High Curbs / Bumps!")
            
            for idx, c_row in curbs.iterrows():
                folium.Marker(
                    location=[c_row["lat"], c_row["lon"]],
                    popup=f"⚠️ <b>High Curb / Severe Shock</b><br>Intensity: {c_row['max_bump_g']:.2f}g<br>Speed: {c_row['speed_kmh']:.1f} km/h<br>Time: {c_row['timestamp'].strftime('%H:%M:%S')}",
                    icon=folium.Icon(color="red", icon="exclamation-sign", prefix="glyphicon")
                ).add_to(m)
        else:
            st.sidebar.info("No curbs found at current settings.")
            
        # Render map in Streamlit
        event = st_folium(m, height=650, width=None, returned_objects=["last_object_clicked"], use_container_width=True)
        
    with plot_col:
        st.subheader("🔍 Local Detail & Spectrum")
        
        # Select focal window: either clicked by user or the highest vibration window
        clicked = event.get("last_object_clicked") if event else None
        if clicked:
            d = (windows["lat"] - clicked["lat"]) ** 2 + (windows["lon"] - clicked["lng"]) ** 2
            sel = windows.loc[d.idxmin()]
            st.markdown(f"📍 **Selected Point (Clicked Map):**")
        else:
            sel = windows.loc[windows[metric].idxmax()]
            st.markdown(f"🔥 **Point of Maximum Vibration (Default):**")
            
        unit = "km/h" if metric == "speed_kmh" else "g"
        val_fmt = f"{sel[metric]:.1f}" if metric == "speed_kmh" else f"{sel[metric]:.2f}"
        st.markdown(f"""
        * **Timestamp:** {sel['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}
        * **{selected_metric_name}:** {val_fmt} {unit}
        * **Speed:** {sel['speed_kmh']:.1f} km/h
        * **Dominant Freq:** {sel.get('peak_hz', 0.0):.1f} Hz
        """)
        
        # Segment out IMU samples around this window
        sel_imu = imu[imu["ride_id"] == sel["ride_id"]]
        fs = float(sel["fs_hz"])
        win_n = int(sel["win_n"])
        center = pd.to_datetime(sel["timestamp"], utc=True)
        half = pd.Timedelta(seconds=win_n / fs / 2)
        seg_imu = sel_imu[(sel_imu["timestamp"] >= center - half) & (sel_imu["timestamp"] <= center + half)]
        
        if len(seg_imu) >= 8:
            sig = np.sqrt(seg_imu["ax"] ** 2 + seg_imu["ay"] ** 2 + seg_imu["az"] ** 2).to_numpy() - 1.0
            sig = detrend(sig, type="constant")
            n = len(sig)
            win = get_window("hann", n)
            psd = (np.abs(np.fft.rfft(sig * win)) ** 2) / (fs * (win ** 2).sum())
            psd[1:-1] *= 2
            freqs = np.fft.rfftfreq(n, d=1.0 / fs)
            
            # Frequency Spectrum PSD Plot
            fig_freq = px.line(x=freqs, y=np.sqrt(psd),
                               labels={"x": "Frequency (Hz)", "y": "g / √Hz"},
                               log_y=True, title="Power Spectral Density (Vibration Signature)",
                               color_discrete_sequence=["#a855f7"])
            fig_freq.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=250)
            st.plotly_chart(fig_freq, use_container_width=True)
            
            # Low-pass filter for time-domain bump view
            cutoff_hz = 25.0
            nyq = 0.5 * fs
            if cutoff_hz >= nyq:
                sig_filtered = sig
            else:
                normal_cutoff = cutoff_hz / nyq
                b, a = butter(4, normal_cutoff, btype='low', analog=False)
                sig_filtered = filtfilt(b, a, sig)
                
            time_arr = (seg_imu["timestamp"] - seg_imu["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
            
            df_time = pd.DataFrame({
                "Time (s)": np.concatenate([time_arr, time_arr]),
                "Acceleration (g)": np.concatenate([sig, sig_filtered]),
                "Signal": ["Raw Vibration"] * len(time_arr) + ["Filtered (25Hz LP)"] * len(time_arr)
            })
            
            fig_time = px.line(df_time, x="Time (s)", y="Acceleration (g)", color="Signal",
                               title="Time-Domain Bumps (Vertical acceleration)",
                               color_discrete_sequence=["#cbd5e1", "#ef4444"])
            fig_time.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=250, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_time, use_container_width=True)
        else:
            st.info("Not enough raw IMU data around this location window to build frequency spectra.")

with tab_analytics:
    st.subheader("📈 Multi-Ride Vibration Spectrum & Comparison")
    
    if selected_ride_idx != 0:
        # Single Ride Distance Plots
        col1, col2 = st.columns(2)
        with col1:
            fig_vib = px.line(windows, x="cum_dist_m",
                              y=["band_low_g", "band_mid_g", "band_high_g"],
                              labels={"cum_dist_m": "Distance (m)", "value": "g RMS", "variable": "Bands"},
                              title="Vibration Levels along the Ride")
            fig_vib.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_vib, use_container_width=True)
        with col2:
            fig_spd = px.line(windows, x="cum_dist_m", y="speed_kmh",
                              labels={"cum_dist_m": "Distance (m)", "speed_kmh": "Speed (km/h)"},
                              title="Riding Speed along the Ride",
                              color_discrete_sequence=["#10b981"])
            st.plotly_chart(fig_spd, use_container_width=True)
    else:
        # All Rides Aggregated View
        st.markdown("### Ride Comparison")
        # Generate summary stats per ride
        summaries = []
        for r in rides:
            r_windows = windows[windows["ride_id"] == r["id"]]
            if not r_windows.empty:
                summaries.append({
                    "Ride ID": f"Ride #{r['id']}",
                    "Start Time": pd.to_datetime(r["start_time"]).strftime("%Y-%m-%d %H:%M"),
                    "Distance (km)": r["distance_m"] / 1000.0,
                    "Avg Speed (km/h)": r["avg_speed_kmh"],
                    "Avg Vibration (g)": r_windows["rms_g"].mean(),
                    "Max Shock (g)": r_windows["max_bump_g"].max()
                })
        
        if summaries:
            summary_df = pd.DataFrame(summaries)
            st.dataframe(summary_df, use_container_width=True)
            
            # Plot bar chart comparing average vibration
            col1, col2 = st.columns(2)
            with col1:
                fig_comp_vib = px.bar(summary_df, x="Start Time", y="Avg Vibration (g)",
                                      title="Average Road Roughness (g RMS) by Ride",
                                      color="Avg Vibration (g)", color_continuous_scale="Purples")
                st.plotly_chart(fig_comp_vib, use_container_width=True)
            with col2:
                fig_comp_spd = px.bar(summary_df, x="Start Time", y="Distance (km)",
                                      title="Ride Distance (km) by Session",
                                      color="Distance (km)", color_continuous_scale="Tealgrn")
                st.plotly_chart(fig_comp_spd, use_container_width=True)
