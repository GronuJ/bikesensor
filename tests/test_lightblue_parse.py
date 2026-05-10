import struct
import tempfile
import pandas as pd
from pathlib import Path
from src.lightblue_parse import parse

def test_parse_lightblue():
    # Construct dummy byte data representing LightBlue hex format
    
    # SYNC 1: 0xA5, idx=0, fs=250, n_axes=6, padding=0
    sync1 = struct.pack("<BIHBB", 0xA5, 0, 250, 6, 0)
    
    # SYNC 2: 0xA5, idx=250, fs=250, n_axes=6, padding=0
    sync2 = struct.pack("<BIHBB", 0xA5, 250, 250, 6, 0)
    
    # DATA: 0x5A, first_idx=0, n_samples=1, 6*int16 BE (zeros)
    data1 = struct.pack("<BIB", 0x5A, 0, 1) + struct.pack(">hhhhhh", 0, 0, 8192, 0, 0, 0) # az=1g
    data2 = struct.pack("<BIB", 0x5A, 1, 1) + struct.pack(">hhhhhh", 0, 0, 8192, 0, 0, 0)
    
    df = pd.DataFrame({
        "time": ["2023-01-01T12:00:00Z", "2023-01-01T12:00:00.004Z", "2023-01-01T12:00:00.008Z", "2023-01-01T12:00:01Z"],
        "hex": [
            sync1.hex(),
            data1.hex(),
            data2.hex(),
            sync2.hex()
        ]
    })
    
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name
        df.to_csv(tmp_path, index=False)
        
    try:
        parsed = parse(tmp_path)
        assert len(parsed) == 2
        assert "timestamp" in parsed.columns
        assert "ax" in parsed.columns
        assert parsed["sample_idx"].iloc[0] == 0
        assert parsed["sample_idx"].iloc[1] == 1
        assert parsed["az"].iloc[0] == 1.0 # 8192 / 8192 = 1.0
    finally:
        Path(tmp_path).unlink()
