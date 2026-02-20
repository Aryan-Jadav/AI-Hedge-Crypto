"""Quick test for EOS Dashboard API endpoints."""
import json
import sys
sys.path.insert(0, ".")

from EOS.eos_dashboard import app

client = app.test_client()

def test_endpoint(name, url, checks=None):
    r = client.get(url)
    data = json.loads(r.data) if r.content_type == "application/json" else None
    status = "✅" if r.status_code == 200 else "❌"
    print(f"{status} {name}: {r.status_code}", end="")
    if data and isinstance(data, list):
        print(f" | {len(data)} items", end="")
    if data and isinstance(data, dict) and checks:
        for key in checks:
            print(f" | {key}={data.get(key)}", end="")
    print()
    return data

print("=" * 60)
print("EOS DASHBOARD - API ENDPOINT TESTS")
print("=" * 60)

# Test all endpoints
overview = test_endpoint("Overview", "/api/overview",
    ["total_sessions", "total_trades", "total_pnl", "win_rate", "current_capital"])

sessions = test_endpoint("Sessions", "/api/sessions")

trades = test_endpoint("Trades", "/api/trades")

snapshots = test_endpoint("Snapshots", "/api/snapshots")

equity = test_endpoint("Equity Curve", "/api/equity_curve")

positions = test_endpoint("Positions", "/api/positions")

# Session detail
if sessions and len(sessions) > 0:
    sid = sessions[0]["backtest_id"]
    detail = test_endpoint(f"Session Detail ({sid})", f"/api/session/{sid}")
    if detail:
        print(f"   Trades: {len(detail.get('trades', []))}, Snapshots: {len(detail.get('snapshots', []))}")

# Filtered endpoints
if sessions and len(sessions) > 0:
    sid = sessions[0]["backtest_id"]
    test_endpoint(f"Trades (filtered)", f"/api/trades?session_id={sid}")
    test_endpoint(f"Snapshots (filtered)", f"/api/snapshots?session_id={sid}")
    test_endpoint(f"Equity (filtered)", f"/api/equity_curve?session_id={sid}")

# Dashboard HTML
r = client.get("/")
status = "✅" if r.status_code == 200 else "❌"
html = r.data.decode()
has_chart = "equityChart" in html
has_dark = "#0a0a0f" in html
print(f"{status} Dashboard HTML: {r.status_code} | {len(html)} bytes | Chart.js: {has_chart} | Dark theme: {has_dark}")

# 404 test
r = client.get("/api/session/NONEXISTENT")
status = "✅" if r.status_code == 404 else "❌"
print(f"{status} 404 handling: {r.status_code}")

print()
print("=" * 60)
print("ALL DASHBOARD TESTS COMPLETE!")
print("=" * 60)

