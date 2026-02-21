# Last updated: 2026-02-21
"""
EOS Live Runner - Real-time Trading System
Mirrors the EOSBacktester structure but operates on live WebSocket data.

Features:
- Real-time WebSocket data feed
- Live signal detection and validation
- Position management with trailing SL
- Risk management integration
- Portfolio tracking with SQLite
- Scheduled market hours operation
"""

import sys
import time
import threading
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

# Force UTF-8 so Rs / rupee symbol (₹) never crashes on Windows cp1252
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from dataclasses import dataclass, field
from enum import Enum

from .config import EOS_CONFIG, FNO_STOCKS
from .eos_websocket_feed import DhanWebSocketFeed, TickData
from .eos_strategy_engine import (
    EOSStrategyEngine, SignalType, ExitReason, Position, Signal
)
from .eos_risk_manager import EOSRiskManager
from .eos_portfolio_manager import EOSPortfolioManager, TradeRecord, DailySnapshot
from .eos_option_chain import EOSOptionChainManager, OptionData, ATMOption


class RunnerState(Enum):
    """Live runner operational state."""
    INITIALIZING = "INITIALIZING"
    PRE_MARKET = "PRE_MARKET"
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSED = "MARKET_CLOSED"
    STOPPED = "STOPPED"


@dataclass
class LivePosition:
    """Represents an active live position."""
    symbol: str
    option_type: str          # "CALL" or "PUT"
    strike_price: float
    entry_price: float
    entry_time: datetime
    quantity: int
    lot_size: int
    initial_stop_loss: float
    current_stop_loss: float
    highest_price: float
    security_id: int = 0
    option_security_id: int = 0
    trailing_activated: bool = False

    def __post_init__(self):
        if self.initial_stop_loss == 0:
            self.initial_stop_loss = self.entry_price * (1 - EOS_CONFIG["initial_stop_loss_pct"] / 100)
        if self.current_stop_loss == 0:
            self.current_stop_loss = self.initial_stop_loss
        if self.highest_price == 0:
            self.highest_price = self.entry_price


@dataclass
class LiveTrade:
    """Completed trade record."""
    symbol: str
    option_type: str
    strike_price: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    quantity: int
    lot_size: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    price_change_at_entry: float
    oi_change_at_entry: float


class EOSLiveRunner:
    """
    EOS Live Trading Runner - Operates on real-time WebSocket data.

    Architecture mirrors EOSBacktester:
    - WebSocket Feed → Signal Detection → Position Management → Exit Monitoring

    Key Features:
    1. Pre-market data loading (9:10 AM)
    2. Real-time signal detection from 9:15 AM
    3. Trailing stop loss monitoring
    4. SMA-based exit (after 1 hour)
    5. Final exit at 3:18 PM
    """

    def __init__(self,
                 symbols: List[str] = None,
                 initial_capital: float = 500000,
                 paper_trade: bool = True,
                 on_signal: Callable = None,
                 on_trade: Callable = None):
        """
        Initialize EOS Live Runner.

        Args:
            symbols: List of symbols to monitor (default: all FNO_STOCKS)
            initial_capital: Starting capital for risk management
            paper_trade: If True, simulate trades without execution
            on_signal: Callback when signal detected
            on_trade: Callback when trade completed
        """
        self.symbols = symbols or list(FNO_STOCKS.keys())
        self.config = EOS_CONFIG
        self.paper_trade = paper_trade

        # Callbacks
        self.on_signal = on_signal
        self.on_trade = on_trade

        # State
        self.state = RunnerState.INITIALIZING
        self.is_running = False
        self._stop_event = threading.Event()

        # Components
        self.feed: Optional[DhanWebSocketFeed] = None
        self.risk_manager = EOSRiskManager(initial_capital)
        self.portfolio_manager = EOSPortfolioManager()
        self.option_chain = EOSOptionChainManager()  # For real option prices

        # Position tracking
        self.positions: Dict[str, LivePosition] = {}
        self.trades_today: List[LiveTrade] = []
        self.traded_symbols_today: set = set()
        self.daily_pnl: float = 0.0
        self.starting_capital: float = initial_capital

        # Timing
        self.market_open = dt_time(9, 15)
        self.market_close = dt_time(15, 30)
        self.final_exit = dt_time(15, 18)
        self.pre_market_start = dt_time(9, 10)

        # Thread for main loop
        self._main_thread: Optional[threading.Thread] = None

        # Monitoring interval (seconds)
        self._tick_interval = 1.0

        # Live trading session ID for portfolio manager
        self._session_id: Optional[str] = None
        self._trade_counter: int = 0

        print(f"[EOSLiveRunner] Initialized with {len(self.symbols)} symbols")
        print(f"[EOSLiveRunner] Paper Trade Mode: {self.paper_trade}")

    # ===== LIFECYCLE METHODS =====

    def start(self):
        """Start the live runner."""
        if self.is_running:
            print("[EOSLiveRunner] Already running")
            return

        self.is_running = True
        self._stop_event.clear()
        self.state = RunnerState.INITIALIZING

        # Reset daily state
        self.risk_manager.reset_daily_state()
        self.positions.clear()
        self.trades_today.clear()
        self.traded_symbols_today.clear()
        self.daily_pnl = 0.0
        self._trade_counter = 0

        # Start a new live trading session in portfolio manager
        today = datetime.now().strftime('%Y-%m-%d')
        mode_prefix = "PAPER" if self.paper_trade else "LIVE"
        self._session_id = self.portfolio_manager.start_backtest(
            start_date=today,
            end_date=today,
            symbols=self.symbols,
            initial_capital=self.starting_capital,
            config={**self.config, "mode": mode_prefix}
        )
        print(f"[EOSLiveRunner] Session ID: {self._session_id}")

        # Initialize WebSocket feed
        self._init_feed()

        # Start main loop in thread
        self._main_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._main_thread.start()

        print(f"[EOSLiveRunner] Started at {datetime.now()}")

    def stop(self):
        """Stop the live runner."""
        print(f"[EOSLiveRunner] Stopping...")
        self.is_running = False
        self._stop_event.set()

        # Close all positions at market
        self._close_all_positions(ExitReason.TIME_EXIT)

        # Stop feed
        if self.feed:
            self.feed.stop()

        # End session and save daily snapshot to portfolio manager
        self._save_daily_snapshot()
        self._end_session()

        self.state = RunnerState.STOPPED
        print(f"[EOSLiveRunner] Stopped at {datetime.now()}")
        self._print_daily_summary()

    def _init_feed(self):
        """Initialize WebSocket feed with callbacks."""
        self.feed = DhanWebSocketFeed(
            on_tick=self._on_tick,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_error=self._on_error
        )

        # Subscribe to symbols (filter by our list)
        self.feed.subscribe_fno_stocks(self.symbols)

        # Pre-fetch previous close data
        print(f"[EOSLiveRunner] Pre-fetching previous close data...")
        self.feed.prefetch_prev_close_data()

        # Start WebSocket
        self.feed.start()

    # ===== CALLBACKS =====

    def _on_tick(self, tick: TickData):
        """Handle incoming tick data."""
        pass  # Processing done in main loop

    def _on_connect(self):
        """Handle WebSocket connection."""
        print(f"[EOSLiveRunner] WebSocket connected at {datetime.now()}")

    def _on_disconnect(self, reason: str):
        """Handle WebSocket disconnection."""
        print(f"[EOSLiveRunner] WebSocket disconnected: {reason}")

    def _on_error(self, error: str):
        """Handle WebSocket error."""
        print(f"[EOSLiveRunner] WebSocket error: {error}")

    # ===== MAIN LOOP =====

    def _main_loop(self):
        """Main trading loop - runs during market hours."""
        print(f"[EOSLiveRunner] Main loop started")

        while self.is_running and not self._stop_event.is_set():
            now = datetime.now()
            current_time = now.time()

            # Update state based on time
            if current_time < self.market_open:
                if self.state != RunnerState.PRE_MARKET:
                    self.state = RunnerState.PRE_MARKET
                    print(f"[{now}] PRE_MARKET - Waiting for 9:15 AM...")

            elif current_time >= self.market_open and current_time < self.market_close:
                if self.state != RunnerState.MARKET_OPEN:
                    self.state = RunnerState.MARKET_OPEN
                    print(f"[{now}] MARKET OPEN - Starting signal detection")

                # Main market logic
                self._check_entry_signals()
                self._monitor_positions()

                # Check for final exit time
                if current_time >= self.final_exit:
                    self._close_all_positions(ExitReason.TIME_EXIT)

            else:
                if self.state != RunnerState.MARKET_CLOSED:
                    self.state = RunnerState.MARKET_CLOSED
                    print(f"[{now}] MARKET CLOSED")
                    self._close_all_positions(ExitReason.TIME_EXIT)
                    self._print_daily_summary()

            # Sleep before next tick
            self._stop_event.wait(self._tick_interval)

        print(f"[EOSLiveRunner] Main loop ended")

    # ===== ENTRY SIGNAL DETECTION =====

    def _check_entry_signals(self):
        """Check for EOS entry signals from WebSocket data."""
        if not self.feed:
            return

        # Check if we can take more trades
        if len(self.trades_today) >= self.config["max_trades_per_day"]:
            return
        if self.daily_pnl <= -self.config["max_loss_per_day"]:
            return

        # Get stocks meeting entry conditions
        signals = self.feed.get_stocks_with_entry_signals(
            price_threshold=self.config["price_change_threshold"],
            oi_threshold=self.config["oi_change_threshold"]
        )

        for signal_data in signals:
            symbol = signal_data["symbol"]

            # Skip if already traded or in position
            if symbol in self.traded_symbols_today:
                continue
            if symbol in self.positions:
                continue

            # Skip first 5 minutes (9:15-9:20)
            now = datetime.now()
            if now.time() < dt_time(9, 20):
                continue

            # Generate entry signal
            self._process_entry_signal(signal_data)

    def _process_entry_signal(self, signal_data: Dict):
        """Process an entry signal and open position."""
        symbol = signal_data["symbol"]
        direction = signal_data["direction"]

        print(f"\n[SIGNAL] {symbol}: {direction}")
        print(f"  Price: ₹{signal_data['ltp']:.2f} (Change: {signal_data['price_change_pct']:+.2f}%)")
        print(f"  OI: {signal_data['oi']:,} (Change: {signal_data['oi_change_pct']:+.2f}%)")

        # Get REAL ATM option prices from Option Chain API
        spot_price = signal_data['ltp']
        atm = self.option_chain.get_atm_options(symbol, spot_price=spot_price, refresh=True)

        if not atm:
            print(f"  ❌ Could not fetch option chain for {symbol}")
            self._log_validation(symbol, direction, "REJECT", 1.0,
                                 "Could not fetch option chain", "SYSTEM",
                                 signal_data.get('price_change_pct', 0),
                                 signal_data.get('oi_change_pct', 0), 0)
            return

        # Get the correct option (PUT for bullish signal, CALL for bearish)
        # EOS is contrarian: Buy PUT when stock UP, Buy CALL when stock DOWN
        option_type = "PE" if direction == "PUT" else "CE"
        option_data = atm.put if option_type == "PE" else atm.call

        if not option_data or option_data.ltp <= 0:
            print(f"  ❌ No valid {option_type} option data for {symbol}")
            self._log_validation(symbol, direction, "REJECT", 1.0,
                                 f"No valid {option_type} option data", "SYSTEM",
                                 signal_data.get('price_change_pct', 0),
                                 signal_data.get('oi_change_pct', 0), 0)
            return

        entry_price = option_data.ltp
        atm_strike = atm.atm_strike

        # Get security ID for WebSocket subscription
        sec_ids = self.option_chain.get_atm_security_ids(symbol)
        option_security_id = sec_ids.get("put" if option_type == "PE" else "call", 0)

        print(f"  ATM Strike: ₹{atm_strike:.2f}, {option_type} LTP: ₹{entry_price:.2f}")
        if option_security_id:
            print(f"  Option Security ID: {option_security_id}")

        # Check risk manager
        can_trade, reason = self.risk_manager.can_take_trade(symbol, entry_price)
        if not can_trade:
            print(f"  ❌ Blocked by risk manager: {reason}")
            self._log_validation(symbol, direction, "REJECT", 1.0,
                                 f"Risk manager: {reason}", "RISK_MANAGER",
                                 signal_data.get('price_change_pct', 0),
                                 signal_data.get('oi_change_pct', 0), entry_price)
            return

        # Create position
        stock_info = FNO_STOCKS.get(symbol, {})
        lot_size = stock_info.get("lot_size", 1)

        position = LivePosition(
            symbol=symbol,
            option_type=direction,
            strike_price=atm_strike,  # Real ATM strike
            entry_price=entry_price,  # Real option LTP
            entry_time=datetime.now(),
            quantity=lot_size * self.config["lots_per_trade"],
            lot_size=lot_size,
            initial_stop_loss=0,  # Will be set by __post_init__
            current_stop_loss=0,
            highest_price=0,
            security_id=stock_info.get("equity_id", 0),
            option_security_id=option_security_id
        )

        # Subscribe to option WebSocket feed for real-time price updates
        if option_security_id and self.feed:
            opt_key = f"{symbol}_{option_type}"
            self.feed.subscribe_options({opt_key: option_security_id})

        self.positions[symbol] = position
        self.traded_symbols_today.add(symbol)

        # Register with risk manager
        self.risk_manager.register_entry(
            symbol=symbol,
            option_type=direction,
            entry_price=position.entry_price,
            quantity=position.quantity,
            lot_size=lot_size
        )

        print(f"  ✅ ENTRY: {direction} @ ₹{position.entry_price:.2f}")
        print(f"  Stop Loss: ₹{position.initial_stop_loss:.2f}")

        # Log APPROVE validation
        self._log_validation(symbol, direction, "APPROVE", 0.9,
                             f"Entry taken: {direction} @ ₹{position.entry_price:.2f}",
                             "STRATEGY",
                             signal_data.get('price_change_pct', 0),
                             signal_data.get('oi_change_pct', 0),
                             position.entry_price)

        # Callback
        if self.on_signal:
            self.on_signal(signal_data, position)

    def _log_validation(self, symbol: str, signal_type: str, result: str,
                        confidence: float, reason: str, tier_used: str,
                        price_change_pct: float = 0, oi_change_pct: float = 0,
                        entry_price: float = 0):
        """Log a validation decision to the database."""
        try:
            session_id = self._session_id or ""
            self.portfolio_manager.record_validation(
                backtest_id=session_id,
                symbol=symbol,
                signal_type=signal_type,
                result=result,
                confidence=confidence,
                reason=reason,
                tier_used=tier_used,
                price_change_pct=price_change_pct,
                oi_change_pct=oi_change_pct,
                entry_price=entry_price
            )
        except Exception as e:
            print(f"  [DB] Error logging validation: {e}")

    # ===== POSITION MONITORING =====

    def _monitor_positions(self):
        """Monitor open positions for exit conditions."""
        if not self.positions:
            return

        positions_to_close = []

        for symbol, position in self.positions.items():
            # Get REAL option price from WebSocket feed
            option_type = "PE" if position.option_type == "PUT" else "CE"
            opt_key = f"{symbol}_{option_type}"
            option_tick = self.feed.get_option_tick(symbol, option_type) if self.feed else None

            if option_tick and option_tick.ltp > 0:
                # Use REAL option LTP from WebSocket
                current_price = option_tick.ltp
            else:
                # Fallback: Refresh from Option Chain API (rate limited)
                # Only do this if we haven't checked recently
                atm = self.option_chain.get_cached_atm(symbol)
                if atm:
                    option_data = atm.put if option_type == "PE" else atm.call
                    if option_data and option_data.ltp > 0:
                        current_price = option_data.ltp
                    else:
                        # Last resort: use entry price (no update)
                        current_price = position.entry_price
                else:
                    current_price = position.entry_price

            # Update highest price for trailing SL
            if current_price > position.highest_price:
                position.highest_price = current_price

            # Check exit conditions
            exit_reason = self._check_exit_conditions(position, current_price)

            if exit_reason:
                positions_to_close.append((symbol, current_price, exit_reason))

        # Close positions outside the loop
        for symbol, exit_price, exit_reason in positions_to_close:
            self._close_position(symbol, exit_price, exit_reason)

    def _check_exit_conditions(self, position: LivePosition, current_price: float) -> Optional[ExitReason]:
        """Check all exit conditions for a position."""
        now = datetime.now()

        # 1. Initial Stop Loss (30%)
        if current_price <= position.initial_stop_loss:
            return ExitReason.INITIAL_STOP_LOSS

        # 2. Trailing Stop Loss
        self._update_trailing_sl(position, current_price)
        if current_price <= position.current_stop_loss and position.trailing_activated:
            return ExitReason.TRAILING_STOP_LOSS

        # 3. Time Exit (3:18 PM)
        if now.time() >= self.final_exit:
            return ExitReason.TIME_EXIT

        # 4. SMA Crossover (after 1 hour) - simplified for live
        time_in_trade = (now - position.entry_time).total_seconds() / 60
        if time_in_trade >= self.config["sma_active_after_minutes"]:
            # TODO: Implement SMA check with futures prices
            pass

        return None

    def _update_trailing_sl(self, position: LivePosition, current_price: float):
        """Update trailing stop loss using ₹10 trail logic."""
        trigger = self.config["trailing_sl_trigger"]
        trail_amount = self.config["trailing_sl_amount"]

        profit = current_price - position.entry_price

        # Activate trailing when profit >= ₹10
        if profit >= trigger:
            position.trailing_activated = True
            trail_steps = int(profit / trigger)
            new_sl = position.entry_price + (trail_steps - 1) * trail_amount

            if new_sl > position.current_stop_loss:
                position.current_stop_loss = new_sl

    def _close_position(self, symbol: str, exit_price: float, exit_reason: ExitReason):
        """Close a position and record the trade."""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]
        exit_time = datetime.now()
        pnl = (exit_price - position.entry_price) * position.quantity
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100
        hold_duration = (exit_time - position.entry_time).total_seconds() / 60

        # Create trade record
        trade = LiveTrade(
            symbol=symbol,
            option_type=position.option_type,
            strike_price=position.strike_price,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            quantity=position.quantity,
            lot_size=position.lot_size,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason.value,
            price_change_at_entry=0,  # TODO: Track from signal
            oi_change_at_entry=0
        )

        self.trades_today.append(trade)
        self.daily_pnl += pnl

        # Update risk manager
        self.risk_manager.register_exit(symbol, exit_price, exit_reason.value)

        # Record trade in database (Portfolio Manager)
        self._record_trade_to_db(trade, hold_duration)

        # Remove position
        del self.positions[symbol]

        pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"
        print(f"\n[EXIT] {symbol}: {exit_reason.value}")
        print(f"  Entry: Rs{position.entry_price:.2f} -> Exit: Rs{exit_price:.2f}")
        print(f"  PnL: {pnl_str} ({pnl_pct:+.2f}%)")

        # Callback
        if self.on_trade:
            self.on_trade(trade)

    def _close_all_positions(self, exit_reason: ExitReason):
        """Close all open positions."""
        if not self.positions:
            return

        print(f"\n[EOSLiveRunner] Closing all {len(self.positions)} positions...")

        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]

            # Get REAL option price from WebSocket or API
            option_type = "PE" if position.option_type == "PUT" else "CE"
            option_tick = self.feed.get_option_tick(symbol, option_type) if self.feed else None

            if option_tick and option_tick.ltp > 0:
                # Use real option LTP
                exit_price = option_tick.ltp
            else:
                # Try to get fresh price from Option Chain API
                atm = self.option_chain.get_atm_options(symbol, refresh=True)
                if atm:
                    option_data = atm.put if option_type == "PE" else atm.call
                    if option_data and option_data.ltp > 0:
                        exit_price = option_data.ltp
                    else:
                        # Fallback: use entry price (worst case)
                        exit_price = position.entry_price
                else:
                    exit_price = position.entry_price

            self._close_position(symbol, exit_price, exit_reason)


    # ===== DATABASE INTEGRATION =====

    def _record_trade_to_db(self, trade: LiveTrade, hold_duration: float):
        """Record a completed trade to the portfolio manager database."""
        if not self._session_id:
            return

        self._trade_counter += 1
        trade_id = f"{self._session_id}_T{self._trade_counter:03d}"

        trade_record = TradeRecord(
            trade_id=trade_id,
            symbol=trade.symbol,
            option_type=trade.option_type,
            strike_price=trade.strike_price,
            entry_date=trade.entry_time.strftime('%Y-%m-%d'),
            entry_time=trade.entry_time.strftime('%H:%M:%S'),
            entry_price=trade.entry_price,
            exit_date=trade.exit_time.strftime('%Y-%m-%d'),
            exit_time=trade.exit_time.strftime('%H:%M:%S'),
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            lot_size=trade.lot_size,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            exit_reason=trade.exit_reason,
            hold_duration_minutes=hold_duration,
            price_change_at_entry=trade.price_change_at_entry,
            oi_change_at_entry=trade.oi_change_at_entry,
            backtest_id=self._session_id
        )

        try:
            self.portfolio_manager.record_trade(trade_record)
            print(f"  [DB] Trade recorded: {trade_id}")
        except Exception as e:
            print(f"  [DB] Error recording trade: {e}")

    def _save_daily_snapshot(self):
        """Save daily snapshot to portfolio manager database."""
        if not self._session_id:
            return

        today = datetime.now().strftime('%Y-%m-%d')
        winners = [t for t in self.trades_today if t.pnl > 0]
        losers = [t for t in self.trades_today if t.pnl <= 0]

        ending_capital = self.starting_capital + self.daily_pnl
        daily_pnl_pct = (self.daily_pnl / self.starting_capital) * 100 if self.starting_capital > 0 else 0

        snapshot = DailySnapshot(
            date=today,
            starting_capital=self.starting_capital,
            ending_capital=ending_capital,
            daily_pnl=self.daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            trades_taken=len(self.trades_today),
            winning_trades=len(winners),
            losing_trades=len(losers),
            max_drawdown=0.0,  # TODO: Track intraday drawdown
            cumulative_pnl=self.daily_pnl,
            backtest_id=self._session_id
        )

        try:
            self.portfolio_manager.record_daily_snapshot(snapshot)
            print(f"[DB] Daily snapshot saved for {today}")
        except Exception as e:
            print(f"[DB] Error saving daily snapshot: {e}")

    def _end_session(self):
        """End the trading session and save final metrics."""
        if not self._session_id:
            return

        ending_capital = self.starting_capital + self.daily_pnl
        winners = [t for t in self.trades_today if t.pnl > 0]
        win_rate = (len(winners) / len(self.trades_today) * 100) if self.trades_today else 0

        metrics = {
            'total_pnl': self.daily_pnl,
            'total_trades': len(self.trades_today),
            'win_rate': win_rate,
            'sharpe_ratio': 0.0,  # Single day - not applicable
            'max_drawdown': 0.0   # TODO: Track intraday
        }

        try:
            self.portfolio_manager.end_backtest(
                backtest_id=self._session_id,
                final_capital=ending_capital,
                metrics=metrics
            )
            print(f"[DB] Session {self._session_id} ended and saved")
        except Exception as e:
            print(f"[DB] Error ending session: {e}")


    # ===== REPORTING =====

    def _print_daily_summary(self):
        """Print end-of-day summary."""
        print("\n" + "=" * 60)
        print("EOS LIVE RUNNER - DAILY SUMMARY")
        print("=" * 60)
        print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
        print(f"Mode: {'PAPER TRADE' if self.paper_trade else 'LIVE'}")

        print(f"\n--- TRADES ---")
        print(f"Total Trades: {len(self.trades_today)}")

        if self.trades_today:
            winners = [t for t in self.trades_today if t.pnl > 0]
            losers = [t for t in self.trades_today if t.pnl <= 0]

            print(f"Winning: {len(winners)}")
            print(f"Losing: {len(losers)}")
            print(f"Win Rate: {len(winners)/len(self.trades_today)*100:.1f}%")

            print(f"\n--- P&L ---")
            pnl_str = f"+₹{self.daily_pnl:,.2f}" if self.daily_pnl >= 0 else f"-₹{abs(self.daily_pnl):,.2f}"
            print(f"Daily P&L: {pnl_str}")

            if winners:
                avg_win = sum(t.pnl for t in winners) / len(winners)
                print(f"Avg Win: +₹{avg_win:,.2f}")
            if losers:
                avg_loss = sum(t.pnl for t in losers) / len(losers)
                print(f"Avg Loss: -₹{abs(avg_loss):,.2f}")

            print(f"\n--- TRADE DETAILS ---")
            for i, trade in enumerate(self.trades_today, 1):
                pnl_str = f"+₹{trade.pnl:.2f}" if trade.pnl >= 0 else f"-₹{abs(trade.pnl):.2f}"
                print(f"{i}. {trade.symbol} {trade.option_type}: {pnl_str} ({trade.exit_reason})")
        else:
            print("No trades taken today")

        print("\n" + "=" * 60)

        # Print risk manager summary
        self.risk_manager.print_risk_status()

    def get_status(self) -> Dict:
        """Get current runner status."""
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "paper_trade": self.paper_trade,
            "symbols_monitored": len(self.symbols),
            "open_positions": len(self.positions),
            "trades_today": len(self.trades_today),
            "daily_pnl": self.daily_pnl,
            "positions": {
                symbol: {
                    "option_type": pos.option_type,
                    "entry_price": pos.entry_price,
                    "current_sl": pos.current_stop_loss,
                    "highest_price": pos.highest_price
                }
                for symbol, pos in self.positions.items()
            },
            "feed_connected": self.feed.is_connected if self.feed else False
        }

    def print_status(self):
        """Print current status."""
        status = self.get_status()

        print("\n" + "=" * 60)
        print("EOS LIVE RUNNER STATUS")
        print("=" * 60)
        print(f"State: {status['state']}")
        print(f"Running: {status['is_running']}")
        print(f"Feed Connected: {status['feed_connected']}")
        print(f"Symbols: {status['symbols_monitored']}")
        print(f"Open Positions: {status['open_positions']}")
        print(f"Trades Today: {status['trades_today']}")

        pnl_str = f"+₹{status['daily_pnl']:,.2f}" if status['daily_pnl'] >= 0 else f"-₹{abs(status['daily_pnl']):,.2f}"
        print(f"Daily P&L: {pnl_str}")

        if status['positions']:
            print(f"\n--- Open Positions ---")
            for symbol, pos in status['positions'].items():
                print(f"  {symbol}: {pos['option_type']} @ ₹{pos['entry_price']:.2f}, SL=₹{pos['current_sl']:.2f}")

        print("=" * 60)