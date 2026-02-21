"""
CRYPTO Live Runner
Mirrors EOS/eos_live_runner.py architecture exactly.

Key differences from EOS:
  - 24/7 market: uses Asia session window (05:30-14:00 IST) instead of NSE hours
  - No option chain: trades the perp directly (no separate options contract lookup)
  - SL is % of entry price (3%), not % of option premium (30%)
  - Funding rate flip is an additional exit condition
  - Paper trade mode: simulates orders without calling Bybit order API
  - Live mode: uses Bybit place_order() for real execution

State Machine (mirrors EOS RunnerState):
  INITIALIZING → PRE_SESSION → SESSION_OPEN → SESSION_CLOSED → STOPPED
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date, time as dt_time
from enum import Enum
from typing import Callable, Dict, List, Optional

import pytz

from .config import CRYPTO_CONFIG, CRYPTO_PAIRS
from .data_fetcher import CryptoDataFetcher
from .strategy_engine import (
    CryptoStrategyEngine, CryptoSignalType, CryptoExitReason, CryptoSignal
)
from .risk_manager import CryptoRiskManager
from .portfolio_manager import CryptoPortfolioManager, CryptoTradeRecord, CryptoDailySnapshot
from .ai_validator import CryptoAIValidator, CryptoSignalData, CryptoValidationResult
from .websocket_feed import CryptoWebSocketFeed, CryptoTickData
from .market_context import CryptoMarketContext

IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# ENUMS / DATACLASSES
# =============================================================================

class CryptoRunnerState(Enum):
    """State machine. Mirrors EOS RunnerState."""
    INITIALIZING    = "INITIALIZING"
    PRE_SESSION     = "PRE_SESSION"      # Before 05:30 IST
    SESSION_OPEN    = "SESSION_OPEN"     # 05:30 - 14:00 IST
    SESSION_CLOSED  = "SESSION_CLOSED"   # After 14:00 IST
    STOPPED         = "STOPPED"


@dataclass
class CryptoLivePosition:
    """Active live position. Mirrors EOS LivePosition."""
    symbol: str
    side: str                      # "LONG" or "SHORT"
    entry_price: float
    entry_time: datetime
    quantity: float                # base asset units
    min_qty: float
    initial_stop_loss: float = field(default=0.0)
    current_stop_loss: float = field(default=0.0)
    highest_favorable_price: float = field(default=0.0)
    trailing_activated: bool = False
    position_id: int = 0           # DB position_id
    funding_rate_at_entry: float = 0.0
    price_change_at_entry: float = 0.0
    volume_spike_at_entry: bool = False
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def __post_init__(self):
        sl_pct = CRYPTO_CONFIG["initial_stop_loss_pct"] / 100.0
        if self.initial_stop_loss == 0.0:
            if self.side == "LONG":
                self.initial_stop_loss = self.entry_price * (1.0 - sl_pct)
            else:
                self.initial_stop_loss = self.entry_price * (1.0 + sl_pct)
        if self.current_stop_loss == 0.0:
            self.current_stop_loss = self.initial_stop_loss
        if self.highest_favorable_price == 0.0:
            self.highest_favorable_price = self.entry_price


@dataclass
class CryptoLiveTrade:
    """Completed trade. Mirrors EOS LiveTrade."""
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    quantity: float
    min_qty: float
    pnl_usdt: float
    pnl_pct: float
    exit_reason: str
    price_change_at_entry: float
    funding_rate_at_entry: float
    volume_spike_at_entry: bool
    trade_id: str


# =============================================================================
# LIVE RUNNER
# =============================================================================

class CryptoLiveRunner:
    """
    CRYPTO Live Trading Runner.
    Mirrors EOSLiveRunner class architecture exactly.

    Orchestrates: WebSocket feed → Strategy → AI Validator → Risk Manager →
                  Portfolio Manager → Order Execution.
    """

    def __init__(
        self,
        symbols: List[str] = None,
        initial_capital_usdt: float = None,
        paper_trade: bool = True,
        on_signal: Callable = None,
        on_trade: Callable = None,
    ) -> None:
        self.symbols = symbols or list(CRYPTO_PAIRS.keys())
        self.config = CRYPTO_CONFIG
        self.paper_trade = paper_trade or self.config.get("paper_trade", True)

        # Callbacks (mirrors EOS pattern)
        self.on_signal = on_signal
        self.on_trade  = on_trade

        # State machine
        self.state: CryptoRunnerState = CryptoRunnerState.INITIALIZING
        self.is_running: bool = False
        self._stop_event: threading.Event = threading.Event()

        # Components (mirrors EOS component composition)
        self.feed:             Optional[CryptoWebSocketFeed] = None
        self.data_fetcher:     CryptoDataFetcher = CryptoDataFetcher()
        self.risk_manager:     CryptoRiskManager = CryptoRiskManager(
            initial_capital_usdt or self.config["total_capital_usdt"]
        )
        self.portfolio_manager: CryptoPortfolioManager = CryptoPortfolioManager()
        self.strategy_engine:  CryptoStrategyEngine = CryptoStrategyEngine(self.data_fetcher)
        self.ai_validator:     CryptoAIValidator = CryptoAIValidator()
        self.market_ctx_mgr:   CryptoMarketContext = CryptoMarketContext()

        # Session tracking (mirrors EOS session tracking)
        self.positions: Dict[str, CryptoLivePosition] = {}
        self.trades_today: List[CryptoLiveTrade] = []
        self.traded_symbols_today: set = set()
        self.daily_pnl_usdt: float = 0.0
        self.starting_capital_usdt: float = (
            initial_capital_usdt or self.config["total_capital_usdt"]
        )

        # Session boundaries (IST)
        self.session_start  = dt_time(5, 30)
        self.session_end    = dt_time(14, 0)
        self.final_exit     = dt_time(13, 50)
        self.pre_session_start = dt_time(5, 20)

        self._session_id: Optional[str] = None
        self._trade_counter: int = 0
        self._main_thread: Optional[threading.Thread] = None
        self._tick_interval: float = 1.0   # seconds between main loop ticks
        self._last_scan_time: float = 0.0
        self._scan_interval: float = 30.0  # scan for signals every 30s

        mode_str = "PAPER" if self.paper_trade else "LIVE"
        print(f"\n{'='*60}")
        print(f"  CRYPTO LIVE RUNNER | Mode: {mode_str}")
        print(f"  Symbols: {self.symbols}")
        print(f"  Capital: ${self.starting_capital_usdt:.2f} USDT")
        print(f"  Session: {self.session_start.strftime('%H:%M')} - "
              f"{self.session_end.strftime('%H:%M')} IST (Asia)")
        print(f"{'='*60}\n")

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    def start(self) -> None:
        """
        Start the live runner. Mirrors EOSLiveRunner.start().
        Initializes components, starts WebSocket, begins main loop.
        """
        if self.is_running:
            print("[CryptoRunner] Already running.")
            return

        print("[CryptoRunner] Starting...")
        self.is_running = True
        self.state = CryptoRunnerState.INITIALIZING

        # Reset daily state
        self._reset_daily_state()

        # Start new DB session
        today = datetime.now(IST).strftime("%Y-%m-%d")
        mode = "PAPER" if self.paper_trade else "LIVE"
        self._session_id = self.portfolio_manager.start_session(
            start_date=today,
            end_date=today,
            symbols=self.symbols,
            initial_capital_usdt=self.starting_capital_usdt,
            config=self.config,
            mode=mode,
        )

        # Initialize and start WebSocket feed
        self._init_feed()

        # Start main loop in daemon thread (mirrors EOS threading pattern)
        self._main_thread = threading.Thread(
            target=self._main_loop,
            daemon=True,
            name="CryptoMainLoop",
        )
        self._main_thread.start()
        print("[CryptoRunner] Main loop started.")

    def stop(self) -> None:
        """
        Graceful shutdown. Mirrors EOSLiveRunner.stop().
        Closes all positions → saves snapshot → ends session.
        """
        if not self.is_running:
            return

        print("\n[CryptoRunner] Stopping...")
        self.is_running = False
        self._stop_event.set()

        # Close all open positions at market price
        if self.positions:
            print(f"[CryptoRunner] Closing {len(self.positions)} open positions...")
            self._close_all_positions(CryptoExitReason.TIME_EXIT)

        # Stop WebSocket
        if self.feed:
            self.feed.stop()

        # Save final snapshot and end session
        self._save_daily_snapshot()
        self._end_session()
        self.state = CryptoRunnerState.STOPPED

        self._print_session_summary()
        print("[CryptoRunner] Stopped.")

    def _reset_daily_state(self) -> None:
        """Reset all daily counters. Mirrors EOSLiveRunner reset logic."""
        self.positions = {}
        self.trades_today = []
        self.traded_symbols_today = set()
        self.daily_pnl_usdt = 0.0
        self._trade_counter = 0
        self.strategy_engine.reset_daily_state()
        self.risk_manager.reset_daily_state()

    # =========================================================================
    # FEED INITIALIZATION
    # =========================================================================

    def _init_feed(self) -> None:
        """
        Initialize Bybit WebSocket feed.
        Mirrors EOSLiveRunner._init_feed().
        """
        self.feed = CryptoWebSocketFeed(
            on_tick=self._on_tick,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_error=self._on_error,
        )
        self.feed.subscribe_pairs(self.symbols)

        # Pre-fetch market data before WebSocket starts (mirrors EOS prefetch_prev_close_data)
        self.feed.prefetch_market_data()

        self.feed.start()
        time.sleep(2)  # Allow WebSocket to connect

    # =========================================================================
    # WEBSOCKET CALLBACKS (mirrors EOS _on_tick, _on_connect, etc.)
    # =========================================================================

    def _on_tick(self, tick: CryptoTickData) -> None:
        """Called on every WebSocket tick update."""
        pass  # Main loop handles signal scanning; tick data is cached

    def _on_connect(self) -> None:
        print("[CryptoRunner] WebSocket connected.")

    def _on_disconnect(self, reason: str) -> None:
        print(f"[CryptoRunner] WebSocket disconnected: {reason}")

    def _on_error(self, error: str) -> None:
        print(f"[CryptoRunner] WebSocket error: {error}")

    # =========================================================================
    # MAIN LOOP (mirrors EOSLiveRunner._main_loop exactly)
    # =========================================================================

    def _main_loop(self) -> None:
        """
        State machine main loop.
        Runs every _tick_interval seconds.
        Mirrors EOSLiveRunner._main_loop() state machine.
        """
        print("[CryptoRunner] Main loop running...")

        while self.is_running and not self._stop_event.is_set():
            try:
                now = datetime.now(IST)
                t = now.time()

                # --- State transitions ---
                if self.state == CryptoRunnerState.INITIALIZING:
                    if t >= self.pre_session_start:
                        self.state = CryptoRunnerState.PRE_SESSION
                        print(f"[CryptoRunner] State -> PRE_SESSION")

                elif self.state == CryptoRunnerState.PRE_SESSION:
                    if t >= self.session_start:
                        self.state = CryptoRunnerState.SESSION_OPEN
                        print(f"[CryptoRunner] State -> SESSION_OPEN "
                              f"({self.session_start.strftime('%H:%M')} IST)")

                elif self.state == CryptoRunnerState.SESSION_OPEN:
                    if t >= self.session_end:
                        self.state = CryptoRunnerState.SESSION_CLOSED
                        print(f"[CryptoRunner] State -> SESSION_CLOSED")
                        self._close_all_positions(CryptoExitReason.TIME_EXIT)
                        self._save_daily_snapshot()
                        self._end_session()
                    else:
                        # --- Active trading ---
                        # Scan for entry signals (every 30s, not every tick)
                        now_ts = time.time()
                        if now_ts - self._last_scan_time >= self._scan_interval:
                            self._check_entry_signals(now)
                            self._last_scan_time = now_ts

                        # Monitor open positions every tick (1s)
                        if self.positions:
                            self._monitor_positions(now)

                        # Time-based exit (10 min before session end)
                        if t >= self.final_exit and self.positions:
                            print("[CryptoRunner] Final exit time reached. Closing all positions.")
                            self._close_all_positions(CryptoExitReason.TIME_EXIT)

                elif self.state == CryptoRunnerState.SESSION_CLOSED:
                    # Wait for next session (could be next day)
                    print("[CryptoRunner] Session closed. Waiting for next session...")
                    self.is_running = False
                    break

            except Exception as e:
                print(f"[CryptoRunner] Main loop error: {e}")

            time.sleep(self._tick_interval)

        print("[CryptoRunner] Main loop exited.")

    # =========================================================================
    # ENTRY SIGNAL PROCESSING (mirrors EOSLiveRunner._check_entry_signals)
    # =========================================================================

    def _check_entry_signals(self, current_time: datetime) -> None:
        """
        Scan for entry signals using WebSocket cached data.
        Mirrors EOSLiveRunner._check_entry_signals().
        """
        # Skip first 5 min of session (09:15-09:20 equivalent)
        session_open_dt = current_time.replace(
            hour=self.session_start.hour,
            minute=self.session_start.minute,
            second=0,
        )
        elapsed_minutes = (current_time - session_open_dt).total_seconds() / 60.0
        if elapsed_minutes < 5:
            return

        # Get pairs with signals from WebSocket cached data
        signal_candidates = self.feed.get_pairs_with_entry_signals() if self.feed else []

        for signal_data in signal_candidates:
            symbol = signal_data["symbol"]

            # Skip if already in position or traded today
            if symbol in self.positions:
                continue
            if not self.config.get("allow_reentry", False) and symbol in self.traded_symbols_today:
                continue

            # Check risk limits
            current_price = signal_data["last_price"]
            can_trade, reason = self.risk_manager.can_take_trade(symbol, current_price)
            if not can_trade:
                print(f"[CryptoRunner] {symbol}: risk check failed - {reason}")
                continue

            self._process_entry_signal(signal_data, current_time)

    def _process_entry_signal(self, signal_data: Dict, current_time: datetime) -> None:
        """
        Process a detected entry signal: validate with AI, then open position.
        Mirrors EOSLiveRunner._process_entry_signal().
        """
        symbol = signal_data["symbol"]
        direction = signal_data["direction"]
        entry_price = signal_data["last_price"]
        price_change_pct = signal_data["price_change_pct"]
        funding_rate = signal_data["funding_rate"]
        volume_spike = signal_data["volume_spike"]
        funding_extreme = signal_data["funding_extreme"]

        pair_info = CRYPTO_PAIRS.get(symbol, {})
        min_qty = pair_info.get("min_qty", 1.0)
        quantity = min_qty * self.config["contracts_per_trade"]

        sl_pct = self.config["initial_stop_loss_pct"] / 100.0
        if direction == "LONG":
            stop_loss = entry_price * (1.0 - sl_pct)
        else:
            stop_loss = entry_price * (1.0 + sl_pct)

        # Build signal data for AI validator
        signal_obj = CryptoSignalData(
            symbol=symbol,
            signal_type=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            price_change_pct=price_change_pct,
            funding_rate=funding_rate,
            funding_rate_extreme=funding_extreme,
            volume_spike=volume_spike,
            entry_date=current_time.strftime("%Y-%m-%d"),
            entry_time=current_time.strftime("%H:%M"),
            quantity=quantity,
        )

        # Build market context for AI
        tick = self.feed.get_tick("BTCUSDT") if self.feed else None
        btc_change = tick.price_change_pct() if tick else None
        btc_funding = tick.funding_rate if tick else None
        market_ctx = self.market_ctx_mgr.build_context(
            btc_price=tick.last_price if tick else None,
            btc_change_pct=btc_change,
            btc_funding_rate=btc_funding,
        )

        # AI Validation
        print(f"[CryptoRunner] Validating {direction} signal on {symbol} @ ${entry_price:.4f} "
              f"(price_chg={price_change_pct:.2f}%)")
        validation = self.ai_validator.validate(signal_obj, market_ctx)

        # Log validation to DB
        self._log_validation(
            symbol=symbol,
            signal_type=direction,
            result=validation.result.value,
            confidence=validation.confidence,
            reason=validation.reason,
            tier_used=validation.tier_used,
            latency_ms=validation.latency_ms,
            tokens_used=validation.tokens_used,
            price_change_pct=price_change_pct,
            funding_rate=funding_rate,
            volume_spike=volume_spike,
            entry_price=entry_price,
        )

        if validation.result == CryptoValidationResult.REJECT:
            print(f"[CryptoRunner] {symbol}: REJECTED by AI - {validation.reason}")
            return

        print(f"[CryptoRunner] {symbol}: APPROVED (confidence={validation.confidence:.2f}) - {validation.reason}")

        # Execute order
        actual_entry = self._execute_entry(symbol, direction, quantity, entry_price)
        if actual_entry is None:
            return

        # Create live position
        position = CryptoLivePosition(
            symbol=symbol,
            side=direction,
            entry_price=actual_entry,
            entry_time=current_time,
            quantity=quantity,
            min_qty=min_qty,
            funding_rate_at_entry=funding_rate,
            price_change_at_entry=price_change_pct,
            volume_spike_at_entry=volume_spike,
        )

        # Register with DB
        position.position_id = self.portfolio_manager.open_position(
            session_id=self._session_id,
            symbol=symbol,
            side=direction,
            entry_date=current_time.strftime("%Y-%m-%d"),
            entry_time=current_time.strftime("%H:%M:%S"),
            entry_price=actual_entry,
            quantity=quantity,
            min_qty=min_qty,
            stop_loss=position.initial_stop_loss,
        )

        # Register with risk manager
        self.risk_manager.register_entry(
            symbol=symbol,
            side=direction,
            entry_price=actual_entry,
            quantity=quantity,
            min_qty=min_qty,
        )

        self.positions[symbol] = position
        self.traded_symbols_today.add(symbol)
        self._trade_counter += 1

        print(f"[CryptoRunner] Position opened: {direction} {symbol} @ ${actual_entry:.4f} | "
              f"qty={quantity} | SL=${position.initial_stop_loss:.4f}")

        if self.on_signal:
            self.on_signal({
                "symbol": symbol, "direction": direction,
                "entry_price": actual_entry, "stop_loss": position.initial_stop_loss,
                "quantity": quantity, "validation": validation.to_dict(),
            })

    def _execute_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        expected_price: float,
    ) -> Optional[float]:
        """
        Execute entry order via Bybit API (or simulate in paper mode).
        Mirrors EOSLiveRunner order execution pattern.
        """
        bybit_side = "Buy" if side == "LONG" else "Sell"

        if self.paper_trade:
            print(f"[CryptoRunner] PAPER: Simulating {bybit_side} {symbol} qty={quantity}")
            return expected_price  # Use current market price

        # Live trading: place market order
        resp = self.data_fetcher.place_order(
            symbol=symbol,
            side=bybit_side,
            order_type="Market",
            qty=quantity,
        )
        if resp.get("error"):
            print(f"[CryptoRunner] Order failed for {symbol}: {resp['error']}")
            return None

        # In live mode, fetch actual fill price
        actual_price = self.data_fetcher.get_current_price(symbol) or expected_price
        print(f"[CryptoRunner] LIVE: Order placed {symbol} {bybit_side} qty={quantity} @ ~${actual_price:.4f}")
        return actual_price

    def _execute_exit(
        self,
        symbol: str,
        side: str,
        quantity: float,
        expected_price: float,
    ) -> Optional[float]:
        """Execute exit order (or simulate in paper mode)."""
        # To close a LONG → Sell; to close a SHORT → Buy
        bybit_side = "Sell" if side == "LONG" else "Buy"

        if self.paper_trade:
            return expected_price

        resp = self.data_fetcher.place_order(
            symbol=symbol,
            side=bybit_side,
            order_type="Market",
            qty=quantity,
            reduce_only=True,
        )
        if resp.get("error"):
            print(f"[CryptoRunner] Exit order failed for {symbol}: {resp['error']}")
            return None

        return self.data_fetcher.get_current_price(symbol) or expected_price

    # =========================================================================
    # POSITION MONITORING (mirrors EOSLiveRunner._monitor_positions)
    # =========================================================================

    def _monitor_positions(self, current_time: datetime) -> None:
        """
        Check exit conditions for all open positions.
        Mirrors EOSLiveRunner._monitor_positions().
        """
        symbols_to_close: List[tuple] = []

        for symbol, position in list(self.positions.items()):
            # Get current price from WebSocket cache
            tick = self.feed.get_tick(symbol) if self.feed else None
            if tick and tick.last_price > 0:
                current_price = tick.last_price
                current_funding = tick.funding_rate
            else:
                # Fallback to REST API
                current_price = self.data_fetcher.get_current_price(symbol)
                current_funding = 0.0
                if not current_price:
                    continue

            # Update risk manager with current price
            self.risk_manager.update_position_price(symbol, current_price)

            # Update DB position
            self.portfolio_manager.update_position(
                position.position_id,
                current_price=current_price,
                trailing_stop=position.current_stop_loss,
                highest_favorable_price=position.highest_favorable_price,
            )

            # Check exit conditions
            exit_reason = self._check_exit_conditions(position, current_price, current_funding, current_time)
            if exit_reason:
                symbols_to_close.append((symbol, current_price, exit_reason))

        # Close positions outside the iteration (avoid dict size change during iteration)
        for symbol, exit_price, reason in symbols_to_close:
            self._close_position(symbol, exit_price, reason)

    def _check_exit_conditions(
        self,
        position: CryptoLivePosition,
        current_price: float,
        current_funding_rate: float,
        current_time: datetime,
    ) -> Optional[CryptoExitReason]:
        """
        Check all exit conditions in priority order.
        Mirrors EOSLiveRunner._check_exit_conditions().
        """
        t = current_time.time()

        # 1. Time exit
        if t >= self.final_exit:
            return CryptoExitReason.TIME_EXIT

        # 2. Initial SL
        if position.side == "LONG":
            if current_price <= position.initial_stop_loss:
                return CryptoExitReason.INITIAL_STOP_LOSS
        else:  # SHORT
            if current_price >= position.initial_stop_loss:
                return CryptoExitReason.INITIAL_STOP_LOSS

        # 3. Update trailing SL, then check
        self._update_trailing_sl(position, current_price)
        if position.trailing_activated:
            if position.side == "LONG" and current_price <= position.current_stop_loss:
                return CryptoExitReason.TRAILING_STOP_LOSS
            elif position.side == "SHORT" and current_price >= position.current_stop_loss:
                return CryptoExitReason.TRAILING_STOP_LOSS

        # 4. SMA crossover (after 60 min)
        hold_minutes = (current_time - position.entry_time).total_seconds() / 60.0
        if hold_minutes >= self.config["sma_active_after_minutes"]:
            closes = self.feed.get_kline_closes(position.symbol) if self.feed else []
            if len(closes) >= self.config["sma_slow"]:
                sma_fast = CryptoDataFetcher.calculate_sma(closes, self.config["sma_fast"])
                sma_slow = CryptoDataFetcher.calculate_sma(closes, self.config["sma_slow"])
                if sma_fast and sma_slow:
                    if position.side == "LONG" and sma_fast < sma_slow:
                        return CryptoExitReason.SMA_CROSSOVER
                    elif position.side == "SHORT" and sma_fast > sma_slow:
                        return CryptoExitReason.SMA_CROSSOVER

        # 5. Funding rate flip (crypto-specific)
        funding_threshold = self.config["funding_rate_threshold"] * 2
        if position.side == "LONG" and current_funding_rate > funding_threshold:
            return CryptoExitReason.FUNDING_FLIP
        elif position.side == "SHORT" and current_funding_rate < -funding_threshold:
            return CryptoExitReason.FUNDING_FLIP

        return None

    def _update_trailing_sl(self, position: CryptoLivePosition, current_price: float) -> None:
        """
        Update trailing stop loss. Mirrors EOSLiveRunner._update_trailing_sl().
        Trail by 1% for every 1% favorable move.
        """
        trail_pct = self.config["trailing_sl_amount_pct"] / 100.0

        if position.side == "LONG":
            if current_price > position.highest_favorable_price:
                position.highest_favorable_price = current_price
                new_sl = position.highest_favorable_price * (1.0 - trail_pct)
                if new_sl > position.current_stop_loss:
                    position.current_stop_loss = new_sl
                    position.trailing_activated = True
        else:  # SHORT
            if current_price < position.highest_favorable_price:
                position.highest_favorable_price = current_price
                new_sl = position.highest_favorable_price * (1.0 + trail_pct)
                if new_sl < position.current_stop_loss:
                    position.current_stop_loss = new_sl
                    position.trailing_activated = True

    # =========================================================================
    # POSITION CLOSING
    # =========================================================================

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: CryptoExitReason,
    ) -> None:
        """
        Close a single position. Mirrors EOSLiveRunner._close_position().
        """
        position = self.positions.get(symbol)
        if not position:
            return

        # Execute exit order
        actual_exit = self._execute_exit(symbol, position.side, position.quantity, exit_price)
        if actual_exit is None:
            actual_exit = exit_price

        # Calculate PnL
        if position.side == "LONG":
            pnl_usdt = (actual_exit - position.entry_price) * position.quantity
        else:
            pnl_usdt = (position.entry_price - actual_exit) * position.quantity

        pnl_pct = (pnl_usdt / (position.entry_price * position.quantity)) * 100.0
        hold_minutes = (datetime.now(IST) - position.entry_time).total_seconds() / 60.0

        self.daily_pnl_usdt += pnl_usdt

        # Create trade record
        now = datetime.now(IST)
        trade = CryptoLiveTrade(
            symbol=symbol,
            side=position.side,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=now,
            exit_price=actual_exit,
            quantity=position.quantity,
            min_qty=position.min_qty,
            pnl_usdt=round(pnl_usdt, 4),
            pnl_pct=round(pnl_pct, 4),
            exit_reason=exit_reason.value,
            price_change_at_entry=position.price_change_at_entry,
            funding_rate_at_entry=position.funding_rate_at_entry,
            volume_spike_at_entry=position.volume_spike_at_entry,
            trade_id=position.trade_id,
        )
        self.trades_today.append(trade)

        # Record to DB
        self._record_trade_to_db(trade, hold_minutes)

        # Update risk manager
        self.risk_manager.register_exit(symbol, actual_exit, exit_reason.value, position.side)

        # Close DB position
        self.portfolio_manager.close_position(position.position_id)

        # Remove from active positions
        del self.positions[symbol]

        pnl_color = "\033[92m" if pnl_usdt >= 0 else "\033[91m"
        print(f"[CryptoRunner] Closed {position.side} {symbol} | {exit_reason.value} | "
              f"{pnl_color}PnL: ${pnl_usdt:+.4f} ({pnl_pct:+.2f}%)\033[0m | "
              f"hold={hold_minutes:.1f}min")

        if self.on_trade:
            self.on_trade({
                "symbol": symbol, "side": position.side,
                "entry_price": position.entry_price, "exit_price": actual_exit,
                "pnl_usdt": round(pnl_usdt, 4), "pnl_pct": round(pnl_pct, 4),
                "exit_reason": exit_reason.value,
            })

    def _close_all_positions(self, exit_reason: CryptoExitReason) -> None:
        """Close all open positions. Mirrors EOSLiveRunner._close_all_positions()."""
        for symbol in list(self.positions.keys()):
            tick = self.feed.get_tick(symbol) if self.feed else None
            current_price = tick.last_price if tick and tick.last_price > 0 \
                else self.data_fetcher.get_current_price(symbol) or 0.0
            if current_price > 0:
                self._close_position(symbol, current_price, exit_reason)

    # =========================================================================
    # DATABASE RECORDING
    # =========================================================================

    def _record_trade_to_db(self, trade: CryptoLiveTrade, hold_duration: float) -> None:
        """Record completed trade to DB. Mirrors EOSLiveRunner._record_trade_to_db()."""
        from .portfolio_manager import CryptoTradeRecord
        record = CryptoTradeRecord(
            trade_id=trade.trade_id,
            session_id=self._session_id or "",
            symbol=trade.symbol,
            side=trade.side,
            entry_date=trade.entry_time.strftime("%Y-%m-%d"),
            entry_time=trade.entry_time.strftime("%H:%M:%S"),
            entry_price=trade.entry_price,
            exit_date=trade.exit_time.strftime("%Y-%m-%d"),
            exit_time=trade.exit_time.strftime("%H:%M:%S"),
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            min_qty=trade.min_qty,
            pnl_usdt=trade.pnl_usdt,
            pnl_pct=trade.pnl_pct,
            exit_reason=trade.exit_reason,
            hold_duration_minutes=round(hold_duration, 2),
            price_change_at_entry=trade.price_change_at_entry,
            funding_rate_at_entry=trade.funding_rate_at_entry,
            volume_spike_at_entry=trade.volume_spike_at_entry,
        )
        self.portfolio_manager.record_trade(record)

    def _save_daily_snapshot(self) -> None:
        """Save daily session snapshot to DB. Mirrors EOSLiveRunner._save_daily_snapshot()."""
        if not self._session_id:
            return

        today = datetime.now(IST).strftime("%Y-%m-%d")
        winning = sum(1 for t in self.trades_today if t.pnl_usdt > 0)
        losing  = sum(1 for t in self.trades_today if t.pnl_usdt <= 0)
        ending_cap = self.starting_capital_usdt + self.daily_pnl_usdt
        daily_pnl_pct = (self.daily_pnl_usdt / self.starting_capital_usdt) * 100.0 \
            if self.starting_capital_usdt > 0 else 0.0

        from .portfolio_manager import CryptoDailySnapshot
        snapshot = CryptoDailySnapshot(
            date=today,
            starting_capital_usdt=self.starting_capital_usdt,
            ending_capital_usdt=ending_cap,
            daily_pnl_usdt=round(self.daily_pnl_usdt, 4),
            daily_pnl_pct=round(daily_pnl_pct, 4),
            trades_taken=len(self.trades_today),
            winning_trades=winning,
            losing_trades=losing,
            max_drawdown_usdt=0.0,
            cumulative_pnl_usdt=round(self.daily_pnl_usdt, 4),
            session_id=self._session_id,
        )
        self.portfolio_manager.record_daily_snapshot(snapshot)

    def _end_session(self) -> None:
        """Finalize session in DB. Mirrors EOSLiveRunner._end_session()."""
        if not self._session_id:
            return

        total = len(self.trades_today)
        wins  = sum(1 for t in self.trades_today if t.pnl_usdt > 0)
        win_rate = (wins / total * 100) if total else 0
        ending_cap = self.starting_capital_usdt + self.daily_pnl_usdt

        self.portfolio_manager.end_session(
            session_id=self._session_id,
            final_capital_usdt=ending_cap,
            metrics={
                "total_trades":      total,
                "win_rate":          round(win_rate, 1),
                "total_pnl_usdt":    round(self.daily_pnl_usdt, 4),
                "max_drawdown_usdt": 0.0,
            },
        )

    def _log_validation(self, **kwargs) -> None:
        """Log AI validation to DB."""
        if self._session_id:
            self.portfolio_manager.record_validation(
                session_id=self._session_id,
                **kwargs,
            )

    # =========================================================================
    # REPORTING
    # =========================================================================

    def get_status(self) -> Dict:
        """Return full runner status. Mirrors EOSLiveRunner.get_status()."""
        return {
            "state":              self.state.value,
            "is_running":         self.is_running,
            "paper_trade":        self.paper_trade,
            "session_id":         self._session_id,
            "daily_pnl_usdt":     round(self.daily_pnl_usdt, 2),
            "trades_today":       len(self.trades_today),
            "open_positions":     len(self.positions),
            "starting_capital":   self.starting_capital_usdt,
            "ws_connected":       self.feed.is_connected if self.feed else False,
            "positions":          {
                sym: {
                    "side":        pos.side,
                    "entry_price": pos.entry_price,
                    "quantity":    pos.quantity,
                    "sl":          pos.current_stop_loss,
                    "trailing_on": pos.trailing_activated,
                }
                for sym, pos in self.positions.items()
            },
        }

    def print_status(self) -> None:
        """Print formatted status. Mirrors EOSLiveRunner.print_status()."""
        s = self.get_status()
        mode = "PAPER" if self.paper_trade else "LIVE"
        print(f"\n{'='*55}")
        print(f"  CRYPTO RUNNER STATUS | {mode} | {s['state']}")
        print(f"{'='*55}")
        print(f"  Session:      {s['session_id']}")
        print(f"  Daily PnL:   ${s['daily_pnl_usdt']:>+.2f} USDT")
        print(f"  Trades Today: {s['trades_today']}")
        print(f"  Open Pos:     {s['open_positions']}")
        print(f"  WS Connected: {s['ws_connected']}")
        if s["positions"]:
            print(f"\n  Open Positions:")
            for sym, pos in s["positions"].items():
                print(f"    {sym} [{pos['side']}] @ ${pos['entry_price']:.4f} | "
                      f"SL=${pos['sl']:.4f} | trail={'ON' if pos['trailing_on'] else 'OFF'}")
        print(f"{'='*55}\n")

    def _print_session_summary(self) -> None:
        """Print end-of-session summary. Mirrors EOSLiveRunner._print_session_summary()."""
        total = len(self.trades_today)
        wins  = sum(1 for t in self.trades_today if t.pnl_usdt > 0)
        mode  = "PAPER" if self.paper_trade else "LIVE"

        print(f"\n{'='*55}")
        print(f"  CRYPTO SESSION SUMMARY | {mode}")
        print(f"{'='*55}")
        print(f"  Total Trades:   {total}")
        print(f"  Wins/Losses:    {wins}/{total - wins}")
        print(f"  Win Rate:       {wins/total*100:.1f}%" if total else "  Win Rate:       N/A")
        print(f"  Daily PnL:     ${self.daily_pnl_usdt:>+.2f} USDT")
        print(f"  Ending Capital:${self.starting_capital_usdt + self.daily_pnl_usdt:.2f} USDT")
        if self.trades_today:
            print(f"\n  Trade Log:")
            for t in self.trades_today:
                color = "\033[92m" if t.pnl_usdt >= 0 else "\033[91m"
                print(f"    {t.symbol} [{t.side}] | {t.exit_reason} | "
                      f"{color}${t.pnl_usdt:>+.4f}\033[0m")
        print(f"{'='*55}\n")


# =============================================================================
# ENTRY POINT (for subprocess execution from dashboard)
# =============================================================================

if __name__ == "__main__":
    import sys
    import json as _json

    paper = True
    symbols = list(CRYPTO_PAIRS.keys())
    capital = CRYPTO_CONFIG["total_capital_usdt"]

    if len(sys.argv) > 1:
        try:
            args = _json.loads(sys.argv[1])
            paper   = args.get("paper_trade", True)
            symbols = args.get("symbols", symbols)
            capital = args.get("initial_capital_usdt", capital)
        except Exception:
            pass

    runner = CryptoLiveRunner(
        symbols=symbols,
        initial_capital_usdt=capital,
        paper_trade=paper,
    )

    try:
        runner.start()
        while runner.is_running:
            runner.print_status()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[CryptoRunner] KeyboardInterrupt — stopping...")
    finally:
        runner.stop()
