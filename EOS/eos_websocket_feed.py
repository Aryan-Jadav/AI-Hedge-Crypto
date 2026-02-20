"""
EOS WebSocket Data Feed Manager
Real-time market data via Dhan WebSocket API for the EOS strategy.

Provides:
- Real-time LTP (Last Traded Price)
- Real-time OI (Open Interest)
- OHLC data for candle building
- Previous day close data
- Volume and trade data
"""

import json
import struct
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import IntEnum
import asyncio

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("WARNING: 'websockets' package not installed. Run: pip install websockets")

from .config import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, FNO_STOCKS

# ===== CONSTANTS =====

WEBSOCKET_URL = "wss://api-feed.dhan.co"

class RequestCode(IntEnum):
    """Request codes for subscribing to different data modes."""
    SUBSCRIBE_TICKER = 15      # LTP + LTT only
    SUBSCRIBE_QUOTE = 17       # LTP + OHLC + Volume
    SUBSCRIBE_FULL = 19        # Full data with OI and depth
    DISCONNECT = 12

class ResponseCode(IntEnum):
    """Response codes from WebSocket binary messages."""
    TICKER = 2          # LTP data
    QUOTE = 4           # Quote data
    OI = 5              # Open Interest data
    PREV_CLOSE = 6      # Previous close data
    MARKET_STATUS = 7   # Market open/close notification
    FULL = 8            # Full packet with depth
    DISCONNECT = 50     # Disconnection message

class ExchangeSegment(IntEnum):
    """Exchange segment codes."""
    NSE_EQ = 1
    NSE_FNO = 2
    NSE_CURRENCY = 3
    BSE_EQ = 4
    MCX = 5
    BSE_FNO = 7
    BSE_CURRENCY = 8

SEGMENT_MAP = {
    "NSE_EQ": ExchangeSegment.NSE_EQ,
    "NSE_FNO": ExchangeSegment.NSE_FNO,
    "BSE_EQ": ExchangeSegment.BSE_EQ,
    "MCX": ExchangeSegment.MCX,
}

@dataclass
class TickData:
    """Real-time tick data for an instrument."""
    security_id: int
    exchange_segment: str
    symbol: str = ""
    ltp: float = 0.0
    last_traded_qty: int = 0
    last_traded_time: int = 0
    avg_traded_price: float = 0.0
    volume: int = 0
    total_sell_qty: int = 0
    total_buy_qty: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    prev_close: float = 0.0
    prev_oi: int = 0
    oi: int = 0
    oi_high: int = 0
    oi_low: int = 0
    last_update_time: datetime = field(default_factory=datetime.now)

    def price_change_pct(self) -> float:
        """Calculate price change percentage from previous close."""
        if self.prev_close == 0:
            return 0.0
        return ((self.ltp - self.prev_close) / self.prev_close) * 100

    def oi_change_pct(self) -> float:
        """Calculate OI change percentage from previous close."""
        if self.prev_oi == 0:
            return 0.0
        return ((self.oi - self.prev_oi) / self.prev_oi) * 100


class DhanWebSocketFeed:
    """
    Dhan WebSocket Feed Manager for real-time market data.

    Features:
    - Subscribes to all FNO stocks simultaneously
    - Parses binary WebSocket responses
    - Maintains real-time data cache
    - Handles connection lifecycle and reconnection
    - Thread-safe data access
    """

    def __init__(self, on_tick: Callable[[TickData], None] = None,
                 on_connect: Callable[[], None] = None,
                 on_disconnect: Callable[[str], None] = None,
                 on_error: Callable[[str], None] = None):
        """
        Initialize WebSocket feed manager.

        Args:
            on_tick: Callback for each tick update
            on_connect: Callback when connection established
            on_disconnect: Callback when disconnected
            on_error: Callback for errors
        """
        self.access_token = DHAN_ACCESS_TOKEN
        self.client_id = DHAN_CLIENT_ID

        # Callbacks
        self.on_tick = on_tick
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_error = on_error

        # Connection state
        self.ws = None
        self.is_connected = False
        self.is_running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 5  # seconds

        # Data cache - thread-safe
        self._data_lock = threading.Lock()
        self._tick_data: Dict[int, TickData] = {}  # security_id -> TickData
        self._symbol_to_security: Dict[str, int] = {}  # symbol -> security_id

        # Subscription tracking
        self._subscribed_instruments: List[Dict] = []

        # Event loop for async
        self._loop = None
        self._thread = None

        # Debug mode
        self._debug_mode = False
        self._seen_codes: set = set()

    def _get_websocket_url(self) -> str:
        """Build WebSocket URL with authentication params."""
        return f"{WEBSOCKET_URL}?version=2&token={self.access_token}&clientId={self.client_id}&authType=2"

    def _build_subscribe_message(self, instruments: List[Dict],
                                  request_code: RequestCode = RequestCode.SUBSCRIBE_QUOTE) -> str:
        """
        Build JSON subscription message.

        Args:
            instruments: List of dicts with ExchangeSegment and SecurityId
            request_code: Type of data subscription

        Returns:
            JSON string to send over WebSocket
        """
        # Limit to 100 instruments per message (API limit)
        if len(instruments) > 100:
            raise ValueError("Max 100 instruments per subscription message")

        return json.dumps({
            "RequestCode": int(request_code),
            "InstrumentCount": len(instruments),
            "InstrumentList": instruments
        })

    def _parse_response_header(self, data: bytes) -> Dict:
        """
        Parse 8-byte response header from binary message.

        Returns:
            Dict with response_code, message_length, exchange_segment, security_id
        """
        if len(data) < 8:
            return {}

        # Little endian format
        response_code = struct.unpack('<B', data[0:1])[0]      # 1 byte
        message_length = struct.unpack('<H', data[1:3])[0]     # 2 bytes
        exchange_segment = struct.unpack('<B', data[3:4])[0]   # 1 byte
        security_id = struct.unpack('<I', data[4:8])[0]        # 4 bytes

        return {
            "response_code": response_code,
            "message_length": message_length,
            "exchange_segment": exchange_segment,
            "security_id": security_id
        }

    def _parse_ticker_packet(self, data: bytes, header: Dict) -> TickData:
        """Parse Ticker packet (code 2) - LTP and LTT only."""
        security_id = header["security_id"]

        with self._data_lock:
            tick = self._tick_data.get(security_id, TickData(
                security_id=security_id,
                exchange_segment=self._get_segment_name(header["exchange_segment"])
            ))

        if len(data) >= 16:
            tick.ltp = struct.unpack('<f', data[8:12])[0]
            tick.last_traded_time = struct.unpack('<I', data[12:16])[0]

        tick.last_update_time = datetime.now()
        return tick

    def _parse_quote_packet(self, data: bytes, header: Dict) -> TickData:
        """Parse Quote packet (code 4) - Full trade data with OHLC."""
        security_id = header["security_id"]

        with self._data_lock:
            tick = self._tick_data.get(security_id, TickData(
                security_id=security_id,
                exchange_segment=self._get_segment_name(header["exchange_segment"])
            ))

        if len(data) >= 50:
            tick.ltp = struct.unpack('<f', data[8:12])[0]
            tick.last_traded_qty = struct.unpack('<H', data[12:14])[0]
            tick.last_traded_time = struct.unpack('<I', data[14:18])[0]
            tick.avg_traded_price = struct.unpack('<f', data[18:22])[0]
            tick.volume = struct.unpack('<I', data[22:26])[0]
            tick.total_sell_qty = struct.unpack('<I', data[26:30])[0]
            tick.total_buy_qty = struct.unpack('<I', data[30:34])[0]
            tick.open = struct.unpack('<f', data[34:38])[0]
            tick.close = struct.unpack('<f', data[38:42])[0]
            tick.high = struct.unpack('<f', data[42:46])[0]
            tick.low = struct.unpack('<f', data[46:50])[0]

        tick.last_update_time = datetime.now()
        return tick

    def _parse_oi_packet(self, data: bytes, header: Dict) -> TickData:
        """Parse OI packet (code 5) - Open Interest data."""
        security_id = header["security_id"]

        with self._data_lock:
            tick = self._tick_data.get(security_id, TickData(
                security_id=security_id,
                exchange_segment=self._get_segment_name(header["exchange_segment"])
            ))

        if len(data) >= 12:
            tick.oi = struct.unpack('<I', data[8:12])[0]

        tick.last_update_time = datetime.now()
        return tick

    def _parse_prev_close_packet(self, data: bytes, header: Dict) -> TickData:
        """Parse Previous Close packet (code 6) - Previous day data."""
        security_id = header["security_id"]

        with self._data_lock:
            tick = self._tick_data.get(security_id, TickData(
                security_id=security_id,
                exchange_segment=self._get_segment_name(header["exchange_segment"])
            ))

        if len(data) >= 16:
            tick.prev_close = struct.unpack('<f', data[8:12])[0]
            tick.prev_oi = struct.unpack('<I', data[12:16])[0]

        tick.last_update_time = datetime.now()
        return tick

    def _parse_full_packet(self, data: bytes, header: Dict) -> TickData:
        """Parse Full packet (code 8) - Complete data with OI and depth."""
        security_id = header["security_id"]

        with self._data_lock:
            tick = self._tick_data.get(security_id, TickData(
                security_id=security_id,
                exchange_segment=self._get_segment_name(header["exchange_segment"])
            ))

        if len(data) >= 62:
            tick.ltp = struct.unpack('<f', data[8:12])[0]
            tick.last_traded_qty = struct.unpack('<H', data[12:14])[0]
            tick.last_traded_time = struct.unpack('<I', data[14:18])[0]
            tick.avg_traded_price = struct.unpack('<f', data[18:22])[0]
            tick.volume = struct.unpack('<I', data[22:26])[0]
            tick.total_sell_qty = struct.unpack('<I', data[26:30])[0]
            tick.total_buy_qty = struct.unpack('<I', data[30:34])[0]
            tick.oi = struct.unpack('<I', data[34:38])[0]
            tick.oi_high = struct.unpack('<I', data[38:42])[0]
            tick.oi_low = struct.unpack('<I', data[42:46])[0]
            tick.open = struct.unpack('<f', data[46:50])[0]
            tick.close = struct.unpack('<f', data[50:54])[0]
            tick.high = struct.unpack('<f', data[54:58])[0]
            tick.low = struct.unpack('<f', data[58:62])[0]

        tick.last_update_time = datetime.now()
        return tick

    def _get_segment_name(self, segment_code: int) -> str:
        """Convert segment code to segment name."""
        for name, code in SEGMENT_MAP.items():
            if code == segment_code:
                return name
        return f"UNKNOWN_{segment_code}"

    def _process_message(self, data: bytes) -> Optional[TickData]:
        """Process a binary WebSocket message and return TickData."""
        if not data or len(data) < 8:
            return None

        header = self._parse_response_header(data)
        if not header:
            return None

        response_code = header["response_code"]
        security_id = header["security_id"]

        # Debug: Track received packet types (uncomment to debug)
        if self._debug_mode:
            if response_code not in self._seen_codes:
                self._seen_codes.add(response_code)
                print(f"[DEBUG] First packet of code {response_code}, sec_id {security_id}, len {len(data)}")

        tick = None

        if response_code == ResponseCode.TICKER:
            tick = self._parse_ticker_packet(data, header)
        elif response_code == ResponseCode.QUOTE:
            tick = self._parse_quote_packet(data, header)
        elif response_code == ResponseCode.OI:
            tick = self._parse_oi_packet(data, header)
        elif response_code == ResponseCode.PREV_CLOSE:
            tick = self._parse_prev_close_packet(data, header)
            # Debug: Print prev close when received
            if tick and tick.prev_close > 0:
                print(f"[PREV_CLOSE] {tick.symbol or security_id}: ₹{tick.prev_close:.2f}, PrevOI={tick.prev_oi}")
        elif response_code == ResponseCode.FULL:
            tick = self._parse_full_packet(data, header)
        elif response_code == ResponseCode.DISCONNECT:
            disconnect_code = struct.unpack('<H', data[8:10])[0] if len(data) >= 10 else 0
            error_msg = f"WebSocket disconnected: code {disconnect_code}"
            if self.on_error:
                self.on_error(error_msg)
            return None

        if tick:
            # Update cache
            with self._data_lock:
                # Find symbol for this security_id
                for symbol, sec_id in self._symbol_to_security.items():
                    if sec_id == security_id:
                        tick.symbol = symbol
                        break
                self._tick_data[security_id] = tick

            # Trigger callback
            if self.on_tick:
                self.on_tick(tick)

        return tick

    async def _connect_and_subscribe(self, instruments: List[Dict]):
        """Async method to connect and subscribe to instruments."""
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets package required. Install: pip install websockets")

        url = self._get_websocket_url()

        try:
            async with websockets.connect(url) as ws:
                self.ws = ws
                self.is_connected = True
                self._reconnect_attempts = 0

                print(f"[{datetime.now()}] WebSocket connected to Dhan")

                if self.on_connect:
                    self.on_connect()

                # Subscribe in batches of 100
                for i in range(0, len(instruments), 100):
                    batch = instruments[i:i+100]
                    msg = self._build_subscribe_message(batch, RequestCode.SUBSCRIBE_QUOTE)
                    await ws.send(msg)
                    print(f"[{datetime.now()}] Subscribed to {len(batch)} instruments (batch {i//100 + 1})")

                # Receive loop
                while self.is_running:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        if isinstance(message, bytes):
                            self._process_message(message)
                    except asyncio.TimeoutError:
                        # Send ping to keep alive (handled by library usually)
                        continue
                    except websockets.ConnectionClosed as e:
                        print(f"[{datetime.now()}] WebSocket connection closed: {e}")
                        break

        except Exception as e:
            error_msg = f"WebSocket error: {str(e)}"
            print(f"[{datetime.now()}] {error_msg}")
            if self.on_error:
                self.on_error(error_msg)
        finally:
            self.is_connected = False
            self.ws = None

    def _run_event_loop(self, instruments: List[Dict]):
        """Run the async event loop in a separate thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        while self.is_running:
            try:
                self._loop.run_until_complete(self._connect_and_subscribe(instruments))
            except Exception as e:
                print(f"[{datetime.now()}] Event loop error: {e}")

            # Reconnect logic
            if self.is_running and self._reconnect_attempts < self._max_reconnect_attempts:
                self._reconnect_attempts += 1
                print(f"[{datetime.now()}] Reconnecting... attempt {self._reconnect_attempts}")
                time.sleep(self._reconnect_delay)
            elif self._reconnect_attempts >= self._max_reconnect_attempts:
                error_msg = f"Max reconnect attempts ({self._max_reconnect_attempts}) exceeded"
                print(f"[{datetime.now()}] {error_msg}")
                if self.on_error:
                    self.on_error(error_msg)
                break

        self._loop.close()

    # ===== PUBLIC METHODS =====

    def subscribe_fno_stocks(self, symbols: List[str] = None,
                               request_code: RequestCode = RequestCode.SUBSCRIBE_QUOTE):
        """
        Subscribe to FNO stocks defined in config.

        Args:
            symbols: List of symbols to subscribe (default: all FNO_STOCKS)
            request_code: Type of data (SUBSCRIBE_TICKER, SUBSCRIBE_QUOTE, SUBSCRIBE_FULL)
        """
        instruments = []

        # Use provided symbols or all FNO_STOCKS
        stock_list = symbols if symbols else list(FNO_STOCKS.keys())

        for symbol in stock_list:
            info = FNO_STOCKS.get(symbol)
            if not info:
                print(f"[WARNING] Symbol {symbol} not found in FNO_STOCKS")
                continue

            # Subscribe to equity
            instruments.append({
                "ExchangeSegment": info.get("segment", "NSE_EQ"),
                "SecurityId": str(info["equity_id"])
            })
            self._symbol_to_security[symbol] = info["equity_id"]

            # Subscribe to futures for SMA calculation
            if "futures_id" in info:
                instruments.append({
                    "ExchangeSegment": "NSE_FNO",
                    "SecurityId": str(info["futures_id"])
                })
                self._symbol_to_security[f"{symbol}_FUT"] = info["futures_id"]

        self._subscribed_instruments = instruments
        print(f"[{datetime.now()}] Prepared {len(instruments)} instruments for subscription ({len(stock_list)} stocks)")

    def subscribe_options(self, option_security_ids: Dict[str, int],
                          request_code: RequestCode = RequestCode.SUBSCRIBE_QUOTE):
        """
        Subscribe to option instruments dynamically.

        Args:
            option_security_ids: Dict mapping option key (e.g., "RELIANCE_CE") to security_id
            request_code: Type of data subscription

        This can be called after start() to add option instruments to existing subscription.
        """
        if not option_security_ids:
            return

        instruments = []

        for key, security_id in option_security_ids.items():
            if security_id is None:
                continue

            instruments.append({
                "ExchangeSegment": "NSE_FNO",
                "SecurityId": str(security_id)
            })
            # Store mapping for lookup
            self._symbol_to_security[key] = security_id

        if not instruments:
            print("[WebSocket] No valid option security IDs to subscribe")
            return

        print(f"[{datetime.now()}] Subscribing to {len(instruments)} option instruments")

        # If WebSocket is already connected, send subscription request
        if self.is_connected and self.ws and self._loop:
            subscription_msg = {
                "RequestCode": int(request_code),
                "InstrumentCount": len(instruments),
                "InstrumentList": instruments
            }
            try:
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps(subscription_msg)),
                    self._loop
                )
                print(f"[{datetime.now()}] Option subscription request sent")
            except Exception as e:
                print(f"[WebSocket] Error sending option subscription: {e}")
        else:
            # Add to pending subscriptions (will be sent when connected)
            self._subscribed_instruments.extend(instruments)
            print(f"[{datetime.now()}] Options added to pending subscriptions")

    def get_option_tick(self, symbol: str, option_type: str) -> Optional['TickData']:
        """
        Get tick data for an option instrument.

        Args:
            symbol: Underlying symbol (e.g., "RELIANCE")
            option_type: "CE" or "PE"

        Returns:
            TickData for the option, or None if not available
        """
        key = f"{symbol}_{option_type}"
        security_id = self._symbol_to_security.get(key)
        if security_id is None:
            return None

        with self._data_lock:
            return self._tick_data.get(security_id)

    def start(self):
        """Start the WebSocket connection in a background thread."""
        if self.is_running:
            print("WebSocket already running")
            return

        if not self._subscribed_instruments:
            self.subscribe_fno_stocks()

        self.is_running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(self._subscribed_instruments,),
            daemon=True
        )
        self._thread.start()
        print(f"[{datetime.now()}] WebSocket feed started in background thread")

    def stop(self):
        """Stop the WebSocket connection."""
        self.is_running = False
        if self.ws:
            # Send disconnect message
            try:
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.ws.send(json.dumps({"RequestCode": int(RequestCode.DISCONNECT)})),
                        self._loop
                    )
            except Exception:
                pass

        if self._thread:
            self._thread.join(timeout=5)

        print(f"[{datetime.now()}] WebSocket feed stopped")

    def get_tick(self, symbol: str) -> Optional[TickData]:
        """
        Get the latest tick data for a symbol.

        Args:
            symbol: Stock symbol (e.g., "RELIANCE")

        Returns:
            TickData or None if not available
        """
        security_id = self._symbol_to_security.get(symbol)
        if security_id is None:
            return None

        with self._data_lock:
            return self._tick_data.get(security_id)

    def get_futures_tick(self, symbol: str) -> Optional[TickData]:
        """Get futures tick data for a symbol."""
        security_id = self._symbol_to_security.get(f"{symbol}_FUT")
        if security_id is None:
            return None

        with self._data_lock:
            return self._tick_data.get(security_id)

    def get_all_ticks(self) -> Dict[str, TickData]:
        """Get all tick data as symbol -> TickData dict."""
        with self._data_lock:
            result = {}
            for symbol, sec_id in self._symbol_to_security.items():
                if sec_id in self._tick_data:
                    result[symbol] = self._tick_data[sec_id]
            return result

    def get_stocks_with_entry_signals(self, price_threshold: float = 2.0,
                                       oi_threshold: float = 1.75) -> List[Dict]:
        """
        Get stocks that meet EOS entry conditions.

        Args:
            price_threshold: Price change % threshold (default 2.0)
            oi_threshold: OI change % threshold (default 1.75)

        Returns:
            List of dicts with symbol, direction, price_change_pct, oi_change_pct
        """
        signals = []

        with self._data_lock:
            for symbol, sec_id in self._symbol_to_security.items():
                if symbol.endswith("_FUT"):
                    continue

                tick = self._tick_data.get(sec_id)
                if not tick or tick.prev_close == 0:
                    continue

                price_change = tick.price_change_pct()
                oi_change = tick.oi_change_pct()

                # Check EOS conditions
                if abs(price_change) >= price_threshold and abs(oi_change) >= oi_threshold:
                    direction = "PUT" if price_change > 0 else "CALL"
                    signals.append({
                        "symbol": symbol,
                        "direction": direction,
                        "ltp": tick.ltp,
                        "prev_close": tick.prev_close,
                        "price_change_pct": price_change,
                        "oi": tick.oi,
                        "prev_oi": tick.prev_oi,
                        "oi_change_pct": oi_change
                    })

        # Sort by absolute price change
        signals.sort(key=lambda x: abs(x["price_change_pct"]), reverse=True)
        return signals

    def print_status(self):
        """Print current connection and data status."""
        print("\n" + "=" * 60)
        print("DHAN WEBSOCKET FEED STATUS")
        print("=" * 60)
        print(f"Connected: {self.is_connected}")
        print(f"Running: {self.is_running}")
        print(f"Subscribed Instruments: {len(self._subscribed_instruments)}")
        print(f"Cached Ticks: {len(self._tick_data)}")

        with self._data_lock:
            if self._tick_data:
                print("\n--- Sample Data ---")
                count = 0
                for symbol, sec_id in self._symbol_to_security.items():
                    if sec_id in self._tick_data and count < 5:
                        tick = self._tick_data[sec_id]
                        pct = tick.price_change_pct()
                        print(f"{symbol}: LTP=₹{tick.ltp:.2f}, PrevClose=₹{tick.prev_close:.2f}, "
                              f"Change={pct:+.2f}%, OI={tick.oi:,}")
                        count += 1

        print("=" * 60)

    def enable_debug(self, enabled: bool = True):
        """Enable or disable debug mode."""
        self._debug_mode = enabled
        self._seen_codes = set()
        print(f"[DEBUG] Debug mode {'enabled' if enabled else 'disabled'}")

    def prefetch_prev_close_data(self):
        """
        Pre-fetch previous day close data using REST API.
        This ensures we have prev_close data even if WebSocket doesn't send it.

        Call this before or after start() for reliable prev_close data.
        """
        import requests

        print(f"[{datetime.now()}] Pre-fetching previous close data via REST API...")

        headers = {
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id
        }

        # Group security IDs by segment and track symbol mapping
        segments = {}
        sec_id_to_symbol = {}
        for symbol, info in FNO_STOCKS.items():
            # Add equity
            segment = info.get("segment", "NSE_EQ")
            if segment not in segments:
                segments[segment] = []
            segments[segment].append(info["equity_id"])
            self._symbol_to_security[symbol] = info["equity_id"]
            sec_id_to_symbol[info["equity_id"]] = symbol

            # Add futures
            if "futures_id" in info:
                if "NSE_FNO" not in segments:
                    segments["NSE_FNO"] = []
                segments["NSE_FNO"].append(info["futures_id"])
                self._symbol_to_security[f"{symbol}_FUT"] = info["futures_id"]
                sec_id_to_symbol[info["futures_id"]] = f"{symbol}_FUT"

        loaded_count = 0

        # Fetch market quote for each segment (with rate limiting)
        for i, (segment, sec_ids) in enumerate(segments.items()):
            if i > 0:
                time.sleep(0.3)  # Rate limit: 300ms between requests
            try:
                payload = {segment: sec_ids}
                response = requests.post(
                    "https://api.dhan.co/v2/marketfeed/quote",
                    headers=headers,
                    json=payload,
                    timeout=10
                )

                if response.status_code == 200:
                    data = response.json()
                    # Response format: {"data": {"NSE_EQ": {"1333": {...}, "2885": {...}}}}
                    if "data" in data and segment in data["data"]:
                        segment_data = data["data"][segment]

                        for sec_id_str, item in segment_data.items():
                            sec_id = int(sec_id_str)
                            symbol = sec_id_to_symbol.get(sec_id, "")

                            with self._data_lock:
                                tick = self._tick_data.get(sec_id, TickData(
                                    security_id=sec_id,
                                    exchange_segment=segment
                                ))
                                tick.symbol = symbol

                                # OHLC data - "close" is previous day close during market hours
                                ohlc = item.get("ohlc", {})
                                tick.prev_close = ohlc.get("close", 0)
                                tick.open = ohlc.get("open", 0)
                                tick.high = ohlc.get("high", 0)
                                tick.low = ohlc.get("low", 0)

                                # Other fields
                                tick.ltp = item.get("last_price", 0)
                                tick.volume = item.get("volume", 0)
                                tick.oi = item.get("oi", 0)
                                tick.prev_oi = item.get("oi_day_high", 0)  # Use as proxy if available

                                self._tick_data[sec_id] = tick
                                loaded_count += 1

                        print(f"   ✅ Fetched data for {len(segment_data)} instruments from {segment}")
                else:
                    print(f"   ⚠️ REST API error for {segment}: {response.status_code} - {response.text[:200]}")

            except Exception as e:
                print(f"   ❌ Error fetching {segment}: {e}")

        # Show sample data
        with self._data_lock:
            sample_count = 0
            for sec_id, tick in self._tick_data.items():
                if tick.prev_close > 0 and sample_count < 3:
                    pct = tick.price_change_pct()
                    print(f"   Sample: {tick.symbol}: LTP=₹{tick.ltp:.2f}, PrevClose=₹{tick.prev_close:.2f}, Change={pct:+.2f}%")
                    sample_count += 1

        print(f"[{datetime.now()}] Pre-fetch complete. {loaded_count} instruments loaded.")
