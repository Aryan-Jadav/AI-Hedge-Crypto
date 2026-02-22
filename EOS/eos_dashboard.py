# Last updated: 2026-02-21
"""
EOS + CRYPTO Dashboard - Unified Web Dashboard for AI Hedgefund

Tracks EOS (Indian F&O) and CRYPTO (Bybit Perps) sessions.
Dark-themed, modern UI with equity curve, positions, trade history,
AI reasoning logs, and start/stop controls for all runners.

Usage:
    python -m EOS.eos_dashboard
"""

import sqlite3
import json
import threading
import subprocess
import sys
import signal
import os
from pathlib import Path
from flask import Flask, jsonify, request, render_template

# ===== APP SETUP =====

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

DB_PATH = str(Path(__file__).parent / "portfolio.db")
EOS_DIR = str(Path(__file__).parent)
PROJECT_ROOT = str(Path(__file__).parent.parent)

# Ensure PROJECT_ROOT is on sys.path so "EOS" and "CRYPTO" packages are always importable,
# regardless of whether this file is launched via `python -m EOS.eos_dashboard` or
# directly as `python EOS/eos_dashboard.py`.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Log directory for subprocess output
LOG_DIR = str(Path(__file__).parent / "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Process tracking for start/stop controls
_running_processes = {
    "backtest":       None,   # subprocess.Popen object
    "live":           None,   # subprocess.Popen object
    "crypto_live":    None,   # CRYPTO live runner subprocess
    "crypto_backtest": None,  # CRYPTO backtest subprocess
}
_process_lock = threading.Lock()

# Log file paths
_log_files = {
    "backtest":       os.path.join(LOG_DIR, "backtest.log"),
    "live":           os.path.join(LOG_DIR, "live.log"),
    "crypto_live":    os.path.join(LOG_DIR, "crypto_live.log"),
    "crypto_backtest": os.path.join(LOG_DIR, "crypto_backtest.log"),
}


def _get_log_path(process_type: str) -> str:
    """Get log file path for a process type."""
    return _log_files.get(process_type, os.path.join(LOG_DIR, f"{process_type}.log"))


def _open_log_file(process_type: str):
    """Open (or create) a log file for subprocess output."""
    path = _get_log_path(process_type)
    return open(path, "w", buffering=1, encoding="utf-8")


def _read_log_tail(process_type: str, lines: int = 100) -> str:
    """Read the last N lines from a process log file."""
    path = _get_log_path(process_type)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception:
        return ""


def get_db():
    """Get a fresh database connection (thread-safe)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _eos_table_exists(conn, table_name: str) -> bool:
    """Check if an EOS table exists in the database."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


# ===== PAGE ROUTES =====

@app.route("/")
def dashboard():
    """Serve the main dashboard page."""
    return render_template("dashboard.html")


# ===== API ROUTES =====

@app.route("/api/overview")
def api_overview():
    """Overall portfolio statistics across all sessions."""
    conn = get_db()

    if not _eos_table_exists(conn, "backtests"):
        conn.close()
        return jsonify({
            "total_sessions": 0, "total_trades": 0, "total_pnl": 0.0,
            "win_rate": 0.0, "current_capital": 500000.0, "initial_capital": 500000.0
        })

    cursor = conn.cursor()

    # Total sessions
    cursor.execute("SELECT COUNT(*) as count FROM backtests")
    total_sessions = cursor.fetchone()["count"]

    # Total trades
    trades_exist = _eos_table_exists(conn, "trades")
    total_trades = 0
    win_rate = 0.0
    if trades_exist:
        cursor.execute("SELECT COUNT(*) as count FROM trades")
        total_trades = cursor.fetchone()["count"]
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades
        """)
        row = cursor.fetchone()
        win_rate = (row["wins"] / row["total"] * 100) if row["total"] > 0 else 0

    # Aggregate P&L
    cursor.execute("SELECT SUM(total_pnl) as total_pnl FROM backtests")
    row = cursor.fetchone()
    total_pnl = row["total_pnl"] if row and row["total_pnl"] is not None else 0.0

    # Latest session capital
    cursor.execute("SELECT initial_capital, final_capital FROM backtests ORDER BY created_at DESC LIMIT 1")
    latest = cursor.fetchone()
    current_capital = 500000.0
    initial_capital = 500000.0
    if latest:
        initial_capital = latest["initial_capital"]
        current_capital = latest["final_capital"] if latest["final_capital"] is not None else initial_capital

    conn.close()
    return jsonify({
        "total_sessions": total_sessions,
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "current_capital": round(current_capital, 2),
        "initial_capital": round(initial_capital, 2)
    })



@app.route("/api/sessions")
def api_sessions():
    """List all trading sessions (backtests + live)."""
    conn = get_db()
    if not _eos_table_exists(conn, "backtests"):
        conn.close()
        return jsonify([])
    cursor = conn.cursor()
    cursor.execute("""
        SELECT backtest_id, start_date, end_date, symbols, initial_capital,
               final_capital, total_pnl, total_trades, win_rate, max_drawdown,
               created_at, config_json
        FROM backtests
        ORDER BY created_at DESC
    """)
    sessions = []
    for row in cursor.fetchall():
        session = dict(row)
        # Parse config to get mode
        config = {}
        if session.get("config_json"):
            try:
                config = json.loads(session["config_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        session["mode"] = config.get("mode", "BACKTEST")
        sessions.append(session)

    conn.close()
    return jsonify(sessions)


@app.route("/api/session/<session_id>")
def api_session_detail(session_id):
    """Get detailed info for a specific session."""
    conn = get_db()
    if not _eos_table_exists(conn, "backtests"):
        conn.close()
        return jsonify({"error": "Session not found"}), 404
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM backtests WHERE backtest_id = ?", (session_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Session not found"}), 404

    session = dict(row)

    # Get trades
    cursor.execute("SELECT * FROM trades WHERE backtest_id = ? ORDER BY entry_date, entry_time", (session_id,))
    trades = [dict(r) for r in cursor.fetchall()]

    # Get snapshots
    cursor.execute("SELECT * FROM daily_snapshots WHERE backtest_id = ? ORDER BY date", (session_id,))
    snapshots = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return jsonify({
        "session": session,
        "trades": trades,
        "snapshots": snapshots
    })


@app.route("/api/trades")
def api_trades():
    """Get trades, optionally filtered by session_id."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _eos_table_exists(conn, "trades"):
        conn.close()
        return jsonify([])
    cursor = conn.cursor()

    if session_id:
        cursor.execute("""
            SELECT * FROM trades WHERE backtest_id = ?
            ORDER BY entry_date DESC, entry_time DESC
        """, (session_id,))
    else:
        cursor.execute("SELECT * FROM trades ORDER BY entry_date DESC, entry_time DESC LIMIT 100")

    trades = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(trades)


@app.route("/api/snapshots")
def api_snapshots():
    """Get daily snapshots, optionally filtered by session_id."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _eos_table_exists(conn, "daily_snapshots"):
        conn.close()
        return jsonify([])
    cursor = conn.cursor()

    if session_id:
        cursor.execute("SELECT * FROM daily_snapshots WHERE backtest_id = ? ORDER BY date", (session_id,))
    else:
        cursor.execute("SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 100")

    snapshots = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(snapshots)


@app.route("/api/equity_curve")
def api_equity_curve():
    """Get equity curve data for charting."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _eos_table_exists(conn, "daily_snapshots"):
        conn.close()
        return jsonify([])
    cursor = conn.cursor()

    if session_id:
        # Single session equity curve
        cursor.execute("""
            SELECT date, ending_capital, daily_pnl, daily_pnl_pct
            FROM daily_snapshots WHERE backtest_id = ? ORDER BY date
        """, (session_id,))
    else:
        # Aggregate: latest snapshot per date across all sessions
        cursor.execute("""
            SELECT date, ending_capital, daily_pnl, daily_pnl_pct
            FROM daily_snapshots ORDER BY date
        """)

    data = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(data)


@app.route("/api/positions")
def api_positions():
    """Get open positions."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _eos_table_exists(conn, "positions"):
        conn.close()
        return jsonify([])
    cursor = conn.cursor()

    query = "SELECT * FROM positions WHERE status = 'OPEN'"
    params = []
    if session_id:
        query += " AND backtest_id = ?"
        params.append(session_id)

    cursor.execute(query, params)
    positions = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(positions)


# ===== AI VALIDATION LOGS =====

@app.route("/api/validations")
def api_validations():
    """Get AI validation logs, optionally filtered by session_id."""
    session_id = request.args.get("session_id")
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    cursor = conn.cursor()

    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='validation_logs'")
    if not cursor.fetchone():
        conn.close()
        return jsonify([])

    if session_id:
        cursor.execute("""
            SELECT * FROM validation_logs WHERE backtest_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (session_id, limit))
    else:
        cursor.execute("SELECT * FROM validation_logs ORDER BY timestamp DESC LIMIT ?", (limit,))

    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(logs)


# ===== START / STOP CONTROLS =====

def _is_process_running(key: str) -> bool:
    """Check if a managed process is still running."""
    with _process_lock:
        proc = _running_processes.get(key)
        if proc is None:
            return False
        if proc.poll() is not None:
            _running_processes[key] = None
            return False
        return True


@app.route("/api/status")
def api_status():
    """Get status of backtest and live runner processes."""
    return jsonify({
        "backtest_running": _is_process_running("backtest"),
        "live_running": _is_process_running("live")
    })


@app.route("/api/credentials_status")
def api_credentials_status():
    """Check which API credentials are configured."""
    from EOS.config import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
    from CRYPTO.config import BYBIT_API_KEY, BYBIT_API_SECRET
    return jsonify({
        "dhan_configured": bool(DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN),
        "bybit_configured": bool(BYBIT_API_KEY and BYBIT_API_SECRET),
        "bybit_public_ok": True,   # Bybit public endpoints never need auth
    })


@app.route("/api/start_backtest", methods=["POST"])
def api_start_backtest():
    """Start a backtest via the EOS.run_backtest standalone launcher."""
    if _is_process_running("backtest"):
        return jsonify({"error": "Backtest already running"}), 409

    data = request.get_json(silent=True) or {}
    from EOS.config import FNO_STOCKS as _fno
    symbols    = data.get("symbols") or list(_fno.keys())
    start_date = data.get("start_date", "")
    end_date   = data.get("end_date", "")
    capital    = data.get("initial_capital", 500000)
    expiry     = data.get("expiry_code", 1)

    cmd_parts = [
        sys.executable, "-m", "EOS.run_backtest",
        "--capital", str(capital),
        "--expiry-code", str(expiry),
    ]
    if symbols:
        cmd_parts += ["--symbols"] + symbols
    if start_date:
        cmd_parts += ["--start", start_date]
    if end_date:
        cmd_parts += ["--end", end_date]

    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        log_fh = _open_log_file("backtest")
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=PROJECT_ROOT,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            )
            _running_processes["backtest"] = proc

        return jsonify({
            "status": "started", "pid": proc.pid,
            "symbols": symbols, "start_date": start_date, "end_date": end_date,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/start_live", methods=["POST"])
def api_start_live():
    """Start the EOS live runner (paper or live mode)."""
    if _is_process_running("live"):
        return jsonify({"error": "Live runner already running"}), 409

    data = request.get_json(silent=True) or {}
    from EOS.config import FNO_STOCKS as _fno_live
    symbols     = data.get("symbols") or list(_fno_live.keys())
    capital     = data.get("initial_capital", 500000)
    paper_trade = data.get("paper_trade", True)

    cmd_parts = [
        sys.executable, "-m", "EOS.run_live",
        "--symbols", *symbols,
        "--capital", str(capital),
    ]
    if paper_trade:
        cmd_parts.append("--paper")

    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        log_fh = _open_log_file("live")
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=PROJECT_ROOT,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            )
            _running_processes["live"] = proc

        mode_str = "PAPER" if paper_trade else "LIVE"
        return jsonify({
            "status": "started", "pid": proc.pid,
            "mode": mode_str, "symbols": symbols,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop/<process_type>", methods=["POST"])
def api_stop(process_type):
    """Stop a running backtest or live process."""
    if process_type not in ("backtest", "live", "crypto_live", "crypto_backtest"):
        return jsonify({"error": "Invalid process type"}), 400

    with _process_lock:
        proc = _running_processes.get(process_type)
        if proc is None or proc.poll() is not None:
            _running_processes[process_type] = None
            return jsonify({"status": "not_running"})

        try:
            if os.name == 'nt':
                proc.terminate()
            else:
                proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            _running_processes[process_type] = None

    return jsonify({"status": "stopped"})


# =====================================================================
# ===== CRYPTO API ROUTES (CRYPTO-EOS Bybit Perps Module) =====
# =====================================================================

def _crypto_table_exists(conn, table_name: str) -> bool:
    """Check if a crypto table exists in the shared DB."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


@app.route("/api/crypto/overview")
def api_crypto_overview():
    """Crypto portfolio summary across all sessions."""
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_sessions"):
        conn.close()
        return jsonify({
            "total_sessions": 0, "total_trades": 0,
            "total_pnl_usdt": 0.0, "win_rate": 0.0,
            "current_capital_usdt": 0.0, "initial_capital_usdt": 0.0,
        })

    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as count FROM crypto_sessions")
    total_sessions = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM crypto_trades WHERE status='CLOSED'")
    total_trades = cursor.fetchone()["count"]

    cursor.execute("SELECT SUM(total_pnl_usdt) as total_pnl FROM crypto_sessions")
    row_pnl = cursor.fetchone()
    total_pnl = row_pnl["total_pnl"] if row_pnl and row_pnl["total_pnl"] is not None else 0.0

    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins
        FROM crypto_trades WHERE status='CLOSED'
    """)
    row = cursor.fetchone()
    win_rate = 0.0
    if row and row["total"] and row["total"] > 0:
        win_rate = (row["wins"] / row["total"] * 100)

    cursor.execute("""
        SELECT initial_capital_usdt, final_capital_usdt
        FROM crypto_sessions ORDER BY created_at DESC LIMIT 1
    """)
    latest = cursor.fetchone()
    initial_cap = 10000.0
    current_cap = 10000.0
    if latest:
        initial_cap = latest["initial_capital_usdt"]
        current_cap = latest["final_capital_usdt"] if latest["final_capital_usdt"] is not None else initial_cap

    conn.close()
    return jsonify({
        "total_sessions":       total_sessions,
        "total_trades":         total_trades,
        "total_pnl_usdt":       round(total_pnl, 2),
        "win_rate":             round(win_rate, 1),
        "current_capital_usdt": round(current_cap, 2),
        "initial_capital_usdt": round(initial_cap, 2),
    })



@app.route("/api/crypto/sessions")
def api_crypto_sessions():
    """List all crypto trading sessions."""
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_sessions"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    cursor.execute("""
        SELECT session_id, start_date, end_date, symbols,
               initial_capital_usdt, final_capital_usdt,
               total_pnl_usdt, total_trades, win_rate,
               max_drawdown_usdt, created_at, mode
        FROM crypto_sessions ORDER BY created_at DESC
    """)
    sessions = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(sessions)


@app.route("/api/crypto/session/<session_id>")
def api_crypto_session_detail(session_id):
    """Detail view for a crypto session."""
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_sessions"):
        conn.close()
        return jsonify({"error": "Crypto tables not initialized"}), 404

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM crypto_sessions WHERE session_id=?", (session_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Session not found"}), 404

    session = dict(row)

    cursor.execute("""
        SELECT * FROM crypto_trades WHERE session_id=?
        ORDER BY entry_date, entry_time
    """, (session_id,))
    trades = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT * FROM crypto_daily_snapshots WHERE session_id=? ORDER BY date
    """, (session_id,))
    snapshots = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return jsonify({"session": session, "trades": trades, "snapshots": snapshots})


@app.route("/api/crypto/trades")
def api_crypto_trades():
    """Get crypto trades, optional ?session_id= filter."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_trades"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    if session_id:
        cursor.execute("""
            SELECT * FROM crypto_trades WHERE session_id=? AND status='CLOSED'
            ORDER BY entry_date DESC, entry_time DESC
        """, (session_id,))
    else:
        cursor.execute("""
            SELECT * FROM crypto_trades WHERE status='CLOSED'
            ORDER BY entry_date DESC, entry_time DESC LIMIT 100
        """)

    trades = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(trades)


@app.route("/api/crypto/snapshots")
def api_crypto_snapshots():
    """Get crypto daily snapshots."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_daily_snapshots"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    if session_id:
        cursor.execute(
            "SELECT * FROM crypto_daily_snapshots WHERE session_id=? ORDER BY date",
            (session_id,),
        )
    else:
        cursor.execute("SELECT * FROM crypto_daily_snapshots ORDER BY date DESC LIMIT 100")

    snapshots = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(snapshots)


@app.route("/api/crypto/equity_curve")
def api_crypto_equity_curve():
    """Crypto equity curve data for Chart.js."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_daily_snapshots"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    if session_id:
        cursor.execute("""
            SELECT date, ending_capital_usdt, daily_pnl_usdt, daily_pnl_pct
            FROM crypto_daily_snapshots WHERE session_id=? ORDER BY date
        """, (session_id,))
    else:
        cursor.execute("""
            SELECT date, ending_capital_usdt, daily_pnl_usdt, daily_pnl_pct
            FROM crypto_daily_snapshots ORDER BY date
        """)

    data = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(data)


@app.route("/api/crypto/positions")
def api_crypto_positions():
    """Get open crypto positions."""
    session_id = request.args.get("session_id")
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_positions"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    query = "SELECT * FROM crypto_positions WHERE status='OPEN'"
    params = []
    if session_id:
        query += " AND session_id=?"
        params.append(session_id)
    cursor.execute(query, params)
    positions = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(positions)


@app.route("/api/crypto/validations")
def api_crypto_validations():
    """Get crypto AI validation logs."""
    session_id = request.args.get("session_id")
    limit = request.args.get("limit", 50, type=int)
    conn = get_db()
    if not _crypto_table_exists(conn, "crypto_validation_logs"):
        conn.close()
        return jsonify([])

    cursor = conn.cursor()
    if session_id:
        cursor.execute("""
            SELECT * FROM crypto_validation_logs WHERE session_id=?
            ORDER BY timestamp DESC LIMIT ?
        """, (session_id, limit))
    else:
        cursor.execute(
            "SELECT * FROM crypto_validation_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify(logs)


def _fetch_prices_bybit(pairs_keys):
    """Try Bybit REST API (primary). Returns list of price dicts or None on failure."""
    try:
        from CRYPTO.data_fetcher import CryptoDataFetcher
        df = CryptoDataFetcher()

        # Try primary domain first, then bytick.com fallback
        for base in (df.base_url, "https://api.bytick.com/v5"):
            df.base_url = base
            result = df.get_all_tickers()
            if result.get("error"):
                continue
            tickers = result.get("data", {}).get("list", [])
            pair_set = set(pairs_keys)
            prices = []
            for t in tickers:
                sym = t.get("symbol", "")
                if sym in pair_set:
                    prices.append({
                        "symbol":        sym,
                        "last_price":    float(t.get("lastPrice", 0)),
                        "change_pct_24h": float(t.get("price24hPcnt", 0)) * 100,
                        "high_24h":      float(t.get("highPrice24h", 0)),
                        "low_24h":       float(t.get("lowPrice24h", 0)),
                        "volume_24h":    float(t.get("volume24h", 0)),
                        "funding_rate":  float(t.get("fundingRate", 0)),
                        "open_interest": float(t.get("openInterest", 0)),
                    })
            if prices:
                return prices
    except Exception:
        pass
    return None


def _fetch_prices_coingecko(pairs_keys):
    """Fallback: CoinGecko free API (no key needed, works from any IP)."""
    import requests as _req
    # Map Bybit symbols → CoinGecko IDs
    _CG_MAP = {
        "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
        "BNBUSDT": "binancecoin", "XRPUSDT": "ripple",
    }
    cg_ids = [_CG_MAP[s] for s in pairs_keys if s in _CG_MAP]
    if not cg_ids:
        return None
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        resp = _req.get(url, params={
            "vs_currency": "usd", "ids": ",".join(cg_ids),
            "order": "market_cap_desc", "sparkline": "false",
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Reverse map CoinGecko ID → Bybit symbol
        _REV = {v: k for k, v in _CG_MAP.items()}
        prices = []
        for coin in data:
            sym = _REV.get(coin["id"])
            if sym and sym in pairs_keys:
                prices.append({
                    "symbol":        sym,
                    "last_price":    float(coin.get("current_price", 0)),
                    "change_pct_24h": float(coin.get("price_change_percentage_24h", 0)),
                    "high_24h":      float(coin.get("high_24h", 0)),
                    "low_24h":       float(coin.get("low_24h", 0)),
                    "volume_24h":    float(coin.get("total_volume", 0)),
                    "funding_rate":  0.0,   # CoinGecko doesn't have funding rate
                    "open_interest": 0.0,   # CoinGecko doesn't have OI
                })
        return prices if prices else None
    except Exception:
        return None


@app.route("/api/crypto/live_prices")
def api_crypto_live_prices():
    """Fetch live crypto prices. Tries Bybit first, falls back to CoinGecko."""
    try:
        from CRYPTO.config import CRYPTO_PAIRS
        pairs_keys = list(CRYPTO_PAIRS.keys())

        # Try Bybit (primary + bytick.com fallback)
        prices = _fetch_prices_bybit(pairs_keys)
        source = "bybit"

        # Fallback to CoinGecko if Bybit is blocked (403 on US servers)
        if not prices:
            prices = _fetch_prices_coingecko(pairs_keys)
            source = "coingecko"

        if not prices:
            return jsonify({"error": "All price sources failed"}), 503

        # Sort by configured order
        prices.sort(key=lambda x: pairs_keys.index(x["symbol"]) if x["symbol"] in pairs_keys else 99)
        return jsonify({"prices": prices, "count": len(prices), "source": source})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crypto/status")
def api_crypto_status():
    """Status of crypto live runner process."""
    return jsonify({
        "crypto_live_running":      _is_process_running("crypto_live"),
        "crypto_backtest_running":  _is_process_running("crypto_backtest"),
    })


@app.route("/api/crypto/start_backtest", methods=["POST"])
def api_crypto_start_backtest():
    """Start a CRYPTO backtest via Bybit public klines (no auth needed)."""
    if _is_process_running("crypto_backtest"):
        return jsonify({"error": "Crypto backtest already running"}), 409

    data = request.get_json(silent=True) or {}
    from CRYPTO.config import CRYPTO_PAIRS as _pairs, CRYPTO_CONFIG as _ccfg
    symbols    = data.get("symbols") or list(_pairs.keys())
    capital    = data.get("initial_capital_usdt", _ccfg["total_capital_usdt"])
    start_date = data.get("start_date", "")
    end_date   = data.get("end_date", "")
    days       = data.get("days", 30)

    cmd_parts = [
        sys.executable, "-m", "CRYPTO.run_crypto_backtest",
        "--symbols", *symbols,
        "--capital", str(capital),
        "--days",    str(days),
    ]
    if start_date:
        cmd_parts += ["--start", start_date]
    if end_date:
        cmd_parts += ["--end", end_date]

    try:
        env    = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        log_fh = _open_log_file("crypto_backtest")
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=PROJECT_ROOT,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            _running_processes["crypto_backtest"] = proc

        return jsonify({
            "status":   "started",
            "pid":      proc.pid,
            "symbols":  symbols,
            "start_date": start_date,
            "end_date":   end_date,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crypto/start_live", methods=["POST"])
def api_crypto_start_live():
    """Start the CRYPTO live runner as a subprocess."""
    if _is_process_running("crypto_live"):
        return jsonify({"error": "Crypto runner already running"}), 409

    data = request.get_json(silent=True) or {}
    paper_trade = data.get("paper_trade", True)
    from CRYPTO.config import CRYPTO_PAIRS as _pairs, CRYPTO_CONFIG as _ccfg
    symbols = data.get("symbols") or list(_pairs.keys())
    capital = data.get("initial_capital_usdt", _ccfg["total_capital_usdt"])

    # Use standalone launcher script
    cmd_parts = [
        sys.executable, "-m", "CRYPTO.run_crypto_live",
        "--symbols", *symbols,
        "--capital", str(capital),
    ]
    if paper_trade:
        cmd_parts.append("--paper")

    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        log_fh = _open_log_file("crypto_live")
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=PROJECT_ROOT,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            _running_processes["crypto_live"] = proc

        mode_str = "PAPER" if paper_trade else "LIVE"
        return jsonify({
            "status":   "started",
            "pid":      proc.pid,
            "mode":     mode_str,
            "symbols":  symbols,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crypto/stop_live", methods=["POST"])
def api_crypto_stop_live():
    """Stop the crypto live runner subprocess."""
    with _process_lock:
        proc = _running_processes.get("crypto_live")
        if proc is None or proc.poll() is not None:
            _running_processes["crypto_live"] = None
            return jsonify({"status": "not_running"})

        try:
            if os.name == "nt":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            _running_processes["crypto_live"] = None

    return jsonify({"status": "stopped"})


# ===== PROCESS LOG ENDPOINTS =====

@app.route("/api/logs/<process_type>")
def api_logs(process_type):
    """Get subprocess log output (last N lines)."""
    if process_type not in _log_files:
        return jsonify({"error": "Invalid process type"}), 400
    lines = request.args.get("lines", 100, type=int)
    content = _read_log_tail(process_type, lines)
    return jsonify({
        "process_type": process_type,
        "running": _is_process_running(process_type),
        "log": content,
    })


@app.route("/api/logs/<process_type>/clear", methods=["POST"])
def api_logs_clear(process_type):
    """Clear a process log file."""
    if process_type not in _log_files:
        return jsonify({"error": "Invalid process type"}), 400
    path = _get_log_path(process_type)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return jsonify({"status": "cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===== MAIN =====

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 55)
    print("EOS + CRYPTO UNIFIED DASHBOARD")
    print("=" * 55)
    print(f"Database: {DB_PATH}")
    print(f"URL:      http://localhost:{port}")
    print("=" * 55)
    app.run(debug=False, host="0.0.0.0", port=port)
