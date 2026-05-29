import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rides.db"

def get_db_connection() -> sqlite3.Connection:
    """Establishes a connection to the SQLite database."""
    DB_PATH.parent.mkdir(exist_ok=True, parents=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row  # Returns rows as dictionary-like objects
    return conn

def init_db() -> None:
    """Initializes the database schema if it doesn't already exist."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                distance_m REAL NOT NULL,
                duration_s REAL NOT NULL,
                avg_speed_kmh REAL NOT NULL,
                file_path TEXT NOT NULL
            )
        """)
        conn.commit()
    print(f"Database initialized at: {DB_PATH}")

def add_ride(
    start_time: str,
    end_time: str,
    distance_m: float,
    duration_s: float,
    avg_speed_kmh: float,
    file_path: str
) -> int:
    """Inserts a new ride record and returns its database ID."""
    query = """
        INSERT INTO rides (start_time, end_time, distance_m, duration_s, avg_speed_kmh, file_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with get_db_connection() as conn:
        cursor = conn.execute(
            query,
            (start_time, end_time, distance_m, duration_s, avg_speed_kmh, file_path)
        )
        conn.commit()
        return cursor.lastrowid

def get_all_rides() -> List[Dict[str, Any]]:
    """Retrieves all ride records, sorted by start_time descending."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM rides ORDER BY start_time DESC").fetchall()
        return [dict(row) for row in rows]

def get_ride_by_id(ride_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves a single ride record by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM rides WHERE id = ?", (ride_id,)).fetchone()
        return dict(row) if row else None

def clear_db() -> None:
    """Deletes all ride records and resets the auto-increment counter."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM rides")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='rides'")
        conn.commit()
    print("Database cleared successfully.")

if __name__ == "__main__":
    init_db()
