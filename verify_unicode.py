import sys
import os
from pathlib import Path

# Mocking enough to run the reconfiguration block
try:
    # Force UTF-8 so Rs / rupee symbol (₹) never crashes on Windows cp1252
    for _stream in (sys.stdout, sys.stderr):
        if _stream and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    
    print("Testing Rupees symbol: ₹")
    print("Unicode test successful!")
except Exception as e:
    print(f"Unicode test failed with error: {e}")
    sys.exit(1)
