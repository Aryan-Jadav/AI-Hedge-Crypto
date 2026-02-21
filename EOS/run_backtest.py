"""
EOS Backtest Runner - Standalone launcher for clean subprocess execution from dashboard.

Behaviour
---------
  - If DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN are set  → real EOSBacktester (live Dhan API data)
  - Otherwise                                       → EOSSyntheticBacktester
      Uses Yahoo Finance daily OHLC + Black-Scholes option pricing.
      Requires no credentials. Produces real trades on real price moves.

Usage:
    python -m EOS.run_backtest --start 2026-01-01 --end 2026-01-31 --capital 500000
    python -m EOS.run_backtest   # defaults: last 30 days, all symbols, Rs.5L capital
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 so Rs / rupee symbol never crashes on Windows cp1252
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Ensure project root on sys.path
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main():
    parser = argparse.ArgumentParser(description="EOS Backtest Runner")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Symbols to backtest (default: all FNO stocks)")
    parser.add_argument("--start", default=None,
                        help="Start date YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--capital", type=float, default=500000,
                        help="Initial capital in INR (default: 500000)")
    parser.add_argument("--expiry-code", type=int, default=1,
                        help="Expiry code (Dhan mode only): 1=current month, 2=last month")
    args = parser.parse_args()

    from EOS.config import FNO_STOCKS, DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN

    symbols    = args.symbols or list(FNO_STOCKS.keys())
    start_date = args.start or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date   = args.end   or datetime.now().strftime("%Y-%m-%d")
    capital    = args.capital

    use_dhan = bool(DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN)
    mode_label = "REAL (Dhan API)" if use_dhan else "SIM (Yahoo Finance + Black-Scholes)"

    print("=" * 65)
    print("EOS BACKTEST STARTING")
    print("=" * 65)
    print(f"Symbols : {len(symbols)} stocks")
    print(f"Period  : {start_date} to {end_date}")
    print(f"Capital : Rs.{capital:,.0f}")
    print(f"Mode    : {mode_label}")
    print("=" * 65)
    sys.stdout.flush()

    if use_dhan:
        # ── Real backtest via Dhan API ────────────────────────────────────
        from EOS.eos_backtester import EOSBacktester
        bt = EOSBacktester()
        result = bt.run_backtest(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            expiry_code=args.expiry_code,
        )
        bt.print_summary(result)
        sys.stdout.flush()
        backtest_id = bt.save_results_to_db(result, initial_capital=capital)
    else:
        # ── Synthetic backtest (no credentials needed) ────────────────────
        print("[INFO] Dhan credentials not set – running in SIM mode.")
        print("[INFO] Real stock price data from Yahoo Finance (NSE).")
        print("[INFO] Option prices synthesised via Black-Scholes.")
        sys.stdout.flush()

        from EOS.sim_engine import EOSSyntheticBacktester
        bt = EOSSyntheticBacktester()
        result = bt.run_backtest(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )
        trades = result["trades"]
        total_pnl = sum(t["pnl"] for t in trades)
        wins      = [t for t in trades if t["pnl"] > 0]
        win_rate  = len(wins) / len(trades) * 100 if trades else 0

        print("\n" + "=" * 65)
        print("BACKTEST SUMMARY (SIM)")
        print("=" * 65)
        print(f"Period        : {start_date} to {end_date}")
        print(f"Total Trades  : {len(trades)}")
        print(f"Winning       : {len(wins)}")
        print(f"Losing        : {len(trades) - len(wins)}")
        print(f"Win Rate      : {win_rate:.1f}%")
        print(f"Total PnL     : Rs.{total_pnl:,.0f}")
        print("=" * 65)
        sys.stdout.flush()

        backtest_id = bt.save_results_to_db(result, initial_capital=capital)

    print(f"\n[DB] Results saved. Session ID: {backtest_id}")
    print("[run_backtest] Completed successfully.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
