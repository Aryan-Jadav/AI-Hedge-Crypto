import sqlite3
import os
import json

db_path = r"c:\Aryan\projects\AI Hedgefund\Ai-Hedge-Fund-V3\EOS\portfolio.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def get_overview():
    cursor.execute("SELECT COUNT(*) as count FROM backtests")
    total_sessions = cursor.fetchone()["count"]
    cursor.execute("SELECT COUNT(*) as count FROM trades")
    total_trades = cursor.fetchone()["count"]
    cursor.execute("SELECT SUM(total_pnl) as total_pnl FROM backtests WHERE total_pnl IS NOT NULL")
    total_pnl = cursor.fetchone()["total_pnl"] or 0
    cursor.execute("SELECT AVG(win_rate) as win_rate FROM backtests WHERE win_rate IS NOT NULL")
    win_rate = cursor.fetchone()["win_rate"] or 0
    cursor.execute("SELECT initial_capital, final_capital FROM backtests ORDER BY created_at DESC LIMIT 1")
    latest = cursor.fetchone()
    initial_cap = latest["initial_capital"] if latest else 500000
    current_cap = latest["final_capital"] if latest and latest["final_capital"] else initial_cap
    return {
        "total_sessions": total_sessions,
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "current_capital": round(current_cap, 2),
        "initial_capital": round(initial_cap, 2)
    }

print("EOS OVERVIEW:")
print(json.dumps(get_overview(), indent=2))

print("\nLATEST BACKTEST:")
cursor.execute("SELECT * FROM backtests ORDER BY created_at DESC LIMIT 1")
latest_bt = cursor.fetchone()
if latest_bt:
    print(dict(latest_bt))
    bt_id = latest_bt['backtest_id']
    cursor.execute("SELECT COUNT(*) as count FROM trades WHERE backtest_id=?", (bt_id,))
    print(f"Trades for {bt_id}: {cursor.fetchone()['count']}")
    cursor.execute("SELECT COUNT(*) as count FROM daily_snapshots WHERE backtest_id=?", (bt_id,))
    print(f"Snapshots for {bt_id}: {cursor.fetchone()['count']}")

print("\nCRYPTO OVERVIEW (approx):")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_sessions'")
if cursor.fetchone():
    cursor.execute("SELECT COUNT(*) as count FROM crypto_sessions")
    print(f"Total Crypto Sessions: {cursor.fetchone()['count']}")
    cursor.execute("SELECT SUM(total_pnl_usdt) as total_pnl FROM crypto_sessions")
    print(f"Total Crypto PnL: {cursor.fetchone()['total_pnl']}")

conn.close()
