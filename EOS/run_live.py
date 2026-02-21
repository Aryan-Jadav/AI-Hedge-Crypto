# Last updated: 2026-02-21
"""
EOS Live Runner Launcher
Standalone script for clean subprocess launch from the dashboard.

Usage:
    python -m EOS.run_live --symbols RELIANCE HDFCBANK --capital 500000
    python -m EOS.run_live                           # all FNO symbols
"""

import sys
import time
import signal
import argparse
import json

from EOS.eos_live_runner import EOSLiveRunner


def main():
    parser = argparse.ArgumentParser(description="EOS Live Runner")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Symbols to monitor (default: all FNO stocks)")
    parser.add_argument("--capital", type=float, default=500000,
                        help="Initial capital (default: 500000)")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trade mode (default: True)")
    args = parser.parse_args()

    symbols = args.symbols
    if not symbols:
        from EOS.config import FNO_STOCKS
        symbols = list(FNO_STOCKS.keys())

    print(f"[run_live] Starting EOS Live Runner")
    print(f"[run_live] Symbols: {symbols}")
    print(f"[run_live] Capital: {args.capital}")
    print(f"[run_live] Paper Trade: {args.paper}")
    sys.stdout.flush()

    runner = EOSLiveRunner(
        symbols=symbols,
        initial_capital=args.capital,
        paper_trade=args.paper,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    def shutdown(sig, frame):
        print(f"\n[run_live] Received signal {sig}, stopping...")
        sys.stdout.flush()
        runner.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    runner.start()

    # Keep alive while runner is running
    try:
        while runner.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if runner.is_running:
            runner.stop()

    print("[run_live] Exited")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
