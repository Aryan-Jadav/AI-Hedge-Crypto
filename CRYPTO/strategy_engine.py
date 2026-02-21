"""
CRYPTO Strategy Engine
Mirrors EOS/eos_strategy_engine.py structure exactly.

CRYPTO-EOS Contrarian Strategy:
  - Price DROPS >4%  → LONG PERP  (expect price to recover)
  - Price SPIKES >4% → SHORT PERP (expect price to retrace)
  Second confirmation: funding_rate extreme OR volume spike

NEVER CHANGE:
  - price_change_threshold: 4.0%
  - funding_rate_threshold: 0.0001
  - initial_stop_loss_pct: 3.0%
  - Signal logic (DOWN→LONG, UP→SHORT)
  - Exit rules
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from enum import Enum
from typing import Dict, List, Optional, Tuple

import pytz

from .config import CRYPTO_CONFIG, CRYPTO_PAIRS
from .data_fetcher import CryptoDataFetcher

IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# ENUMS
# =============================================================================

class CryptoSignalType(Enum):
    NO_SIGNAL  = "NO_SIGNAL"
    LONG_PERP  = "LONG_PERP"    # Price dropped >4% → BUY (expect reversal up)
    SHORT_PERP = "SHORT_PERP"   # Price spiked >4% → SELL (expect reversal down)


class CryptoExitReason(Enum):
    INITIAL_STOP_LOSS  = "INITIAL_STOP_LOSS"
    TRAILING_STOP_LOSS = "TRAILING_STOP_LOSS"
    SMA_CROSSOVER      = "SMA_CROSSOVER"
    TIME_EXIT          = "TIME_EXIT"
    MAX_LOSS_DAY       = "MAX_LOSS_DAY"
    FUNDING_FLIP       = "FUNDING_FLIP"   # Crypto-specific: funding rate flips adversely


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class CryptoPosition:
    """
    Open perpetual futures position.
    Mirrors EOS Position dataclass exactly (with crypto-specific fields).
    """
    symbol: str
    side: str                          # "LONG" or "SHORT"
    entry_price: float
    entry_time: datetime
    quantity: float                    # in base asset (e.g., 0.001 BTC)
    min_qty: float
    initial_stop_loss: float = field(default=0.0)
    current_stop_loss: float = field(default=0.0)
    highest_favorable_price: float = field(default=0.0)  # highest for LONG, lowest for SHORT
    trailing_activated: bool = False
    leverage: int = 5

    def __post_init__(self):
        sl_pct = CRYPTO_CONFIG["initial_stop_loss_pct"] / 100.0
        if self.initial_stop_loss == 0.0:
            if self.side == "LONG":
                self.initial_stop_loss = self.entry_price * (1.0 - sl_pct)
            else:  # SHORT
                self.initial_stop_loss = self.entry_price * (1.0 + sl_pct)
        if self.current_stop_loss == 0.0:
            self.current_stop_loss = self.initial_stop_loss
        if self.highest_favorable_price == 0.0:
            self.highest_favorable_price = self.entry_price


@dataclass
class CryptoSignal:
    """
    Entry signal. Mirrors EOS Signal dataclass.
    """
    symbol: str
    signal_type: CryptoSignalType
    timestamp: datetime
    current_price: float
    prev_price: float                  # Reference price (24h ago via ticker)
    price_change_pct: float
    funding_rate: float
    funding_rate_extreme: bool
    current_volume: float
    avg_volume: float
    volume_spike: bool
    sma_fast: Optional[float]
    sma_slow: Optional[float]
    confidence: str                    # "HIGH", "MEDIUM", "LOW"
    notes: str = ""

    @property
    def side(self) -> str:
        return "LONG" if self.signal_type == CryptoSignalType.LONG_PERP else "SHORT"


# =============================================================================
# STRATEGY ENGINE
# =============================================================================

class CryptoStrategyEngine:
    """
    CRYPTO Strategy Engine.
    Mirrors EOSStrategyEngine class structure exactly.

    Entry logic:
      1. abs(price_change_pct) > 4.0% (from 24h reference)
      2. funding_rate_extreme (abs > 0.01%) OR volume_spike (>1.5x avg)
      3. Within Asia session window (05:30-14:00 IST)

    Exit logic:
      1. Initial SL: 3% against entry
      2. Trailing SL: 1% trail per 1% favorable move
      3. SMA Crossover: 8-SMA vs 20-SMA (after 60 min)
      4. Time exit: 13:50 IST
      5. Funding flip: funding reverses adversely (crypto-specific)
    """

    def __init__(self, data_fetcher: CryptoDataFetcher = None) -> None:
        self.data_fetcher = data_fetcher or CryptoDataFetcher()
        self.config = CRYPTO_CONFIG

        # Session state (reset daily)
        self.positions: Dict[str, CryptoPosition] = {}
        self.daily_pnl_usdt: float = 0.0
        self.trades_today: int = 0
        self.traded_symbols_today: set = set()

        # Parse session times
        self._session_start = dt_time(5, 30)
        self._session_end   = dt_time(14, 0)
        self._final_exit    = dt_time(13, 50)

    # -------------------------------------------------------------------------
    # State management
    # -------------------------------------------------------------------------

    def reset_daily_state(self) -> None:
        """Reset all daily counters. Called at start of each session."""
        self.daily_pnl_usdt = 0.0
        self.trades_today = 0
        self.traded_symbols_today = set()
        self.positions = {}
        print("[CryptoEngine] Daily state reset.")

    # -------------------------------------------------------------------------
    # Session / time checks
    # -------------------------------------------------------------------------

    def is_trading_session(self, current_time: datetime = None) -> bool:
        """
        Check if within Asia session (05:30-14:00 IST).
        Mirrors EOSStrategyEngine.is_market_hours().
        """
        if current_time is None:
            current_time = datetime.now(IST)
        t = current_time.time() if hasattr(current_time, "time") else current_time
        return self._session_start <= t <= self._session_end

    def can_take_new_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if a new trade can be taken. Mirrors EOSStrategyEngine.can_take_new_trade().
        """
        if self.daily_pnl_usdt <= -self.config["max_loss_per_day_usdt"]:
            return False, f"Daily loss limit hit (${abs(self.daily_pnl_usdt):.2f})"
        if self.trades_today >= self.config["max_trades_per_day"]:
            return False, f"Max trades per day reached ({self.config['max_trades_per_day']})"
        if symbol in self.positions:
            return False, f"Already in position: {symbol}"
        if not self.config.get("allow_reentry", False) and symbol in self.traded_symbols_today:
            return False, f"Already traded {symbol} today (re-entry disabled)"
        return True, "OK"

    # -------------------------------------------------------------------------
    # Entry logic
    # -------------------------------------------------------------------------

    def check_entry_conditions(
        self,
        symbol: str,
        current_price: float,
        prev_price: float,
        funding_rate: float,
        current_volume: float,
        avg_volume: float,
        perp_prices: List[float],   # Recent close prices for SMA
    ) -> Tuple[bool, CryptoSignalType, Dict]:
        """
        Check if CRYPTO-EOS entry conditions are met.
        Mirrors EOSStrategyEngine.check_entry_conditions().

        Returns:
            (entry_valid: bool, signal_type: CryptoSignalType, details: dict)
        """
        details: Dict = {}

        price_change_pct = CryptoDataFetcher.calculate_price_change_pct(current_price, prev_price)
        funding_extreme = CryptoDataFetcher.calculate_funding_rate_extreme(funding_rate)
        volume_spike = CryptoDataFetcher.calculate_volume_spike(current_volume, avg_volume)

        sma_fast = CryptoDataFetcher.calculate_sma(perp_prices, self.config["sma_fast"])
        sma_slow = CryptoDataFetcher.calculate_sma(perp_prices, self.config["sma_slow"])

        details = {
            "price_change_pct":     round(price_change_pct, 4),
            "funding_rate":         funding_rate,
            "funding_extreme":      funding_extreme,
            "volume_spike":         volume_spike,
            "current_volume":       current_volume,
            "avg_volume":           avg_volume,
            "sma_fast":             sma_fast,
            "sma_slow":             sma_slow,
        }

        price_threshold = self.config["price_change_threshold"]

        # Condition 1: Price must have moved > threshold
        if abs(price_change_pct) < price_threshold:
            details["reject_reason"] = f"Price change {price_change_pct:.2f}% < {price_threshold}%"
            return False, CryptoSignalType.NO_SIGNAL, details

        # Condition 2: At least one secondary confirmation
        if not funding_extreme and not volume_spike:
            details["reject_reason"] = "No secondary confirmation (no funding extreme, no volume spike)"
            return False, CryptoSignalType.NO_SIGNAL, details

        # Determine direction: contrarian
        if price_change_pct < -price_threshold:
            # Price dropped hard → BUY (expect bounce)
            signal_type = CryptoSignalType.LONG_PERP
        else:
            # Price spiked hard → SELL (expect retrace)
            signal_type = CryptoSignalType.SHORT_PERP

        return True, signal_type, details

    def generate_entry_signal(self, symbol: str) -> Optional[CryptoSignal]:
        """
        Generate a CryptoSignal for a symbol if conditions are met.
        Mirrors EOSStrategyEngine.generate_entry_signal().
        """
        if not self.is_trading_session():
            return None

        can_trade, reason = self.can_take_new_trade(symbol)
        if not can_trade:
            return None

        # Fetch all screening data
        data = self.data_fetcher.get_pair_data_for_screening(symbol)
        if data.get("error"):
            print(f"[CryptoEngine] {symbol}: data fetch error - {data['error']}")
            return None

        current_price = data["current_price"]
        prev_price = current_price / (1 + data["price_change_pct"] / 100.0) \
            if data["price_change_pct"] != 0 else current_price
        funding_rate = data["funding_rate"]
        current_volume = data["volume_24h"]
        avg_volume = data["avg_volume"]
        kline_closes = data["kline_closes"]

        entry_valid, signal_type, details = self.check_entry_conditions(
            symbol, current_price, prev_price,
            funding_rate, current_volume, avg_volume,
            kline_closes,
        )

        if not entry_valid:
            return None

        # Determine confidence
        confirmations = sum([details["funding_extreme"], details["volume_spike"]])
        if abs(details["price_change_pct"]) >= 6.0 and confirmations >= 2:
            confidence = "HIGH"
        elif abs(details["price_change_pct"]) >= 5.0 or confirmations >= 1:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        notes_parts = []
        if details["funding_extreme"]:
            notes_parts.append(f"funding extreme ({funding_rate*100:.4f}%)")
        if details["volume_spike"]:
            notes_parts.append(f"volume spike ({current_volume/avg_volume:.1f}x)")

        return CryptoSignal(
            symbol=symbol,
            signal_type=signal_type,
            timestamp=datetime.now(IST),
            current_price=current_price,
            prev_price=prev_price,
            price_change_pct=details["price_change_pct"],
            funding_rate=funding_rate,
            funding_rate_extreme=details["funding_extreme"],
            current_volume=current_volume,
            avg_volume=avg_volume,
            volume_spike=details["volume_spike"],
            sma_fast=details["sma_fast"],
            sma_slow=details["sma_slow"],
            confidence=confidence,
            notes=", ".join(notes_parts),
        )

    # -------------------------------------------------------------------------
    # Position management
    # -------------------------------------------------------------------------

    def open_position(self, signal: CryptoSignal, entry_price: float) -> CryptoPosition:
        """
        Register a new position. Mirrors EOSStrategyEngine.open_position().
        """
        pair_info = CRYPTO_PAIRS.get(signal.symbol, {})
        min_qty = pair_info.get("min_qty", 1.0)
        quantity = min_qty * self.config["contracts_per_trade"]

        position = CryptoPosition(
            symbol=signal.symbol,
            side=signal.side,
            entry_price=entry_price,
            entry_time=datetime.now(IST),
            quantity=quantity,
            min_qty=min_qty,
            leverage=self.config["leverage"],
        )

        self.positions[signal.symbol] = position
        self.trades_today += 1
        self.traded_symbols_today.add(signal.symbol)

        print(f"[CryptoEngine] Opened {signal.side} on {signal.symbol} @ ${entry_price:.2f} | "
              f"qty={quantity} | SL=${position.initial_stop_loss:.2f}")
        return position

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: CryptoExitReason
    ) -> Dict:
        """
        Close position and calculate PnL. Mirrors EOSStrategyEngine.close_position().

        LONG:  pnl = (exit - entry) * qty
        SHORT: pnl = (entry - exit) * qty
        """
        position = self.positions.get(symbol)
        if not position:
            return {"error": f"No position for {symbol}"}

        if position.side == "LONG":
            pnl_usdt = (exit_price - position.entry_price) * position.quantity
        else:  # SHORT
            pnl_usdt = (position.entry_price - exit_price) * position.quantity

        pnl_pct = (pnl_usdt / (position.entry_price * position.quantity)) * 100.0
        hold_duration = (datetime.now(IST) - position.entry_time).total_seconds() / 60.0

        self.daily_pnl_usdt += pnl_usdt
        del self.positions[symbol]

        summary = {
            "symbol":           symbol,
            "side":             position.side,
            "entry_price":      position.entry_price,
            "exit_price":       exit_price,
            "entry_time":       position.entry_time.isoformat(),
            "exit_time":        datetime.now(IST).isoformat(),
            "quantity":         position.quantity,
            "pnl_usdt":         round(pnl_usdt, 4),
            "pnl_pct":          round(pnl_pct, 4),
            "exit_reason":      exit_reason.value,
            "hold_duration_min": round(hold_duration, 2),
        }

        pnl_color = "\033[92m" if pnl_usdt >= 0 else "\033[91m"
        print(f"[CryptoEngine] Closed {symbol} | {exit_reason.value} | "
              f"{pnl_color}PnL: ${pnl_usdt:+.2f}\033[0m")
        return summary

    # -------------------------------------------------------------------------
    # Exit logic
    # -------------------------------------------------------------------------

    def update_trailing_stop_loss(
        self,
        position: CryptoPosition,
        current_price: float,
    ) -> float:
        """
        Update trailing SL. Mirrors EOSStrategyEngine.update_trailing_stop_loss().

        Trail by 1% for every 1% favorable move (from config).
        For LONG: trail below highest favorable price.
        For SHORT: trail above lowest favorable price.
        """
        trail_pct = self.config["trailing_sl_amount_pct"] / 100.0

        if position.side == "LONG":
            if current_price > position.highest_favorable_price:
                position.highest_favorable_price = current_price
                position.current_stop_loss = position.highest_favorable_price * (1.0 - trail_pct)
                position.trailing_activated = True
        else:  # SHORT
            # "Highest favorable" is LOWEST price for a short
            if current_price < position.highest_favorable_price or not position.trailing_activated:
                if not position.trailing_activated:
                    position.highest_favorable_price = current_price
                elif current_price < position.highest_favorable_price:
                    position.highest_favorable_price = current_price
                    position.current_stop_loss = position.highest_favorable_price * (1.0 + trail_pct)
                    position.trailing_activated = True

        return position.current_stop_loss

    def check_exit_conditions(
        self,
        position: CryptoPosition,
        current_price: float,
        perp_prices: List[float],
        current_funding_rate: float,
        current_time: datetime = None,
    ) -> Tuple[bool, Optional[CryptoExitReason]]:
        """
        Check all exit conditions. Mirrors EOSStrategyEngine.check_exit_conditions().
        Checks in priority order.
        """
        if current_time is None:
            current_time = datetime.now(IST)

        t = current_time.time() if hasattr(current_time, "time") else current_time

        # 1. Time exit (highest priority - must exit before session ends)
        if t >= self._final_exit:
            return True, CryptoExitReason.TIME_EXIT

        # 2. Initial SL
        if position.side == "LONG":
            if current_price <= position.initial_stop_loss:
                return True, CryptoExitReason.INITIAL_STOP_LOSS
        else:  # SHORT
            if current_price >= position.initial_stop_loss:
                return True, CryptoExitReason.INITIAL_STOP_LOSS

        # 3. Trailing SL (update first, then check)
        self.update_trailing_stop_loss(position, current_price)
        if position.trailing_activated:
            if position.side == "LONG" and current_price <= position.current_stop_loss:
                return True, CryptoExitReason.TRAILING_STOP_LOSS
            elif position.side == "SHORT" and current_price >= position.current_stop_loss:
                return True, CryptoExitReason.TRAILING_STOP_LOSS

        # 4. SMA crossover (after 60 minutes)
        hold_minutes = (current_time - position.entry_time).total_seconds() / 60.0
        if hold_minutes >= self.config["sma_active_after_minutes"]:
            sma_fast = CryptoDataFetcher.calculate_sma(perp_prices, self.config["sma_fast"])
            sma_slow = CryptoDataFetcher.calculate_sma(perp_prices, self.config["sma_slow"])
            if sma_fast and sma_slow:
                if position.side == "LONG" and sma_fast < sma_slow:
                    return True, CryptoExitReason.SMA_CROSSOVER
                elif position.side == "SHORT" and sma_fast > sma_slow:
                    return True, CryptoExitReason.SMA_CROSSOVER

        # 5. Funding rate flip (crypto-specific exit)
        # If we're LONG but funding becomes heavily positive → longs over-leveraged, exit
        # If we're SHORT but funding becomes heavily negative → shorts over-leveraged, exit
        funding_threshold = self.config["funding_rate_threshold"] * 2  # 2x threshold for exit
        if position.side == "LONG" and current_funding_rate > funding_threshold:
            # Funding is very positive → long sentiment too crowded → exit long
            return True, CryptoExitReason.FUNDING_FLIP
        elif position.side == "SHORT" and current_funding_rate < -funding_threshold:
            # Funding very negative → short sentiment too crowded → exit short
            return True, CryptoExitReason.FUNDING_FLIP

        return False, None

    # -------------------------------------------------------------------------
    # Scanner
    # -------------------------------------------------------------------------

    def scan_all_pairs(self) -> List[CryptoSignal]:
        """
        Scan all pairs in CRYPTO_PAIRS for entry signals.
        Mirrors EOSStrategyEngine.scan_all_stocks().
        """
        signals: List[CryptoSignal] = []

        if not self.is_trading_session():
            return signals

        for symbol in CRYPTO_PAIRS:
            signal = self.generate_entry_signal(symbol)
            if signal:
                signals.append(signal)

        # Sort by confidence then price change magnitude
        confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        signals.sort(key=lambda s: (
            confidence_order.get(s.confidence, 3),
            -abs(s.price_change_pct)
        ))

        return signals

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_portfolio_status(self) -> Dict:
        """Mirrors EOSStrategyEngine.get_portfolio_status()."""
        positions_detail = {}
        for sym, pos in self.positions.items():
            positions_detail[sym] = {
                "side":             pos.side,
                "entry_price":      pos.entry_price,
                "quantity":         pos.quantity,
                "initial_sl":       pos.initial_stop_loss,
                "current_sl":       pos.current_stop_loss,
                "trailing_active":  pos.trailing_activated,
            }

        return {
            "open_positions":       len(self.positions),
            "positions":            positions_detail,
            "daily_pnl_usdt":       round(self.daily_pnl_usdt, 2),
            "trades_today":         self.trades_today,
            "traded_symbols_today": list(self.traded_symbols_today),
            "session_active":       self.is_trading_session(),
        }
