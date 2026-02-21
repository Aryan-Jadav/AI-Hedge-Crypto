"""
EOS Live Runner Launcher
Standalone script for clean subprocess launch from the dashboard.

Behaviour
---------
  Live mode  (paper_trade=False) + Dhan creds present  → real EOSLiveRunner (Dhan WebSocket)
  Paper mode (paper_trade=True)  + Dhan creds present  → real EOSLiveRunner --paper
  Paper mode (paper_trade=True)  + NO Dhan creds       → EOSPaperLiveRunner (Yahoo Finance polling)
  Live mode  + NO Dhan creds                           → error (can't trade live without creds)

Usage:
    python -m EOS.run_live --symbols RELIANCE HDFCBANK --capital 500000
    python -m EOS.run_live --paper          # paper trade (sim if no Dhan creds)
    python -m EOS.run_live                  # all FNO symbols, paper mode
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
    parser = argparse.ArgumentParser(description="EOS Live Runner")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Symbols to monitor (default: all FNO stocks)")
    parser.add_argument("--capital", type=float, default=500000,
                        help="Initial capital in INR (default: 500000)")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trade mode (default: True)")
    args = parser.parse_args()

    from EOS.config import FNO_STOCKS, DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN

    symbols = args.symbols or list(FNO_STOCKS.keys())
    capital = args.capital
    paper   = args.paper

    has_dhan = bool(DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN)

    # Decide which engine to use
    if not paper and not has_dhan:
        print("=" * 65)
        print("ERROR: Live trading requires Dhan API credentials.")
        print("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN, then restart.")
        print("=" * 65)
        sys.exit(1)

    use_sim = paper and not has_dhan
    mode_label = (
        "LIVE (Dhan API)"        if not paper and has_dhan else
        "PAPER (Dhan WebSocket)" if paper and has_dhan    else
        "PAPER SIM (Yahoo Finance REST polling)"
    )

    print("=" * 65)
    print("EOS LIVE RUNNER STARTING")
    print("=" * 65)
    print(f"Symbols : {len(symbols)} stocks")
    print(f"Capital : Rs.{capital:,.0f}")
    print(f"Mode    : {mode_label}")
    print("=" * 65)
    sys.stdout.flush()

    if use_sim:
        # ── Paper sim: Yahoo Finance polling, no Dhan auth ────────────────
        print("[INFO] Dhan credentials not set – running Paper SIM mode.")
        print("[INFO] Polls Yahoo Finance for real NSE prices every 30s.")
        print("[INFO] Signals fire when any stock moves >2% from prev close.")
        sys.stdout.flush()

        from EOS.sim_engine import EOSPaperLiveRunner
        runner = EOSPaperLiveRunner(symbols=symbols, initial_capital=capital)

        def shutdown(sig, frame):
            print(f"\n[run_live] Signal {sig} – stopping...")
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
        # ── Real live runner via Dhan WebSocket ───────────────────────────
        from EOS.eos_live_runner import EOSLiveRunner
        runner = EOSLiveRunner(
            symbols=symbols,
            initial_capital=capital,
            paper_trade=paper,
        )

        def shutdown(sig, frame):
            print(f"\n[run_live] Signal {sig} – stopping...")
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

    print("[run_live] Exited.")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
