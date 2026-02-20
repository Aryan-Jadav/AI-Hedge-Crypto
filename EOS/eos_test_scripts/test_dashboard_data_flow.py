"""
Test that all dashboard data-flow changes are working:
  1. API endpoints return valid data
  2. Frontend has all new features (no dummy data, no prompt())
  3. save_results_to_db method works end-to-end
  4. Backtest subprocess command includes DB save
"""
import urllib.request
import json
import sys
import os

BASE = "http://localhost:5000"
passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  OK: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1


def api(path):
    r = urllib.request.urlopen(BASE + path)
    return r.status, json.loads(r.read().decode())


print("=" * 55)
print("TESTING DASHBOARD DATA-FLOW CHANGES")
print("=" * 55)

# --- Test 1: All API endpoints ---
print("\n[TEST 1] API Endpoints return valid JSON")
for ep in ["/api/overview", "/api/sessions", "/api/trades",
           "/api/equity_curve", "/api/positions", "/api/validations", "/api/status"]:
    status, data = api(ep)
    check(f"{ep} -> {status}", status == 200)

# --- Test 2: Overview has correct fields ---
print("\n[TEST 2] Overview has correct fields")
_, ov = api("/api/overview")
for field in ["total_sessions", "total_trades", "total_pnl",
              "win_rate", "current_capital", "initial_capital"]:
    check(f"overview.{field} exists", field in ov)

# --- Test 3: Frontend features ---
print("\n[TEST 3] Frontend has all new features")
r = urllib.request.urlopen(BASE + "/")
html = r.read().decode()
check("startFastRefresh function", "startFastRefresh" in html)
check("No prompt() in startLive", "prompt(" not in html)
check("confirm() in startLive", "confirm(" in html)
check("Backtest modal present", "btModal" in html)
check("20 FnO symbols text", "20 F" in html)
check("Date pickers present", "btStartDate" in html and "btEndDate" in html)
check("AI Reasoning panel", "AI Reasoning" in html)
check("Fast refresh timer var", "fastRefreshTimer" in html)
check("5-second interval", "5000" in html)

# --- Test 4: Status endpoint ---
print("\n[TEST 4] Status endpoint structure")
_, st = api("/api/status")
check("backtest_running field", "backtest_running" in st)
check("live_running field", "live_running" in st)

# --- Test 5: save_results_to_db method ---
print("\n[TEST 5] save_results_to_db method on EOSBacktester")
from EOS.eos_backtester import EOSBacktester, BacktestResult
bt = EOSBacktester()
check("Method exists", hasattr(bt, "save_results_to_db"))
check("Method callable", callable(bt.save_results_to_db))

# --- Test 6: save_results_to_db actually writes to DB ---
print("\n[TEST 6] save_results_to_db writes to DB")
from EOS.eos_backtester import BacktestTrade
dummy_trade = BacktestTrade(
    symbol="TEST_SYM", option_type="PUT", strike_price=100.0,
    entry_date="2026-01-01", entry_time="09:30:00", entry_price=50.0,
    exit_date="2026-01-01", exit_time="14:00:00", exit_price=55.0,
    quantity=100, lot_size=100, pnl=500.0, pnl_pct=10.0,
    exit_reason="TRAILING_SL", hold_duration_minutes=270.0,
    price_change_at_entry=2.5, oi_change_at_entry=2.0
)
dummy_result = BacktestResult(
    start_date="2026-01-01", end_date="2026-01-01",
    symbols_tested=["TEST_SYM"], total_trades=1,
    winning_trades=1, losing_trades=0, win_rate=100.0,
    total_pnl=500.0, max_drawdown=0.0, max_drawdown_pct=0.0,
    sharpe_ratio=0.0, avg_win=500.0, avg_loss=0.0,
    avg_hold_time_minutes=270.0, profit_factor=999.0,
    trades=[dummy_trade], daily_pnl={"2026-01-01": 500.0}
)
bt_id = bt.save_results_to_db(dummy_result, initial_capital=100000)
check("Returned backtest_id", bt_id is not None and bt_id.startswith("BT_"))

# Verify it shows up in the API
_, sessions = api("/api/sessions")
found = any(s["backtest_id"] == bt_id for s in sessions)
check("New session visible in /api/sessions", found)

_, trades = api("/api/trades")
found_trade = any(t["symbol"] == "TEST_SYM" and t["backtest_id"] == bt_id for t in trades)
check("Trade visible in /api/trades", found_trade)

_, curve = api("/api/equity_curve")
found_snap = any(d["date"] == "2026-01-01" for d in curve)
check("Snapshot visible in /api/equity_curve", found_snap)

# --- Test 7: Backend subprocess command includes save ---
print("\n[TEST 7] Backend subprocess command includes save_results_to_db")
import inspect
from EOS.eos_dashboard import api_start_backtest
src = inspect.getsource(api_start_backtest)
check("save_results_to_db in backtest command", "save_results_to_db" in src)

# --- Summary ---
total = passed + failed
print("\n" + "=" * 55)
print(f"Results: {passed}/{total} passed" + (" ✓" if failed == 0 else f", {failed} FAILED"))
print("=" * 55)
sys.exit(0 if failed == 0 else 1)

