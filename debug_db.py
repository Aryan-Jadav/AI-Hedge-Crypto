import sqlite3
import os

db_path = r"c:\Aryan\projects\AI Hedgefund\Ai-Hedge-Fund-V3\EOS\portfolio.db"
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("--- BACKTESTS ---")
cursor.execute("SELECT * FROM backtests ORDER BY created_at DESC LIMIT 5")
for row in cursor.fetchall():
    print(dict(row))

print("\n--- TRADES (Last 5) ---")
cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 5")
for row in cursor.fetchall():
    print(dict(row))

print("\n--- CRYPTO SESSIONS (Last 5) ---")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_sessions'")
if cursor.fetchone():
    cursor.execute("SELECT * FROM crypto_sessions ORDER BY created_at DESC LIMIT 5")
    for row in cursor.fetchall():
        print(dict(row))
else:
    print("crypto_sessions table not found")

print("\n--- CRYPTO TRADES (Last 5) ---")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_trades'")
if cursor.fetchone():
    cursor.execute("SELECT * FROM crypto_trades ORDER BY id DESC LIMIT 5")
    for row in cursor.fetchall():
        print(dict(row))
else:
    print("crypto_trades table not found")

conn.close()
