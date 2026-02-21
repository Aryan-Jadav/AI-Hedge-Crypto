"""
Crypto Backtest Launcher
Standalone script for clean subprocess launch from the dashboard.

Downloads real Bybit public klines (no API key needed) and runs the
CRYPTO-EOS contrarian strategy on historical 5-min bars.

Usage:
    python -m CRYPTO.run_crypto_backtest
    python -m CRYPTO.run_crypto_backtest --symbols BTCUSDT ETHUSDT --days 60
    python -m CRYPTO.run_crypto_backtest --start 2026-01-01 --end 2026-02-01
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Force UTF-8
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
    parser = argparse.ArgumentParser(description="Crypto Backtest (Bybit public klines)")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Pairs to backtest (default: all configured pairs)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Initial capital in USDT")
    parser.add_argument("--start", dest="start_date", default=None,
                        help="Start date YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end", dest="end_date", default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=30,
                        help="Lookback days if --start not given (default: 30)")
    args = parser.parse_args()

    from CRYPTO.config import CRYPTO_PAIRS, CRYPTO_CONFIG

    symbols    = args.symbols or list(CRYPTO_PAIRS.keys())
    capital    = args.capital or CRYPTO_CONFIG["total_capital_usdt"]
    end_date   = args.end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = args.start_date or (
        datetime.now(timezone.utc) - timedelta(days=args.days)
    ).strftime("%Y-%m-%d")

    print("=" * 65)
    print("CRYPTO BACKTEST STARTING")
    print("=" * 65)
    print(f"Symbols    : {symbols}")
    print(f"Period     : {start_date} -> {end_date}")
    print(f"Capital    : ${capital:.2f} USDT")
    print(f"Data source: Bybit public klines (no API key needed)")
    print("=" * 65)
    sys.stdout.flush()

    from CRYPTO.sim_engine import CryptoSyntheticBacktester

    bt = CryptoSyntheticBacktester()
    result = bt.run_backtest(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
    )

    trades = result.get("trades", [])
    winning = [t for t in trades if t["pnl_usdt"] > 0]
    total_pnl = sum(t["pnl_usdt"] for t in trades)
    win_rate  = len(winning) / len(trades) * 100 if trades else 0.0

    print()
    print("=" * 65)
    print("CRYPTO BACKTEST SUMMARY")
    print("=" * 65)
    print(f"Period       : {start_date} -> {end_date}")
    print(f"Total Trades : {len(trades)}")
    print(f"Winning      : {len(winning)}")
    print(f"Losing       : {len(trades) - len(winning)}")
    print(f"Win Rate     : {win_rate:.1f}%")
    print(f"Total PnL    : ${total_pnl:+.2f} USDT")
    print("=" * 65)
    sys.stdout.flush()

    if trades:
        session_id = bt.save_results_to_db(result, initial_capital_usdt=capital)
        print(f"\n[DB] Results saved. Session ID: {session_id}")
    else:
        print("\n[INFO] No trades generated – nothing saved to DB.")
        print("[INFO] Try a longer date range or check Bybit kline availability.")

    print("[run_crypto_backtest] Completed successfully.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
