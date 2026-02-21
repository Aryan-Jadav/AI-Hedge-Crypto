"""
Crypto Live Runner Launcher
Standalone script for clean subprocess launch from the dashboard.

Behaviour
---------
  Paper mode (default) → CryptoPaperLiveRunner
      Polls Bybit PUBLIC tickers every 30 s (no API key needed).
      Enters virtual positions when any pair moves >4% from prev close.
      Saves all trades + snapshots to portfolio.db.

  Live mode  (--no-paper + BYBIT_API_KEY set) → CryptoLiveRunner
      Uses real Bybit WebSocket + order API.

Usage:
    python -m CRYPTO.run_crypto_live                     # paper sim, all pairs
    python -m CRYPTO.run_crypto_live --symbols BTCUSDT ETHUSDT --capital 5000
    python -m CRYPTO.run_crypto_live --no-paper          # real live (needs API key)
"""

import sys
import time
import signal
import argparse
from pathlib import Path

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
    parser = argparse.ArgumentParser(description="Crypto Live Runner")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Pairs to monitor (default: all configured pairs)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Initial capital in USDT")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trade mode (default: True)")
    parser.add_argument("--no-paper", dest="paper", action="store_false",
                        help="Live trade mode (requires BYBIT_API_KEY + BYBIT_API_SECRET)")
    args = parser.parse_args()

    from CRYPTO.config import CRYPTO_PAIRS, CRYPTO_CONFIG, BYBIT_API_KEY, BYBIT_API_SECRET

    symbols = args.symbols or list(CRYPTO_PAIRS.keys())
    capital = args.capital or CRYPTO_CONFIG["total_capital_usdt"]
    paper   = args.paper

    has_bybit = bool(BYBIT_API_KEY and BYBIT_API_SECRET)

    if not paper and not has_bybit:
        print("=" * 65)
        print("ERROR: Live trading requires BYBIT_API_KEY + BYBIT_API_SECRET.")
        print("Set them as environment variables, then restart.")
        print("=" * 65)
        sys.exit(1)

    # Always use paper sim unless explicitly live with real credentials
    use_sim = paper  # sim = paper mode (uses public REST, no auth)

    mode_label = (
        "LIVE (Bybit WebSocket + Orders)" if not paper and has_bybit else
        "PAPER SIM (Bybit public REST polling)"
    )

    print("=" * 65)
    print("CRYPTO LIVE RUNNER STARTING")
    print("=" * 65)
    print(f"Symbols  : {symbols}")
    print(f"Capital  : ${capital:.2f} USDT")
    print(f"Mode     : {mode_label}")
    print("=" * 65)
    sys.stdout.flush()

    if use_sim:
        # ── Paper sim: Bybit public REST polling, no auth ─────────────────
        print("[INFO] Using Bybit PUBLIC API – no credentials required.")
        print(f"[INFO] Scanning {len(symbols)} pairs every 30 s.")
        print("[INFO] Entry when abs(price_change_24h) > 4%  (contrarian).")
        sys.stdout.flush()

        from CRYPTO.sim_engine import CryptoPaperLiveRunner
        runner = CryptoPaperLiveRunner(
            symbols=symbols,
            initial_capital_usdt=capital,
            paper_trade=True,
        )

        def shutdown(sig, frame):
            print(f"\n[run_crypto_live] Signal {sig} – stopping...")
            sys.stdout.flush()
            runner.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, shutdown)

        runner.start()

        try:
            while runner.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if runner.is_running:
                runner.stop()

    else:
        # ── Real live runner via Bybit WebSocket ──────────────────────────
        from CRYPTO.live_runner import CryptoLiveRunner
        runner = CryptoLiveRunner(
            symbols=symbols,
            initial_capital_usdt=capital,
            paper_trade=False,
        )

        def shutdown(sig, frame):
            print(f"\n[run_crypto_live] Signal {sig} – stopping...")
            sys.stdout.flush()
            runner.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, shutdown)

        runner.start()

        try:
            while runner.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if runner.is_running:
                runner.stop()

    print("[run_crypto_live] Exited.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
