"""
EOS Dashboard - Minimal Web Dashboard for EOS Trading System

Tracks both backtest and live trading sessions.
Dark-themed, modern UI with equity curve, positions, trade history,
AI reasoning logs, and start/stop controls for backtest & live.

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

# Process tracking for start/stop controls
_running_processes = {
    "backtest": None,   # subprocess.Popen object
    "live": None,       # subprocess.Popen object
}
_process_lock = threading.Lock()


def get_db():
    """Get a fresh database connection (thread-safe)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    cursor = conn.cursor()

    # Total sessions
    cursor.execute("SELECT COUNT(*) as count FROM backtests")
    total_sessions = cursor.fetchone()["count"]

    # Total trades
    cursor.execute("SELECT COUNT(*) as count FROM trades")
    total_trades = cursor.fetchone()["count"]

    # Aggregate P&L
    cursor.execute("SELECT COALESCE(SUM(total_pnl), 0) as total_pnl FROM backtests WHERE total_pnl IS NOT NULL")
    total_pnl = cursor.fetchone()["total_pnl"]

    # Win rate across all trades
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
        FROM trades
    """)
    row = cursor.fetchone()
    win_rate = (row["wins"] / row["total"] * 100) if row["total"] > 0 else 0

    # Latest session capital
    cursor.execute("SELECT initial_capital, final_capital FROM backtests ORDER BY created_at DESC LIMIT 1")
    latest = cursor.fetchone()
    current_capital = latest["final_capital"] if latest and latest["final_capital"] else (latest["initial_capital"] if latest else 500000)
    initial_capital = latest["initial_capital"] if latest else 500000

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


@app.route("/api/start_backtest", methods=["POST"])
def api_start_backtest():
    """Start a backtest process."""
    if _is_process_running("backtest"):
        return jsonify({"error": "Backtest already running"}), 409

    data = request.get_json(silent=True) or {}
    # Default to ALL FNO symbols from config when none provided
    from EOS.config import FNO_STOCKS as _fno
    symbols = data.get("symbols") or list(_fno.keys())
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")

    # Build command – run backtest AND save results to DB
    cmd_parts = [
        sys.executable, "-c",
        f"from EOS.eos_backtester import EOSBacktester; "
        f"bt = EOSBacktester(); "
        f"result = bt.run_backtest(symbols={repr(symbols)}, "
        f"start_date={repr(start_date) if start_date else 'None'}, "
        f"end_date={repr(end_date) if end_date else 'None'}); "
        f"bt.print_summary(result); "
        f"bt.save_results_to_db(result)"
    ]

    project_root = str(Path(__file__).parent.parent)
    try:
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            _running_processes["backtest"] = proc

        return jsonify({"status": "started", "pid": proc.pid, "symbols": symbols})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/start_live", methods=["POST"])
def api_start_live():
    """Start a live (paper trade) runner process."""
    if _is_process_running("live"):
        return jsonify({"error": "Live runner already running"}), 409

    data = request.get_json(silent=True) or {}
    # Default to ALL FNO symbols from config when none provided
    from EOS.config import FNO_STOCKS as _fno_live
    symbols = data.get("symbols") or list(_fno_live.keys())
    capital = data.get("initial_capital", 500000)

    cmd_parts = [
        sys.executable, "-c",
        f"from EOS.eos_live_runner import EOSLiveRunner; "
        f"runner = EOSLiveRunner(symbols={repr(symbols)}, "
        f"initial_capital={capital}, paper_trade=True); "
        f"runner.start(); "
        f"import time;\n"
        f"try:\n"
        f"    while runner.is_running: time.sleep(1)\n"
        f"except KeyboardInterrupt: runner.stop()"
    ]

    project_root = str(Path(__file__).parent.parent)
    try:
        with _process_lock:
            proc = subprocess.Popen(
                cmd_parts,
                cwd=project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            _running_processes["live"] = proc

        return jsonify({"status": "started", "pid": proc.pid, "symbols": symbols})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop/<process_type>", methods=["POST"])
def api_stop(process_type):
    """Stop a running backtest or live process."""
    if process_type not in ("backtest", "live"):
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


# ===== MAIN =====

if __name__ == "__main__":
    print("=" * 50)
    print("EOS DASHBOARD")
    print("=" * 50)
    print(f"Database: {DB_PATH}")
    print(f"URL: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, port=5000)
