import sqlite3
import json

db_path = r"c:\Aryan\projects\AI Hedgefund\Ai-Hedge-Fund-V3\EOS\portfolio.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def check_session(sid):
    print(f"\n--- SESSION: {sid} ---")
    cursor.execute("SELECT * FROM backtests WHERE backtest_id=?", (sid,))
    print("Backtest Record:", dict(cursor.fetchone() or {}))

    cursor.execute("SELECT COUNT(*) as count FROM trades WHERE backtest_id=?", (sid,))
    print("Trade Count:", cursor.fetchone()["count"])

    cursor.execute("SELECT COUNT(*) as count FROM daily_snapshots WHERE backtest_id=?", (sid,))
    print("Snapshot Count:", cursor.fetchone()["count"])

    cursor.execute("SELECT COUNT(*) as count FROM validation_logs WHERE backtest_id=?", (sid,))
    print("Validation Count:", cursor.fetchone()["count"])

print("LAST 3 EOS SESSIONS:")
cursor.execute("SELECT backtest_id FROM backtests ORDER BY created_at DESC LIMIT 3")
sessions = [r["backtest_id"] for r in cursor.fetchall()]
for s in sessions:
    check_session(s)

print("\n--- GLOBAL COUNTS ---")
cursor.execute("SELECT COUNT(*) as count FROM trades")
print("Total Trades:", cursor.fetchone()["count"])
cursor.execute("SELECT COUNT(*) as count FROM validation_logs")
print("Total Validations:", cursor.fetchone()["count"])

conn.close()
