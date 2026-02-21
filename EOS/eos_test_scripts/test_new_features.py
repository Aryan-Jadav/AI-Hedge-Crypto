# Last updated: 2026-02-21
"""Quick test for new dashboard features: validation_logs, start/stop, AI reasoning."""
import sqlite3
import sys
import os
from pathlib import Path

EOS_DIR = Path(__file__).parent.parent
DB_PATH = str(EOS_DIR / "portfolio.db")
ROOT = str(EOS_DIR.parent)

def test_validation_logs_table():
    """Check validation_logs table exists in DB (or can be created)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"  Tables in DB: {tables}")
    if "validation_logs" not in tables:
        print("  [WARN] validation_logs table not yet created. Creating now...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS validation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id TEXT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT,
                result TEXT NOT NULL,
                confidence REAL,
                reason TEXT,
                tier_used TEXT,
                latency_ms INTEGER,
                tokens_used INTEGER,
                price_change_pct REAL,
                oi_change_pct REAL,
                entry_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id)
            )
        """)
        conn.commit()
    print("  OK: validation_logs table exists")
    conn.close()
    return True

def test_record_validation():
    """Insert and read a test validation record."""
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Ensure table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='validation_logs'")
    if not cursor.fetchone():
        test_validation_logs_table()
        cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO validation_logs (backtest_id, timestamp, symbol, signal_type, result,
            confidence, reason, tier_used, price_change_pct, oi_change_pct, entry_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("TEST_SESSION", datetime.now().isoformat(), "RELIANCE", "PUT", "APPROVE",
          0.85, "Strong signal: price +3.2%, OI +2.1%", "STRATEGY", 3.2, 2.1, 245.50))
    conn.commit()

    cursor.execute("SELECT * FROM validation_logs WHERE backtest_id='TEST_SESSION' ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    assert row is not None, "Failed to insert validation record"
    print(f"  Inserted validation: id={row[0]}, symbol={row[3]}, result={row[5]}")

    # Cleanup
    cursor.execute("DELETE FROM validation_logs WHERE backtest_id='TEST_SESSION'")
    conn.commit()
    conn.close()
    print("  OK: record_validation works")
    return True

def test_dashboard_api_endpoints():
    """Test all new API endpoints via Flask test client."""
    sys.path.insert(0, ROOT)
    from EOS.eos_dashboard import app
    client = app.test_client()

    # /api/validations
    r = client.get("/api/validations")
    assert r.status_code == 200, f"validations returned {r.status_code}"
    print(f"  /api/validations -> 200, {len(r.get_json())} entries")

    # /api/status
    r = client.get("/api/status")
    assert r.status_code == 200
    d = r.get_json()
    assert "backtest_running" in d
    assert "live_running" in d
    print(f"  /api/status -> 200, backtest={d['backtest_running']}, live={d['live_running']}")

    # /api/stop/backtest (should return not_running)
    r = client.post("/api/stop/backtest")
    assert r.status_code == 200
    print(f"  /api/stop/backtest -> 200, {r.get_json()}")

    # /api/stop/live
    r = client.post("/api/stop/live")
    assert r.status_code == 200
    print(f"  /api/stop/live -> 200, {r.get_json()}")

    # /api/stop/invalid
    r = client.post("/api/stop/invalid")
    assert r.status_code == 400
    print(f"  /api/stop/invalid -> 400 (correct)")

    # Existing endpoints still work
    for ep in ["/api/overview", "/api/sessions", "/api/trades", "/api/positions", "/api/equity_curve"]:
        r = client.get(ep)
        assert r.status_code == 200, f"{ep} returned {r.status_code}"
    print("  All existing endpoints still return 200")

    # Dashboard HTML loads
    r = client.get("/")
    assert r.status_code == 200
    html = r.data.decode()
    assert "AI Reasoning Log" in html, "AI Reasoning panel missing from HTML"
    assert "btnStartBT" in html, "Start backtest button missing"
    assert "btnStopLive" in html, "Stop live button missing"
    print("  Dashboard HTML contains new controls and AI panel")

    return True

if __name__ == "__main__":
    print("=" * 50)
    print("TESTING NEW DASHBOARD FEATURES")
    print("=" * 50)
    passed = 0
    total = 3
    tests = [
        ("Validation Logs Table", test_validation_logs_table),
        ("Record Validation", test_record_validation),
        ("Dashboard API Endpoints", test_dashboard_api_endpoints),
    ]
    for name, fn in tests:
        print(f"\n[TEST] {name}")
        try:
            fn()
            passed += 1
            print(f"  PASSED")
        except Exception as e:
            print(f"  FAILED: {e}")

    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} passed")
    print("=" * 50)

