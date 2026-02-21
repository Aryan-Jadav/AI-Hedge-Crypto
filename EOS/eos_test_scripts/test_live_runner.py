# Last updated: 2026-02-21
"""
Test script for EOS Live Runner
Tests the live trading system with a small batch of stocks.

Usage:
    python -m EOS.eos_test_scripts.test_live_runner
    python -m EOS.eos_test_scripts.test_live_runner --quick
"""

import time
import signal
import sys
from datetime import datetime

from ..eos_live_runner import EOSLiveRunner, LivePosition, LiveTrade


# Test with a small batch of 5 stocks
TEST_SYMBOLS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY"]


def on_signal_callback(signal_data: dict, position: LivePosition):
    """Callback when a signal is detected and position opened."""
    print(f"\n🔔 SIGNAL CALLBACK:")
    print(f"   Symbol: {signal_data['symbol']}")
    print(f"   Direction: {signal_data['direction']}")
    print(f"   Position Entry: ₹{position.entry_price:.2f}")


def on_trade_callback(trade: LiveTrade):
    """Callback when a trade is completed."""
    print(f"\n📊 TRADE CALLBACK:")
    print(f"   Symbol: {trade.symbol}")
    print(f"   P&L: ₹{trade.pnl:.2f} ({trade.pnl_pct:.2f}%)")
    print(f"   Exit Reason: {trade.exit_reason}")


def main():
    print("=" * 60)
    print("EOS LIVE RUNNER TEST")
    print("=" * 60)
    print(f"Start Time: {datetime.now()}")
    print(f"Testing with {len(TEST_SYMBOLS)} symbols: {TEST_SYMBOLS}")
    print("=" * 60)
    
    # Create live runner in paper trade mode
    runner = EOSLiveRunner(
        symbols=TEST_SYMBOLS,
        initial_capital=500000,
        paper_trade=True,
        on_signal=on_signal_callback,
        on_trade=on_trade_callback
    )
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n\nReceived interrupt signal. Stopping...")
        runner.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start the runner
    print("\nStarting EOS Live Runner...")
    runner.start()
    
    # Wait for WebSocket to connect
    time.sleep(3)
    
    # Monitor for a period (adjust as needed)
    test_duration_seconds = 60  # 1 minute test
    print(f"\nMonitoring for {test_duration_seconds} seconds...")
    print("Press Ctrl+C to stop early\n")
    
    status_interval = 10  # Print status every 10 seconds
    elapsed = 0
    
    try:
        while elapsed < test_duration_seconds:
            time.sleep(status_interval)
            elapsed += status_interval
            
            print(f"\n--- Status at {elapsed}s ---")
            runner.print_status()
            
            # Check for signals (for testing)
            if runner.feed:
                signals = runner.feed.get_stocks_with_entry_signals()
                if signals:
                    print(f"\n📍 Potential Signals Detected: {len(signals)}")
                    for sig in signals[:3]:  # Show top 3
                        print(f"   {sig['symbol']}: {sig['direction']} "
                              f"(Price: {sig['price_change_pct']:+.2f}%, OI: {sig['oi_change_pct']:+.2f}%)")
                        
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    
    # Stop and show summary
    print("\nStopping runner...")
    runner.stop()
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


def quick_test():
    """Quick test without market hours - just check WebSocket connection."""
    print("=" * 60)
    print("QUICK WEBSOCKET TEST (No market hours check)")
    print("=" * 60)
    
    runner = EOSLiveRunner(
        symbols=TEST_SYMBOLS[:3],  # Just 3 stocks
        paper_trade=True
    )
    
    # Just test feed initialization
    print("\nInitializing WebSocket feed...")
    runner._init_feed()
    
    # Wait for connection
    time.sleep(5)
    
    # Check data
    if runner.feed:
        print("\n--- Feed Status ---")
        runner.feed.print_status()
        
        print("\n--- Sample Tick Data ---")
        for symbol in TEST_SYMBOLS[:3]:
            tick = runner.feed.get_tick(symbol)
            if tick:
                print(f"{symbol}: LTP=₹{tick.ltp:.2f}, PrevClose=₹{tick.prev_close:.2f}, "
                      f"Change={tick.price_change_pct():+.2f}%")
            else:
                print(f"{symbol}: No data")
    
    # Cleanup
    if runner.feed:
        runner.feed.stop()
    
    print("\nQuick test complete!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test EOS Live Runner")
    parser.add_argument("--quick", action="store_true", help="Quick WebSocket test only")
    args = parser.parse_args()
    
    if args.quick:
        quick_test()
    else:
        main()

