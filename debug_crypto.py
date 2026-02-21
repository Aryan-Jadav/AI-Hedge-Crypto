import sqlite3
import json

db_path = r"c:\Aryan\projects\AI Hedgefund\Ai-Hedge-Fund-V3\EOS\portfolio.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("--- CRYPTO SESSIONS ---")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_sessions'")
if cursor.fetchone():
    cursor.execute("SELECT * FROM crypto_sessions")
    for row in cursor.fetchall():
        print(dict(row))
else:
    print("No crypto_sessions table")

print("\n--- CRYPTO TRADES ---")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_trades'")
if cursor.fetchone():
    cursor.execute("SELECT COUNT(*) as count FROM crypto_trades")
    print(f"Total Crypto Trades: {cursor.fetchone()['count']}")
    cursor.execute("SELECT * FROM crypto_trades LIMIT 5")
    for row in cursor.fetchall():
        print(dict(row))

conn.close()
