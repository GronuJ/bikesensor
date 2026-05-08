"""
Preliminary Streamlit dashboard for merged ride data.

Run:
    uv run streamlit run src/dashboard.py -- data/merged.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="Bike Sensor", layout="wide")
st.title("Bike vibration map")

default = Path("data/merged.csv")
path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
if not path.exists():
    st.error(f"Merged CSV not found at {path}. Run src/merge.py first.")
    st.stop()

df = pd.read_csv(path, parse_dates=["timestamp"])

# --- sidebar controls ---
metric = st.sidebar.selectbox(
    "Color metric", ["vib_rms_g", "vib_g", "speed_kmh", "accel_mag_g"], index=0
)
downsample = st.sidebar.slider("Downsample (every Nth point)", 1, 50, 5)
df_plot = df.iloc[::downsample].copy()

# Map colors: low=green, high=red.
v = df_plot[metric].to_numpy()
v = np.clip((v - np.nanpercentile(v, 5)) /
            (np.nanpercentile(v, 95) - np.nanpercentile(v, 5) + 1e-9), 0, 1)
df_plot["r"] = (255 * v).astype(int)
df_plot["g"] = (255 * (1 - v)).astype(int)
df_plot["b"] = 60

st.subheader("Map")
midpoint = (df_plot["lat"].mean(), df_plot["lon"].mean())
st.pydeck_chart(pdk.Deck(
    map_style="road",
    initial_view_state=pdk.ViewState(
        latitude=midpoint[0], longitude=midpoint[1], zoom=14, pitch=0
    ),
    layers=[pdk.Layer(
        "ScatterplotLayer",
        data=df_plot,
        get_position="[lon, lat]",
        get_fill_color="[r, g, b, 180]",
        get_radius=3,
        pickable=True,
    )],
    tooltip={"text": f"{metric}: {{{metric}}}\nspeed: {{speed_kmh}} km/h"},
))

# --- timeseries ---
c1, c2, c3 = st.columns(3)
c1.metric("Duration (min)", f"{(df['timestamp'].max() - df['timestamp'].min()).total_seconds()/60:.1f}")
c2.metric("Avg speed (km/h)", f"{df['speed_kmh'].mean():.1f}")
c3.metric("Mean vib RMS (g)", f"{df['vib_rms_g'].mean():.3f}")

st.subheader("Vibration over time")
st.line_chart(df.set_index("timestamp")[["vib_rms_g", "vib_g"]])

st.subheader("Speed and acceleration over time")
st.line_chart(df.set_index("timestamp")[["speed_kmh", "accel_mag_g"]])

with st.expander("Raw data"):
    st.dataframe(df.head(500))
