"""
CRYPTO Risk Manager
Mirrors EOS/eos_risk_manager.py structure exactly.

OBSERVATION ONLY: Does NOT alter strategy signals or entry/exit rules.
Manages: USDT position sizing, daily loss limits, margin calculation.

NEVER CHANGE:
  - max_loss_per_day_usdt: $500
  - max_trades_per_day: 5
  - initial_stop_loss_pct: 3%
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import pytz

from .config import CRYPTO_CONFIG, CRYPTO_PAIRS

IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class CryptoRiskState:
    """
    Current risk state snapshot. Mirrors EOS RiskState exactly.
    All monetary values in USDT.
    """
    date: str
    initial_capital_usdt: float
    current_capital_usdt: float
    daily_pnl_usdt: float = 0.0
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    total_exposure_usdt: float = 0.0
    margin_used_usdt: float = 0.0
    available_margin_usdt: float = 0.0
    daily_loss_limit_hit: bool = False
    max_trades_hit: bool = False

    def __post_init__(self):
        self.available_margin_usdt = self.current_capital_usdt - self.margin_used_usdt

    def to_dict(self) -> Dict:
        return {
            "date":                  self.date,
            "initial_capital_usdt":  round(self.initial_capital_usdt, 2),
            "current_capital_usdt":  round(self.current_capital_usdt, 2),
            "daily_pnl_usdt":        round(self.daily_pnl_usdt, 2),
            "realized_pnl_usdt":     round(self.realized_pnl_usdt, 2),
            "unrealized_pnl_usdt":   round(self.unrealized_pnl_usdt, 2),
            "trades_today":          self.trades_today,
            "open_positions":        self.open_positions,
            "total_exposure_usdt":   round(self.total_exposure_usdt, 2),
            "margin_used_usdt":      round(self.margin_used_usdt, 2),
            "available_margin_usdt": round(self.available_margin_usdt, 2),
            "daily_loss_limit_hit":  self.daily_loss_limit_hit,
            "max_trades_hit":        self.max_trades_hit,
        }


@dataclass
class CryptoPositionRisk:
    """
    Risk metrics for a single open position. Mirrors EOS PositionRisk.
    All monetary values in USDT.
    """
    symbol: str
    side: str                     # "LONG" or "SHORT"
    entry_price: float
    current_price: float
    quantity: float               # base asset units
    min_qty: float
    exposure_usdt: float          # entry_price * quantity (notional value)
    margin_required_usdt: float   # exposure / leverage
    unrealized_pnl_usdt: float
    max_loss_usdt: float          # Based on 3% initial SL
    risk_pct: float               # max_loss / capital * 100


# =============================================================================
# RISK MANAGER
# =============================================================================

class CryptoRiskManager:
    """
    CRYPTO Risk Manager. Mirrors EOSRiskManager exactly.

    Design principle (same as EOS):
    - OBSERVATION ONLY: never modifies entry/exit strategy rules
    - Blocks new trades if daily limits breached
    - Tracks position risk metrics in real time
    - Provides margin availability check
    """

    def __init__(self, initial_capital_usdt: float = None) -> None:
        self.config = CRYPTO_CONFIG
        self.initial_capital_usdt = initial_capital_usdt or self.config["total_capital_usdt"]
        self.current_capital_usdt = self.initial_capital_usdt
        self.max_loss_per_day_usdt = self.config["max_loss_per_day_usdt"]
        self.max_trades_per_day = self.config["max_trades_per_day"]
        self.leverage = self.config["leverage"]
        self.max_exposure_ratio = 0.8  # Max 80% of capital in exposure

        self.risk_state = CryptoRiskState(
            date=datetime.now(IST).strftime("%Y-%m-%d"),
            initial_capital_usdt=self.initial_capital_usdt,
            current_capital_usdt=self.current_capital_usdt,
        )
        self.position_risks: Dict[str, CryptoPositionRisk] = {}
        self.trade_log: List[Dict] = []

    # -------------------------------------------------------------------------
    # State management
    # -------------------------------------------------------------------------

    def reset_daily_state(self) -> None:
        """Reset for new trading session. Mirrors EOSRiskManager.reset_daily_state()."""
        self.risk_state = CryptoRiskState(
            date=datetime.now(IST).strftime("%Y-%m-%d"),
            initial_capital_usdt=self.current_capital_usdt,
            current_capital_usdt=self.current_capital_usdt,
        )
        self.position_risks = {}
        print(f"[CryptoRisk] Daily state reset. Capital: ${self.current_capital_usdt:.2f}")

    # -------------------------------------------------------------------------
    # Position sizing (mirrors EOS — always returns 1 lot)
    # -------------------------------------------------------------------------

    def get_position_size(self, symbol: str) -> Tuple[float, str]:
        """
        Returns (quantity_in_base_asset, reason).
        Always 1 lot (1 * min_qty) per strategy rules.
        Mirrors EOSRiskManager.get_position_size().
        """
        pair_info = CRYPTO_PAIRS.get(symbol, {})
        min_qty = pair_info.get("min_qty", 1.0)
        quantity = min_qty * self.config["contracts_per_trade"]
        return quantity, "Fixed 1 lot per CRYPTO-EOS strategy rules"

    # -------------------------------------------------------------------------
    # Margin & loss calculations
    # -------------------------------------------------------------------------

    def calculate_margin_required(
        self,
        symbol: str,
        entry_price: float,
        quantity: float
    ) -> float:
        """
        margin = (entry_price * quantity) / leverage
        Mirrors EOSRiskManager.calculate_margin_required().
        """
        notional = entry_price * quantity
        return notional / self.leverage

    def calculate_max_loss(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        side: str
    ) -> float:
        """
        Max loss = entry_price * quantity * (sl_pct / 100)
        Same formula for LONG and SHORT (SL is % from entry both ways).
        Mirrors EOSRiskManager.calculate_max_loss().
        """
        sl_pct = self.config["initial_stop_loss_pct"] / 100.0
        return entry_price * quantity * sl_pct

    # -------------------------------------------------------------------------
    # Trade gating
    # -------------------------------------------------------------------------

    def can_take_trade(
        self,
        symbol: str,
        entry_price: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Check if a new trade can be taken.
        Mirrors EOSRiskManager.can_take_trade() exactly.
        Returns (allowed: bool, reason: str).
        Does NOT modify trade or state — pure check only.
        """
        # 1. Daily loss limit
        if self.risk_state.daily_pnl_usdt <= -self.max_loss_per_day_usdt:
            self.risk_state.daily_loss_limit_hit = True
            return False, f"Daily loss limit hit: ${abs(self.risk_state.daily_pnl_usdt):.2f} / ${self.max_loss_per_day_usdt:.2f}"

        # 2. Max trades per day
        if self.risk_state.trades_today >= self.max_trades_per_day:
            self.risk_state.max_trades_hit = True
            return False, f"Max trades per day reached: {self.risk_state.trades_today}/{self.max_trades_per_day}"

        # 3. Available margin check
        if entry_price > 0:
            qty, _ = self.get_position_size(symbol)
            margin_needed = self.calculate_margin_required(symbol, entry_price, qty)
            if margin_needed > self.risk_state.available_margin_usdt:
                return False, (
                    f"Insufficient margin: need ${margin_needed:.2f}, "
                    f"have ${self.risk_state.available_margin_usdt:.2f}"
                )

            # 4. Exposure limit
            max_exposure = self.current_capital_usdt * self.max_exposure_ratio
            new_exposure = entry_price * qty
            if self.risk_state.total_exposure_usdt + new_exposure > max_exposure:
                return False, (
                    f"Exposure limit: current ${self.risk_state.total_exposure_usdt:.2f} + "
                    f"${new_exposure:.2f} > max ${max_exposure:.2f}"
                )

        return True, "OK"

    # -------------------------------------------------------------------------
    # Entry / exit registration
    # -------------------------------------------------------------------------

    def register_entry(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        min_qty: float,
    ) -> None:
        """
        Register a new entry. Updates risk state.
        Mirrors EOSRiskManager.register_entry().
        """
        margin = self.calculate_margin_required(symbol, entry_price, quantity)
        max_loss = self.calculate_max_loss(symbol, entry_price, quantity, side)
        risk_pct = (max_loss / self.current_capital_usdt) * 100.0 if self.current_capital_usdt > 0 else 0

        pos_risk = CryptoPositionRisk(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            min_qty=min_qty,
            exposure_usdt=entry_price * quantity,
            margin_required_usdt=margin,
            unrealized_pnl_usdt=0.0,
            max_loss_usdt=max_loss,
            risk_pct=round(risk_pct, 2),
        )
        self.position_risks[symbol] = pos_risk

        # Update risk state
        self.risk_state.trades_today += 1
        self.risk_state.open_positions += 1
        self.risk_state.total_exposure_usdt += pos_risk.exposure_usdt
        self.risk_state.margin_used_usdt += margin
        self.risk_state.available_margin_usdt = (
            self.current_capital_usdt - self.risk_state.margin_used_usdt
        )

        self.trade_log.append({
            "event": "ENTRY",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "quantity": quantity,
            "margin": round(margin, 2),
            "max_loss": round(max_loss, 2),
            "timestamp": datetime.now(IST).isoformat(),
        })

        print(f"[CryptoRisk] Entry registered: {side} {symbol} @ ${entry_price:.2f} | "
              f"qty={quantity} | margin=${margin:.2f} | max_loss=${max_loss:.2f}")

    def register_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        side: str,
    ) -> None:
        """
        Register exit. Updates PnL and releases margin.
        Mirrors EOSRiskManager.register_exit().
        """
        pos = self.position_risks.get(symbol)
        if not pos:
            print(f"[CryptoRisk] Warning: no position risk entry for {symbol}")
            return

        if side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        self.risk_state.realized_pnl_usdt += pnl
        self.risk_state.daily_pnl_usdt += pnl
        self.risk_state.open_positions = max(0, self.risk_state.open_positions - 1)
        self.risk_state.total_exposure_usdt = max(0, self.risk_state.total_exposure_usdt - pos.exposure_usdt)
        self.risk_state.margin_used_usdt = max(0, self.risk_state.margin_used_usdt - pos.margin_required_usdt)
        self.risk_state.available_margin_usdt = (
            self.current_capital_usdt - self.risk_state.margin_used_usdt
        )
        self.current_capital_usdt += pnl
        self.risk_state.current_capital_usdt = self.current_capital_usdt

        self.trade_log.append({
            "event": "EXIT",
            "symbol": symbol,
            "side": side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "pnl_usdt": round(pnl, 4),
            "exit_reason": exit_reason,
            "timestamp": datetime.now(IST).isoformat(),
        })

        del self.position_risks[symbol]

        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        print(f"[CryptoRisk] Exit registered: {symbol} | {exit_reason} | PnL: {pnl_str} | "
              f"Capital: ${self.current_capital_usdt:.2f}")

    def update_position_price(self, symbol: str, current_price: float) -> None:
        """Update current price and unrealized PnL for a position."""
        pos = self.position_risks.get(symbol)
        if not pos:
            return
        pos.current_price = current_price
        if pos.side == "LONG":
            pos.unrealized_pnl_usdt = (current_price - pos.entry_price) * pos.quantity
        else:
            pos.unrealized_pnl_usdt = (pos.entry_price - current_price) * pos.quantity

        # Update aggregate unrealized PnL
        self.risk_state.unrealized_pnl_usdt = sum(
            p.unrealized_pnl_usdt for p in self.position_risks.values()
        )

    # -------------------------------------------------------------------------
    # Reporting
    # -------------------------------------------------------------------------

    def get_risk_summary(self) -> Dict:
        """Full risk summary. Mirrors EOSRiskManager.get_risk_summary()."""
        return {
            "risk_state": self.risk_state.to_dict(),
            "positions": {
                sym: {
                    "side":              p.side,
                    "entry_price":       p.entry_price,
                    "current_price":     p.current_price,
                    "quantity":          p.quantity,
                    "exposure_usdt":     round(p.exposure_usdt, 2),
                    "margin_usdt":       round(p.margin_required_usdt, 2),
                    "unrealized_pnl":    round(p.unrealized_pnl_usdt, 2),
                    "max_loss_usdt":     round(p.max_loss_usdt, 2),
                    "risk_pct":          p.risk_pct,
                }
                for sym, p in self.position_risks.items()
            },
        }

    def print_risk_status(self) -> None:
        """Print formatted risk status. Mirrors EOSRiskManager.print_risk_status()."""
        s = self.risk_state
        print(f"\n{'='*55}")
        print(f"  CRYPTO RISK STATUS | {s.date}")
        print(f"{'='*55}")
        print(f"  Capital:     ${s.current_capital_usdt:>10.2f} USDT")
        print(f"  Daily PnL:   ${s.daily_pnl_usdt:>+10.2f} USDT")
        print(f"  Realized:    ${s.realized_pnl_usdt:>+10.2f} USDT")
        print(f"  Unrealized:  ${s.unrealized_pnl_usdt:>+10.2f} USDT")
        print(f"  Margin Used: ${s.margin_used_usdt:>10.2f} USDT")
        print(f"  Avail Margin:${s.available_margin_usdt:>10.2f} USDT")
        print(f"  Trades Today:{s.trades_today:>5} / {self.max_trades_per_day}")
        print(f"  Loss Limit:  ${s.daily_pnl_usdt:>+.2f} / -${self.max_loss_per_day_usdt:.2f}")
        if self.position_risks:
            print(f"\n  Open Positions ({len(self.position_risks)}):")
            for sym, p in self.position_risks.items():
                pnl_color = "\033[92m" if p.unrealized_pnl_usdt >= 0 else "\033[91m"
                print(f"    {sym} [{p.side}] @ ${p.entry_price:.2f} | "
                      f"qty={p.quantity} | "
                      f"{pnl_color}PnL: ${p.unrealized_pnl_usdt:+.2f}\033[0m")
        print(f"{'='*55}\n")

    def get_trade_log(self) -> List[Dict]:
        """Return copy of trade log. Mirrors EOSRiskManager.get_trade_log()."""
        return list(self.trade_log)
