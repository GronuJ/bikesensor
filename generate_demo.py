import struct
import math
import numpy as np
import pandas as pd
from pathlib import Path

# 1. Generate GPX (60 seconds of riding in Berlin)
print("Generating demo.gpx...")
gpx_content = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="demo">
  <trk>
    <name>Demo Ride</name>
    <trkseg>
"""
start_time = pd.Timestamp("2023-10-01 12:00:00", tz="UTC")
lat, lon = 52.5200, 13.4050 # Start somewhere in Berlin
gpx_points = []
for i in range(60):
    t = start_time + pd.Timedelta(seconds=i)
    lat += 0.00005 # Move north
    lon += 0.00005 # Move east
    gpx_points.append(f'      <trkpt lat="{lat}" lon="{lon}"><time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time></trkpt>')
gpx_content += "\n".join(gpx_points)
gpx_content += """
    </trkseg>
  </trk>
</gpx>
"""
Path("demo.gpx").write_text(gpx_content)

# 2. Generate Lightblue CSV (60 seconds of 250Hz IMU data)
print("Generating demo.csv...")
fs = 250
n_samples = 60 * fs
t_phone = start_time

sync1 = struct.pack("<BIHBB", 0xA5, 0, fs, 6, 98)
sync2 = struct.pack("<BIHBB", 0xA5, n_samples, fs, 6, 97)

rows = []
rows.append({"time": t_phone.isoformat(), "hex": sync1.hex()})

for k in range(0, n_samples, 10):
    payload = b""
    for j in range(10):
        idx = k + j
        time_sec = idx / fs
        
        # Base 1g gravity on Z axis
        az = 8192 
        
        # Add some bumpiness/vibration between seconds 20 and 40
        if 20 < time_sec < 40:
            # Add a 20Hz vibration (mid band)
            az += int(math.sin(2 * math.pi * 20 * time_sec) * 2000)
            # Add a 50Hz vibration (high band)
            az += int(math.sin(2 * math.pi * 50 * time_sec) * 1000)
        
        payload += struct.pack(">hhhhhh", 0, 0, az, 0, 0, 0)
    
    pkt = struct.pack("<BIB", 0x5A, k, 10) + payload
    t_pkt = t_phone + pd.Timedelta(seconds=k/fs)
    rows.append({"time": t_pkt.isoformat(), "hex": pkt.hex()})

rows.append({"time": (t_phone + pd.Timedelta(seconds=60)).isoformat(), "hex": sync2.hex()})

pd.DataFrame(rows).to_csv("demo.csv", index=False)
print("Demo data generated.")
