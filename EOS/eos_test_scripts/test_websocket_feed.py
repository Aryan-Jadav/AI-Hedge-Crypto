"""
Test script for EOS WebSocket Data Feed.
Verifies connection, subscription, and data reception.

Run this script to test if the live data feed is working correctly.

Usage:
    python -m EOS.eos_test_scripts.test_websocket_feed
"""

import time
from datetime import datetime

from ..eos_websocket_feed import DhanWebSocketFeed, TickData, WEBSOCKETS_AVAILABLE
from ..config import FNO_STOCKS


def on_tick(tick: TickData):
    """Callback for each tick update."""
    if tick.symbol:  # Only print named ticks
        pct_change = tick.price_change_pct()
        oi_change = tick.oi_change_pct()

        # Highlight stocks meeting EOS conditions
        if abs(pct_change) >= 2.0 and abs(oi_change) >= 1.75:
            direction = "🔴 PUT" if pct_change > 0 else "🟢 CALL"
            print(f"⚡ SIGNAL {direction}: {tick.symbol} | LTP=₹{tick.ltp:.2f} | "
                  f"Change={pct_change:+.2f}% | OI Change={oi_change:+.2f}%")


def on_connect():
    """Callback when WebSocket connects."""
    print("\n✅ Connected to Dhan WebSocket!")


def on_disconnect(reason: str):
    """Callback when WebSocket disconnects."""
    print(f"\n❌ Disconnected: {reason}")


def on_error(error: str):
    """Callback for errors."""
    print(f"\n⚠️ Error: {error}")


def main():
    print("=" * 70)
    print("EOS WEBSOCKET DATA FEED TEST")
    print("=" * 70)
    print(f"Start Time: {datetime.now()}")
    print(f"Stocks to Subscribe: {len(FNO_STOCKS)}")
    print(f"WebSocket Library Available: {WEBSOCKETS_AVAILABLE}")
    print("=" * 70)

    if not WEBSOCKETS_AVAILABLE:
        print("\n❌ ERROR: 'websockets' package not installed!")
        print("   Run: pip install websockets")
        return

    # Create WebSocket feed with callbacks
    feed = DhanWebSocketFeed(
        on_tick=on_tick,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        on_error=on_error
    )

    # Enable debug mode to see what packet types we receive
    feed.enable_debug(True)

    # Subscribe to all FNO stocks
    print("\n📡 Subscribing to FNO stocks...")
    feed.subscribe_fno_stocks()

    # Pre-fetch previous close data via REST API (more reliable than WebSocket)
    print("\n📊 Pre-fetching previous close data...")
    feed.prefetch_prev_close_data()

    # Start the WebSocket connection
    print("\n🚀 Starting WebSocket connection...")
    feed.start()

    # Wait for connection
    print("\n⏳ Waiting for connection (5 seconds)...")
    time.sleep(5)

    # Check connection status
    if not feed.is_connected:
        print("\n❌ Failed to connect to WebSocket!")
        print("   Check your access token and client ID in config.py")
        feed.stop()
        return

    print("\n✅ Connection established! Receiving data...")
    print("\n" + "-" * 70)
    print("REAL-TIME DATA (watching for 30 seconds)...")
    print("(Signals will appear if any stock meets EOS entry conditions)")
    print("-" * 70 + "\n")

    # Monitor for 30 seconds
    try:
        for i in range(6):  # 6 x 5 seconds = 30 seconds
            time.sleep(5)

            # Print status every 5 seconds
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Status Check #{i+1}:")

            # Get all current data
            all_ticks = feed.get_all_ticks()
            valid_ticks = [t for t in all_ticks.values() if t.ltp > 0]

            print(f"   Receiving data for {len(valid_ticks)} instruments")

            # Show sample ticks
            if valid_ticks:
                print("\n   Sample Data:")
                for tick in list(valid_ticks)[:3]:
                    pct = tick.price_change_pct()
                    print(f"   • {tick.symbol or tick.security_id}: LTP=₹{tick.ltp:.2f}, "
                          f"PrevClose=₹{tick.prev_close:.2f}, Change={pct:+.2f}%")

            # Check for EOS signals
            signals = feed.get_stocks_with_entry_signals()
            if signals:
                print(f"\n   🔔 {len(signals)} stock(s) meeting EOS entry conditions!")
                for sig in signals[:3]:
                    print(f"   ⚡ {sig['symbol']}: {sig['direction']} | "
                          f"Price={sig['price_change_pct']:+.2f}% | OI={sig['oi_change_pct']:+.2f}%")

    except KeyboardInterrupt:
        print("\n\n⛔ Interrupted by user")

    # Final status
    print("\n" + "=" * 70)
    print("FINAL STATUS")
    print("=" * 70)
    feed.print_status()

    # Stop the feed
    print("\n🛑 Stopping WebSocket feed...")
    feed.stop()

    print("\n✅ Test completed!")
    print("=" * 70)


if __name__ == "__main__":
    main()

