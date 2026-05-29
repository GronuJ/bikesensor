from fastapi import FastAPI, HTTPException, Request, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from pathlib import Path
import json
import pandas as pd
import datetime
import uvicorn

# Import database and merge functionality
from src.db import init_db, add_ride, get_all_rides
from src.merge import build

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes the database on server startup."""
    init_db()
    yield

app = FastAPI(title="Bikesensor Backend Server", lifespan=lifespan)

# Configure CORS so mobile devices and web clients can access the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define directories
RIDES_DIR = Path(__file__).resolve().parent.parent / "data" / "rides"
RIDES_DIR.mkdir(exist_ok=True, parents=True)

# Pydantic Schemas for validation
class BLEPacket(BaseModel):
    timestamp: str  # ISO string or wallclock time
    hex: str        # Raw hex string from MPU-6050

class RideUploadPayload(BaseModel):
    gpx_data: str
    ble_data: List[BLEPacket]

@app.post("/api/upload")
async def upload_ride(payload: RideUploadPayload):
    """
    Receives a ride recording (raw GPX + raw BLE hex logs).
    
    1. Creates a unique ride directory.
    2. Saves the raw GPX and raw BLE data to disk.
    3. Runs the existing Python merge/DSP pipeline to generate processed datasets.
    4. Calculates ride statistics and records the ride in the SQLite DB.
    """
    if not payload.gpx_data:
        raise HTTPException(status_code=400, detail="GPX data is required.")
    if not payload.ble_data:
        raise HTTPException(status_code=400, detail="BLE IMU data is required.")

    # 1. Create unique ride ID
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    ride_id = f"ride_{timestamp_str}"
    ride_dir = RIDES_DIR / ride_id
    ride_dir.mkdir(exist_ok=True, parents=True)

    gpx_path = ride_dir / "raw_gps.gpx"
    csv_path = ride_dir / "raw_imu.csv"

    try:
        # 2. Save GPX data
        gpx_path.write_text(payload.gpx_data, encoding="utf-8")

        # 3. Convert and save BLE data as CSV in LightBlue-parseable format
        ble_df = pd.DataFrame([{"Timestamp": p.timestamp, "Value": p.hex} for p in payload.ble_data])
        ble_df.to_csv(csv_path, index=False)

        # 4. Execute standard Python merge pipeline (DLPF, STFT, GPS Interpolation)
        paths = build(
            gpx_paths=[gpx_path],
            csv_paths=[csv_path],
            out_dir=ride_dir
        )

        # 5. Extract statistics from the merged track dataset
        track_df = pd.read_csv(paths["track"])
        if track_df.empty:
            raise ValueError("Merged GPS track dataset is empty.")

        # Ensure timestamp column is parsed as datetime
        track_df["timestamp"] = pd.to_datetime(track_df["timestamp"])
        
        start_time = track_df["timestamp"].min().isoformat()
        end_time = track_df["timestamp"].max().isoformat()
        
        duration_s = (track_df["timestamp"].max() - track_df["timestamp"].min()).total_seconds()
        distance_m = float(track_df["cum_dist_m"].max())
        avg_speed_kmh = float(track_df["speed_kmh"].mean())

        # 6. Add record to database
        db_id = add_ride(
            start_time=start_time,
            end_time=end_time,
            distance_m=distance_m,
            duration_s=duration_s,
            avg_speed_kmh=avg_speed_kmh,
            file_path=str(ride_dir)
        )

        return {
            "status": "success",
            "message": f"Ride {ride_id} processed successfully.",
            "db_id": db_id,
            "ride_id": ride_id,
            "stats": {
                "start_time": start_time,
                "end_time": end_time,
                "distance_m": distance_m,
                "duration_s": duration_s,
                "avg_speed_kmh": avg_speed_kmh
            }
        }

    except Exception as e:
        # Clean up files if anything fails during processing
        if gpx_path.exists():
            gpx_path.unlink()
        if csv_path.exists():
            csv_path.unlink()
        if ride_dir.exists() and not any(ride_dir.iterdir()):
            ride_dir.rmdir()
            
        print(f"Error processing ride: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process ride: {str(e)}")

def send_mac_notification(ride_id: str, distance_m: float, duration_s: float):
    """Sends a native macOS notification to the MacBook Josts-MacBook-Air.local over SSH."""
    try:
        import paramiko
        # Connect to MacBook Josts-MacBook-Air.local using ssh keys
        mac_ssh = paramiko.SSHClient()
        mac_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect dynamically utilizing active local SSH keys/agent
        mac_ssh.connect(
            "Josts-MacBook-Air.local",
            username="jostjens",
            timeout=3,
            allow_agent=True,
            look_for_keys=True
        )
        
        # Format notification text
        dist_km = distance_m / 1000.0
        dur_min = duration_s / 60.0
        msg = f"Ride synced successfully! Mapped {dist_km:.2f} km in {dur_min:.1f} minutes."
        
        # Display native macOS notification with AppleScript
        cmd = f'osascript -e \'display notification "{msg}" with title "🚴 Bikesensor Sync" sound name "Glass"\''
        mac_ssh.exec_command(cmd)
        mac_ssh.close()
        print("✅ [Background] Sent native macOS notification to Josts-Air.local")
    except Exception as e:
        print(f"[Background] Could not send macOS notification: {e} (Remote Login / SSH might be disabled on your Mac)")

def process_unified_offline_background(csv_path: Path, ride_dir: Path, ride_id: str, x_ride_filename: str):
    """Heavy vibration detrending, SciPy STFT, and SQLite DB insertion executed asynchronously."""
    try:
        print(f"🔄 [Background] Starting STFT & DSP analysis for {x_ride_filename}...")
        from src.merge import process_unified_offline
        paths = process_unified_offline(csv_path, ride_dir)
        
        # Extract statistics from the merged track dataset
        import pandas as pd
        track_df = pd.read_csv(paths["track"])
        track_df["timestamp"] = pd.to_datetime(track_df["timestamp"])
        start_time = track_df["timestamp"].min().isoformat()
        end_time = track_df["timestamp"].max().isoformat()
        duration_s = (track_df["timestamp"].max() - track_df["timestamp"].min()).total_seconds()
        distance_m = float(track_df["cum_dist_m"].max())
        avg_speed_kmh = float(track_df["speed_kmh"].mean())
        
        # Add to database
        db_id = add_ride(
            start_time=start_time,
            end_time=end_time,
            distance_m=distance_m,
            duration_s=duration_s,
            avg_speed_kmh=avg_speed_kmh,
            file_path=str(ride_dir)
        )
        print(f"✨ [Background] Auto-processed unified GPS ride: {ride_id} (DB ID: {db_id})")
        
        # Trigger native macOS notification
        send_mac_notification(ride_id, distance_m, duration_s)
        
    except ValueError as ve:
        print(f"[Background] Ignoring invalid unified offline file {x_ride_filename}: {ve}")
        if csv_path.exists():
            csv_path.unlink()
        if ride_dir.exists() and not any(ride_dir.iterdir()):
            ride_dir.rmdir()
    except Exception as e:
        print(f"[Background] Error processing offline file {x_ride_filename}: {e}")
        # Clean up files if anything fails
        if csv_path.exists():
            csv_path.unlink()
        if ride_dir.exists() and not any(ride_dir.iterdir()):
            ride_dir.rmdir()

@app.post("/api/upload-offline")
async def upload_offline(
    request: Request,
    background_tasks: BackgroundTasks,
    x_ride_filename: str = Header(...)
):
    """
    Receives an offline ride vibration log (CSV) directly from the ESP32 via Wi-Fi.
    
    1. If the CSV contains 'lat' and 'lon' (Unified Standalone GPS Mode), we 
       immediately process it, run the STFT/DSP pipeline, and register it in the DB.
    2. Otherwise, we save it to the pending directory to be merged with a GPX track later.
    """
    try:
        body = await request.body()
        csv_content = body.decode("utf-8")

        # Check if the file is empty or only contains the CSV header
        lines = [line.strip() for line in csv_content.split("\n") if line.strip()]
        if len(lines) <= 1:
            print(f"Received empty or header-only offline file: {x_ride_filename}. Ignoring and returning success.")
            return {"status": "ignored", "message": "File contains no data rows."}

        # Check the headers of the CSV to auto-detect the mode
        first_line = lines[0]
        headers = [h.strip() for h in first_line.split(",")]
        
        if "lat" in headers and "lon" in headers:
            # --- Method 2: Unified GPS Mode (Fully Automated!) ---
            timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            ride_id = f"ride_auto_{timestamp_str}"
            ride_dir = RIDES_DIR / ride_id
            ride_dir.mkdir(exist_ok=True, parents=True)
            
            # Save the raw CSV directly as raw_imu.csv
            csv_path = ride_dir / "raw_imu.csv"
            csv_path.write_text(csv_content, encoding="utf-8")
            
            # Queue the heavy STFT, filtering, and database operations in a background task
            background_tasks.add_task(
                process_unified_offline_background,
                csv_path,
                ride_dir,
                ride_id,
                x_ride_filename
            )
            
            print(f"Successfully received and queued unified offline file: {x_ride_filename} (processing in background)")
            return {
                "status": "success",
                "message": f"Ride {ride_id} received successfully. Processing is running in the background.",
                "ride_id": ride_id
            }
        else:
            # --- Method 1: Vibration-Only Mode (Pending GPS match) ---
            pending_dir = Path(__file__).resolve().parent.parent / "data" / "rides" / "pending_vibrations"
            pending_dir.mkdir(exist_ok=True, parents=True)
            
            save_path = pending_dir / x_ride_filename
            save_path.write_text(csv_content, encoding="utf-8")
            
            print(f"Successfully received offline vibration file: {x_ride_filename} ({len(body)} bytes)")
            return {"status": "success", "message": f"Saved {x_ride_filename} to pending vibrations."}
            
    except Exception as e:
        print(f"Error receiving offline file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/rides")
async def list_rides():
    """Lists all rides and their stats from the database."""
    try:
        rides = get_all_rides()
        return {"status": "success", "rides": rides}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("src.server:app", host="0.0.0.0", port=8000, reload=True)
