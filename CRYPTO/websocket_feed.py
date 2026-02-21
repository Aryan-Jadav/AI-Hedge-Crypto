"""
CRYPTO WebSocket Feed - Bybit v5 WebSocket
Mirrors EOS/eos_websocket_feed.py architecture exactly.

Key differences from Dhan WebSocket (EOS):
  - Protocol: JSON text frames (not binary struct)
  - Auth: JSON "auth" message sent post-connection
  - URL: wss://stream.bybit.com/v5/public/linear
  - Subscribe: {"op":"subscribe","args":["tickers.BTCUSDT"]}
  - Much simpler parsing (no struct.unpack needed)

Thread-safe cache with _data_lock (same pattern as EOS).
"""

import asyncio
import hmac
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

import websockets
import pytz

from .config import BYBIT_API_KEY, BYBIT_API_SECRET, CRYPTO_CONFIG, CRYPTO_PAIRS
from .data_fetcher import CryptoDataFetcher

IST = pytz.timezone("Asia/Kolkata")

PUBLIC_WS_URL  = "wss://stream.bybit.com/v5/public/linear"
PRIVATE_WS_URL = "wss://stream.bybit.com/v5/private"


# =============================================================================
# TICK DATA (mirrors EOS TickData)
# =============================================================================

@dataclass
class CryptoTickData:
    """
    Real-time tick data from Bybit WebSocket.
    Mirrors EOS TickData structure.
    """
    symbol: str
    last_price: float = 0.0
    prev_price_24h: float = 0.0
    price_24h_pct: float = 0.0      # decimal (e.g., -0.042 = -4.2%)
    volume_24h: float = 0.0
    turnover_24h: float = 0.0
    funding_rate: float = 0.0
    next_funding_time: int = 0      # Unix ms timestamp
    bid_price: float = 0.0
    ask_price: float = 0.0
    open_interest: float = 0.0
    last_update_time: datetime = field(default_factory=lambda: datetime.now(IST))

    def price_change_pct(self) -> float:
        """% change from 24h reference price."""
        return self.price_24h_pct * 100.0

    def is_funding_extreme(self, threshold: float = None) -> bool:
        """True if abs(funding_rate) > threshold."""
        if threshold is None:
            threshold = CRYPTO_CONFIG["funding_rate_threshold"]
        return abs(self.funding_rate) > threshold


# =============================================================================
# WEBSOCKET FEED
# =============================================================================

class CryptoWebSocketFeed:
    """
    Bybit v5 WebSocket Feed Manager.
    Mirrors DhanWebSocketFeed architecture exactly.

    Public feed: tickers + klines for all configured pairs.
    Optional private feed: position/order updates for live trading.

    Threading model (same as EOS):
    - WebSocket runs in a daemon thread with its own asyncio event loop
    - Main thread can safely call get_tick(), get_kline_closes(), etc.
    - Thread safety via _data_lock (same as EOS)
    """

    def __init__(
        self,
        on_tick: Callable[[CryptoTickData], None] = None,
        on_connect: Callable[[], None] = None,
        on_disconnect: Callable[[str], None] = None,
        on_error: Callable[[str], None] = None,
    ) -> None:
        self.api_key    = BYBIT_API_KEY
        self.api_secret = BYBIT_API_SECRET

        # Callbacks (mirrors EOS pattern)
        self.on_tick       = on_tick
        self.on_connect    = on_connect
        self.on_disconnect = on_disconnect
        self.on_error      = on_error

        # State
        self.is_connected: bool = False
        self.is_running:   bool = False
        self._reconnect_attempts: int = 0
        self._max_reconnect_attempts: int = 5
        self._reconnect_delay: int = 5   # seconds

        # Thread-safe tick cache (mirrors EOS _tick_data + _data_lock)
        self._data_lock: threading.Lock = threading.Lock()
        self._tick_data:   Dict[str, CryptoTickData] = {}
        self._kline_data:  Dict[str, List[float]] = {}   # symbol -> close prices

        self._subscribed_symbols: List[str] = []
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # Reference volume for spike detection (pre-populated)
        self._avg_volumes: Dict[str, float] = {}

    # -------------------------------------------------------------------------
    # Auth helpers (for private WebSocket)
    # -------------------------------------------------------------------------

    def _build_auth_message(self) -> Dict:
        """
        Build Bybit private WebSocket auth message.
        expires = now + 10s; sig = HMAC_SHA256(secret, "GET/realtime" + expires)
        """
        expires = int(time.time() * 1000) + 10000
        sig_str = f"GET/realtime{expires}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            sig_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {"op": "auth", "args": [self.api_key, expires, signature]}

    def _build_subscribe_message(self, topics: List[str]) -> Dict:
        """{"op": "subscribe", "args": topics}"""
        return {"op": "subscribe", "args": topics}

    # -------------------------------------------------------------------------
    # Message parsing (much simpler than EOS binary protocol)
    # -------------------------------------------------------------------------

    def _process_message(self, raw: str) -> Optional[CryptoTickData]:
        """
        Parse JSON message from Bybit WebSocket.
        Returns CryptoTickData if a ticker update, else None.
        Mirrors EOS _process_message() pattern.
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return None

        topic = msg.get("topic", "")

        # Ticker update: "tickers.BTCUSDT"
        if topic.startswith("tickers."):
            return self._parse_ticker_update(msg)

        # Kline update: "kline.5.BTCUSDT"
        if topic.startswith("kline."):
            self._parse_kline_update(msg)
            return None

        # Connection confirmation / subscription ack
        if msg.get("op") in ("subscribe", "auth", "ping", "pong"):
            return None

        return None

    def _parse_ticker_update(self, msg: Dict) -> Optional[CryptoTickData]:
        """
        Parse tickers.SYMBOL topic.
        Bybit sends delta updates; merge with existing tick data.
        """
        topic = msg.get("topic", "")
        symbol = topic.split(".")[-1] if "." in topic else ""
        if not symbol:
            return None

        data = msg.get("data", {})
        msg_type = msg.get("type", "delta")  # "snapshot" or "delta"

        with self._data_lock:
            # Get or create tick entry
            tick = self._tick_data.get(symbol, CryptoTickData(symbol=symbol))

            # Update only fields present in the delta
            if "lastPrice" in data:
                tick.last_price = float(data["lastPrice"])
            if "prevPrice24h" in data:
                tick.prev_price_24h = float(data["prevPrice24h"])
            if "price24hPcnt" in data:
                tick.price_24h_pct = float(data["price24hPcnt"])
            if "volume24h" in data:
                tick.volume_24h = float(data["volume24h"])
            if "turnover24h" in data:
                tick.turnover_24h = float(data["turnover24h"])
            if "fundingRate" in data:
                tick.funding_rate = float(data["fundingRate"])
            if "nextFundingTime" in data:
                tick.next_funding_time = int(data["nextFundingTime"])
            if "bid1Price" in data:
                tick.bid_price = float(data["bid1Price"])
            if "ask1Price" in data:
                tick.ask_price = float(data["ask1Price"])
            if "openInterest" in data:
                tick.open_interest = float(data["openInterest"])

            tick.last_update_time = datetime.now(IST)
            self._tick_data[symbol] = tick

        return tick

    def _parse_kline_update(self, msg: Dict) -> None:
        """
        Parse kline.5.SYMBOL topic.
        Append confirmed close price to _kline_data[symbol].
        """
        topic = msg.get("topic", "")
        parts = topic.split(".")
        if len(parts) < 3:
            return
        symbol = parts[-1]

        data_list = msg.get("data", [])
        for candle in data_list:
            if candle.get("confirm") is True:
                try:
                    close = float(candle["close"])
                    with self._data_lock:
                        if symbol not in self._kline_data:
                            self._kline_data[symbol] = []
                        self._kline_data[symbol].append(close)
                        # Keep last 50 closes for SMA calculation
                        if len(self._kline_data[symbol]) > 50:
                            self._kline_data[symbol] = self._kline_data[symbol][-50:]
                except (KeyError, ValueError):
                    pass

    # -------------------------------------------------------------------------
    # Async WebSocket handler
    # -------------------------------------------------------------------------

    async def _connect_and_subscribe(self, symbols: List[str]) -> None:
        """
        Connect to Bybit public WebSocket and subscribe to tickers + klines.
        Mirrors EOS _connect_and_run() pattern.
        """
        interval = CRYPTO_CONFIG["candle_interval"]

        # Build subscription topics
        ticker_topics = [f"tickers.{sym}" for sym in symbols]
        kline_topics  = [f"kline.{interval}.{sym}" for sym in symbols]
        all_topics = ticker_topics + kline_topics

        while self.is_running:
            try:
                print(f"[CryptoWS] Connecting to {PUBLIC_WS_URL}")
                async with websockets.connect(
                    PUBLIC_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.is_connected = True
                    self._reconnect_attempts = 0
                    print(f"[CryptoWS] Connected. Subscribing to {len(all_topics)} topics.")

                    if self.on_connect:
                        self.on_connect()

                    # Subscribe in batches of 10
                    batch_size = 10
                    for i in range(0, len(all_topics), batch_size):
                        batch = all_topics[i:i+batch_size]
                        sub_msg = self._build_subscribe_message(batch)
                        await ws.send(json.dumps(sub_msg))
                        await asyncio.sleep(0.1)

                    # Main receive loop
                    async for raw_msg in ws:
                        if not self.is_running:
                            break
                        tick = self._process_message(raw_msg)
                        if tick and self.on_tick:
                            self.on_tick(tick)

            except websockets.exceptions.ConnectionClosed as e:
                self.is_connected = False
                reason = f"Connection closed: {e.code} {e.reason}"
                print(f"[CryptoWS] {reason}")
                if self.on_disconnect:
                    self.on_disconnect(reason)

            except Exception as e:
                self.is_connected = False
                err = f"WebSocket error: {e}"
                print(f"[CryptoWS] {err}")
                if self.on_error:
                    self.on_error(err)

            if not self.is_running:
                break

            # Reconnect logic (mirrors EOS reconnect pattern)
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                print(f"[CryptoWS] Max reconnect attempts reached. Stopping.")
                self.is_running = False
                break

            print(f"[CryptoWS] Reconnecting in {self._reconnect_delay}s "
                  f"(attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})...")
            await asyncio.sleep(self._reconnect_delay)

    def _run_event_loop(self, symbols: List[str]) -> None:
        """
        Run the asyncio event loop in a daemon thread.
        Mirrors EOS _run_websocket_in_thread().
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_subscribe(symbols))
        finally:
            self._loop.close()
            self.is_connected = False
            print("[CryptoWS] Event loop closed.")

    # -------------------------------------------------------------------------
    # Public API (mirrors DhanWebSocketFeed public interface exactly)
    # -------------------------------------------------------------------------

    def subscribe_pairs(self, symbols: List[str] = None) -> None:
        """
        Subscribe to ticker + kline feeds for specified pairs.
        Mirrors DhanWebSocketFeed.subscribe_instruments().
        """
        self._subscribed_symbols = symbols or list(CRYPTO_PAIRS.keys())
        print(f"[CryptoWS] Will subscribe to: {self._subscribed_symbols}")

    def start(self) -> None:
        """
        Start the WebSocket feed in a daemon thread.
        Mirrors DhanWebSocketFeed.start().
        """
        if self.is_running:
            print("[CryptoWS] Already running.")
            return

        if not self._subscribed_symbols:
            self.subscribe_pairs()

        self.is_running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(self._subscribed_symbols,),
            daemon=True,
            name="CryptoWebSocketThread",
        )
        self._thread.start()
        print(f"[CryptoWS] Feed started in daemon thread.")

    def stop(self) -> None:
        """
        Stop the WebSocket feed gracefully.
        Mirrors DhanWebSocketFeed.stop().
        """
        self.is_running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.is_connected = False
        print("[CryptoWS] Feed stopped.")

    # -------------------------------------------------------------------------
    # Data access (thread-safe)
    # -------------------------------------------------------------------------

    def get_tick(self, symbol: str) -> Optional[CryptoTickData]:
        """Get last tick for a symbol. Thread-safe."""
        with self._data_lock:
            return self._tick_data.get(symbol)

    def get_all_ticks(self) -> Dict[str, CryptoTickData]:
        """Get all ticks. Thread-safe."""
        with self._data_lock:
            return dict(self._tick_data)

    def get_kline_closes(self, symbol: str) -> List[float]:
        """
        Get recent close prices for SMA calculation.
        Mirrors DhanWebSocketFeed.get_candle_history().
        """
        with self._data_lock:
            return list(self._kline_data.get(symbol, []))

    def get_pairs_with_entry_signals(
        self,
        price_threshold: float = None,
        funding_threshold: float = None,
        volume_multiplier: float = None,
    ) -> List[Dict]:
        """
        Scan all subscribed pairs for CRYPTO-EOS entry conditions.
        Mirrors DhanWebSocketFeed.get_stocks_with_entry_signals().

        Returns list of signal dicts for pairs meeting entry conditions.
        """
        if price_threshold is None:
            price_threshold = CRYPTO_CONFIG["price_change_threshold"]
        if funding_threshold is None:
            funding_threshold = CRYPTO_CONFIG["funding_rate_threshold"]
        if volume_multiplier is None:
            volume_multiplier = CRYPTO_CONFIG["volume_spike_multiplier"]

        signals = []
        with self._data_lock:
            ticks = dict(self._tick_data)

        for symbol, tick in ticks.items():
            if tick.last_price <= 0:
                continue

            price_change_pct = tick.price_change_pct()
            funding_extreme  = tick.is_funding_extreme(funding_threshold)
            avg_vol = self._avg_volumes.get(symbol, 0)
            volume_spike = (
                CryptoDataFetcher.calculate_volume_spike(tick.volume_24h, avg_vol, volume_multiplier)
                if avg_vol > 0 else False
            )

            # CRYPTO-EOS entry conditions
            if abs(price_change_pct) >= price_threshold and (funding_extreme or volume_spike):
                direction = "LONG" if price_change_pct < -price_threshold else "SHORT"
                signals.append({
                    "symbol":            symbol,
                    "direction":         direction,
                    "last_price":        tick.last_price,
                    "price_change_pct":  price_change_pct,
                    "funding_rate":      tick.funding_rate,
                    "funding_extreme":   funding_extreme,
                    "volume_24h":        tick.volume_24h,
                    "avg_volume":        avg_vol,
                    "volume_spike":      volume_spike,
                    "open_interest":     tick.open_interest,
                })

        return signals

    def prefetch_market_data(self) -> None:
        """
        Pre-populate tick cache from REST API before WebSocket connects.
        Mirrors DhanWebSocketFeed.prefetch_prev_close_data().
        """
        print("[CryptoWS] Pre-fetching market data from REST API...")
        fetcher = CryptoDataFetcher()
        for symbol in self._subscribed_symbols or list(CRYPTO_PAIRS.keys()):
            try:
                resp = fetcher.get_ticker(symbol)
                if resp.get("error") or not resp.get("data"):
                    continue

                ticker_list = resp["data"].get("list", [])
                if not ticker_list:
                    continue

                t = ticker_list[0]
                tick = CryptoTickData(
                    symbol=symbol,
                    last_price=float(t.get("lastPrice", 0)),
                    prev_price_24h=float(t.get("prevPrice24h", 0)),
                    price_24h_pct=float(t.get("price24hPcnt", 0)),
                    volume_24h=float(t.get("volume24h", 0)),
                    funding_rate=float(t.get("fundingRate", 0)),
                    open_interest=float(t.get("openInterest", 0)),
                )

                # Fetch klines for SMA pre-population
                kline_resp = fetcher.get_kline(symbol, interval=CRYPTO_CONFIG["candle_interval"], limit=30)
                if not kline_resp.get("error") and kline_resp.get("data"):
                    klines = list(reversed(kline_resp["data"].get("list", [])))
                    closes = [float(c[4]) for c in klines if len(c) >= 5]
                    volumes = [float(c[5]) for c in klines if len(c) >= 6]
                    avg_vol = CryptoDataFetcher.calculate_avg_volume(volumes)
                    self._avg_volumes[symbol] = avg_vol

                    with self._data_lock:
                        self._kline_data[symbol] = closes

                with self._data_lock:
                    self._tick_data[symbol] = tick

                print(f"[CryptoWS] Pre-fetched {symbol}: ${tick.last_price:.2f} | "
                      f"funding={tick.funding_rate*100:.4f}% | vol={tick.volume_24h:.0f}")
                time.sleep(0.15)  # rate limit

            except Exception as e:
                print(f"[CryptoWS] Pre-fetch error for {symbol}: {e}")

        print("[CryptoWS] Pre-fetch complete.")

    def print_status(self) -> None:
        """Print feed status. Mirrors DhanWebSocketFeed.print_status()."""
        print(f"\n{'='*50}")
        print(f"  CRYPTO WS FEED STATUS")
        print(f"{'='*50}")
        print(f"  Connected:   {self.is_connected}")
        print(f"  Running:     {self.is_running}")
        print(f"  Subscribed:  {len(self._subscribed_symbols)} pairs")
        print(f"  Tick cache:  {len(self._tick_data)} symbols")
        print(f"  Reconnects:  {self._reconnect_attempts}")
        with self._data_lock:
            for sym, tick in self._tick_data.items():
                pct = tick.price_change_pct()
                color = "\033[92m" if pct >= 0 else "\033[91m"
                print(f"    {sym:12s}: ${tick.last_price:>10.4f} | "
                      f"{color}{pct:>+7.2f}%\033[0m | "
                      f"funding: {tick.funding_rate*100:>+.4f}%")
        print(f"{'='*50}\n")
