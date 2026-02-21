import sqlite3
import json
import os

# We can import from eos_dashboard if we set up the environment, 
# but easier to just mock the Flask context or test the DB logic directly.

db_path = r"c:\Aryan\projects\AI Hedgefund\Ai-Hedge-Fund-V3\EOS\portfolio.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

def test_overview():
    print("\n--- Testing EOS Overview Logic ---")
    cursor.execute("SELECT COUNT(*) as count FROM backtests")
    total_sessions = cursor.fetchone()["count"]
    
    cursor.execute("SELECT SUM(total_pnl) as total_pnl FROM backtests")
    row = cursor.fetchone()
    total_pnl = row["total_pnl"] if row and row["total_pnl"] is not None else 0.0
    
    cursor.execute("SELECT initial_capital, final_capital FROM backtests ORDER BY created_at DESC LIMIT 1")
    latest = cursor.fetchone()
    current_capital = 500000.0
    initial_capital = 500000.0
    if latest:
        initial_capital = latest["initial_capital"]
        current_capital = latest["final_capital"] if latest["final_capital"] is not None else initial_capital
        
    print(f"Total Sessions: {total_sessions}")
    print(f"Total PnL: {total_pnl}")
    print(f"Current Capital: {current_capital}")
    print(f"Initial Capital: {initial_capital}")

def test_crypto_overview():
    print("\n--- Testing Crypto Overview Logic ---")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_sessions'")
    if not cursor.fetchone():
        print("No crypto_sessions table")
        return

    cursor.execute("SELECT SUM(total_pnl_usdt) as total_pnl FROM crypto_sessions")
    row_pnl = cursor.fetchone()
    total_pnl = row_pnl["total_pnl"] if row_pnl and row_pnl["total_pnl"] is not None else 0.0
    
    cursor.execute("SELECT initial_capital_usdt, final_capital_usdt FROM crypto_sessions ORDER BY created_at DESC LIMIT 1")
    latest = cursor.fetchone()
    initial_cap = 10000.0
    current_cap = 10000.0
    if latest:
        initial_cap = latest["initial_capital_usdt"]
        current_cap = latest["final_capital_usdt"] if latest["final_capital_usdt"] is not None else initial_cap

    print(f"Total Crypto PnL: {total_pnl}")
    print(f"Current Crypto Cap: {current_cap}")

test_overview()
test_crypto_overview()
conn.close()
