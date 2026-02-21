# Last updated: 2026-02-21
"""
EOS Risk Manager - Position Sizing & Risk Limits

IMPORTANT: This module does NOT alter the strategy logic.
It only:
1. Calculates position size based on available capital
2. Tracks daily loss limits
3. Blocks NEW trades if limits are hit
4. Calculates margin requirements
5. Logs risk metrics

The strategy (entry signals, exit rules, SL, etc.) remains UNCHANGED.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from .config import EOS_CONFIG, FNO_STOCKS


@dataclass
class RiskState:
    """Current risk state for the trading session."""
    date: str
    initial_capital: float
    current_capital: float
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    total_exposure: float = 0.0
    margin_used: float = 0.0
    available_margin: float = 0.0
    daily_loss_limit_hit: bool = False
    max_trades_hit: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "date": self.date,
            "initial_capital": self.initial_capital,
            "current_capital": self.current_capital,
            "daily_pnl": self.daily_pnl,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "trades_today": self.trades_today,
            "open_positions": self.open_positions,
            "total_exposure": self.total_exposure,
            "margin_used": self.margin_used,
            "available_margin": self.available_margin,
            "daily_loss_limit_hit": self.daily_loss_limit_hit,
            "max_trades_hit": self.max_trades_hit
        }


@dataclass
class PositionRisk:
    """Risk metrics for a single position."""
    symbol: str
    option_type: str
    entry_price: float
    current_price: float
    quantity: int
    lot_size: int
    exposure: float  # entry_price * quantity
    margin_required: float
    unrealized_pnl: float
    max_loss: float  # Based on initial SL
    risk_pct: float  # Risk as % of capital


class EOSRiskManager:
    """
    EOS Risk Manager - Manages position sizing and risk limits.
    
    DOES NOT alter:
    - Entry conditions (2%, 1.75%)
    - Exit rules (SL, trailing SL, SMA, time)
    - Signal direction (PUT/CALL)
    
    ONLY manages:
    - Position sizing based on capital
    - Daily loss limit tracking
    - Trade count limits
    - Margin calculations
    """
    
    def __init__(self, initial_capital: float = None):
        self.config = EOS_CONFIG
        self.initial_capital = initial_capital or self.config.get("total_capital", 500000)
        self.current_capital = self.initial_capital
        
        # Daily limits from config
        self.max_loss_per_day = self.config.get("max_loss_per_day", 25000)
        self.max_trades_per_day = self.config.get("max_trades_per_day", 5)
        self.max_exposure = self.config.get("max_exposure", 300000)
        
        # Margin requirement (options typically 100% of premium)
        self.margin_multiplier = 1.0  # 100% of option value required
        
        # Current state
        self.risk_state = self._create_new_state()
        self.position_risks: Dict[str, PositionRisk] = {}
        self.trade_log: List[Dict] = []
    
    def _create_new_state(self) -> RiskState:
        """Create a fresh risk state for a new day."""
        return RiskState(
            date=datetime.now().strftime("%Y-%m-%d"),
            initial_capital=self.initial_capital,
            current_capital=self.current_capital,
            available_margin=self.current_capital
        )
    
    def reset_daily_state(self):
        """Reset state for a new trading day."""
        self.risk_state = self._create_new_state()
        self.position_risks.clear()
    
    # ===== POSITION SIZING (Does NOT change strategy) =====
    
    def get_position_size(self, symbol: str) -> Tuple[int, str]:
        """
        Get position size for a symbol.
        
        Returns:
            Tuple of (lots, reason)
            
        NOTE: Always returns 1 lot as per strategy rules.
        This method exists for future flexibility but does NOT
        change the strategy's fixed 1-lot-per-trade rule.
        """
        # Strategy rule: Always 1 lot
        lots = self.config.get("lots_per_trade", 1)
        return lots, "Fixed 1 lot per strategy rules"
    
    def calculate_margin_required(self, symbol: str, option_price: float, 
                                   lots: int = 1) -> float:
        """
        Calculate margin required for a trade.
        
        Args:
            symbol: Stock symbol
            option_price: Current option premium
            lots: Number of lots
            
        Returns:
            Margin required in rupees
        """
        stock_info = FNO_STOCKS.get(symbol, {})
        lot_size = stock_info.get("lot_size", 1)
        
        # Option buying requires 100% of premium
        exposure = option_price * lot_size * lots
        margin = exposure * self.margin_multiplier
        
        return margin

    def calculate_max_loss(self, symbol: str, entry_price: float,
                           lots: int = 1) -> float:
        """
        Calculate maximum possible loss based on initial stop loss.

        Args:
            symbol: Stock symbol
            entry_price: Option entry price
            lots: Number of lots

        Returns:
            Maximum loss in rupees (positive number)
        """
        stock_info = FNO_STOCKS.get(symbol, {})
        lot_size = stock_info.get("lot_size", 1)

        sl_pct = self.config.get("initial_stop_loss_pct", 30) / 100
        max_loss_per_unit = entry_price * sl_pct

        return max_loss_per_unit * lot_size * lots

    # ===== TRADE VALIDATION (Only blocks, does NOT modify) =====

    def can_take_trade(self, symbol: str, option_price: float = 0) -> Tuple[bool, str]:
        """
        Check if a new trade can be taken.

        This ONLY returns True/False - it does NOT modify the trade
        or the strategy in any way.

        Args:
            symbol: Stock symbol
            option_price: Estimated option premium (for margin check)

        Returns:
            Tuple of (can_trade, reason)
        """
        state = self.risk_state

        # Check 1: Daily loss limit
        if state.daily_pnl <= -self.max_loss_per_day:
            state.daily_loss_limit_hit = True
            return False, f"Daily loss limit hit (₹{self.max_loss_per_day:,.0f})"

        # Check 2: Max trades per day
        if state.trades_today >= self.max_trades_per_day:
            state.max_trades_hit = True
            return False, f"Max trades ({self.max_trades_per_day}) reached for today"

        # Check 3: Available margin (if option_price provided)
        if option_price > 0:
            margin_needed = self.calculate_margin_required(symbol, option_price)
            if margin_needed > state.available_margin:
                return False, f"Insufficient margin (need ₹{margin_needed:,.0f}, have ₹{state.available_margin:,.0f})"

        # Check 4: Max exposure
        if option_price > 0:
            new_exposure = state.total_exposure + self.calculate_margin_required(symbol, option_price)
            if new_exposure > self.max_exposure:
                return False, f"Max exposure (₹{self.max_exposure:,.0f}) would be exceeded"

        return True, "Trade allowed"

    # ===== POSITION TRACKING =====

    def register_entry(self, symbol: str, option_type: str, entry_price: float,
                       quantity: int, lot_size: int):
        """
        Register a new position for risk tracking.

        This is called AFTER the strategy decides to enter.
        It does NOT influence the entry decision.
        """
        exposure = entry_price * quantity
        margin = exposure * self.margin_multiplier
        max_loss = self.calculate_max_loss(symbol, entry_price, quantity // lot_size)

        self.position_risks[symbol] = PositionRisk(
            symbol=symbol,
            option_type=option_type,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            lot_size=lot_size,
            exposure=exposure,
            margin_required=margin,
            unrealized_pnl=0.0,
            max_loss=max_loss,
            risk_pct=(max_loss / self.current_capital) * 100
        )

        # Update state
        self.risk_state.trades_today += 1
        self.risk_state.open_positions += 1
        self.risk_state.total_exposure += exposure
        self.risk_state.margin_used += margin
        self.risk_state.available_margin = self.current_capital - self.risk_state.margin_used

        self.trade_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": "ENTRY",
            "symbol": symbol,
            "option_type": option_type,
            "entry_price": entry_price,
            "quantity": quantity,
            "exposure": exposure,
            "margin_used": margin
        })

    def register_exit(self, symbol: str, exit_price: float, exit_reason: str):
        """
        Register position exit and update P&L.

        This is called AFTER the strategy decides to exit.
        It does NOT influence the exit decision.
        """
        if symbol not in self.position_risks:
            return

        pos = self.position_risks[symbol]
        pnl = (exit_price - pos.entry_price) * pos.quantity

        # Update state
        self.risk_state.realized_pnl += pnl
        self.risk_state.daily_pnl += pnl
        self.risk_state.open_positions -= 1
        self.risk_state.total_exposure -= pos.exposure
        self.risk_state.margin_used -= pos.margin_required
        self.risk_state.available_margin = self.current_capital - self.risk_state.margin_used

        # Update capital
        self.current_capital += pnl
        self.risk_state.current_capital = self.current_capital

        self.trade_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": "EXIT",
            "symbol": symbol,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl": pnl,
            "daily_pnl": self.risk_state.daily_pnl
        })

        # Remove position
        del self.position_risks[symbol]

    def update_position_price(self, symbol: str, current_price: float):
        """Update current price for unrealized P&L calculation."""
        if symbol not in self.position_risks:
            return

        pos = self.position_risks[symbol]
        pos.current_price = current_price
        pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

        # Update total unrealized
        self.risk_state.unrealized_pnl = sum(
            p.unrealized_pnl for p in self.position_risks.values()
        )

    # ===== REPORTING =====

    def get_risk_summary(self) -> Dict:
        """Get current risk state summary."""
        return {
            **self.risk_state.to_dict(),
            "position_count": len(self.position_risks),
            "positions": {
                symbol: {
                    "option_type": pos.option_type,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "quantity": pos.quantity,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "max_loss": pos.max_loss,
                    "risk_pct": pos.risk_pct
                }
                for symbol, pos in self.position_risks.items()
            }
        }

    def print_risk_status(self):
        """Print formatted risk status."""
        state = self.risk_state

        print("\n" + "=" * 60)
        print("EOS RISK MANAGER STATUS")
        print("=" * 60)
        print(f"Date: {state.date}")
        print(f"\n--- CAPITAL ---")
        print(f"Initial Capital:    ₹{state.initial_capital:,.2f}")
        print(f"Current Capital:    ₹{state.current_capital:,.2f}")
        print(f"Available Margin:   ₹{state.available_margin:,.2f}")

        print(f"\n--- P&L ---")
        pnl_str = f"₹{state.daily_pnl:,.2f}" if state.daily_pnl >= 0 else f"-₹{abs(state.daily_pnl):,.2f}"
        print(f"Daily P&L:          {pnl_str}")
        print(f"Realized P&L:       ₹{state.realized_pnl:,.2f}")
        print(f"Unrealized P&L:     ₹{state.unrealized_pnl:,.2f}")

        print(f"\n--- LIMITS ---")
        print(f"Trades Today:       {state.trades_today}/{self.max_trades_per_day}")
        print(f"Daily Loss Limit:   ₹{self.max_loss_per_day:,.0f} ({'HIT ❌' if state.daily_loss_limit_hit else 'OK ✅'})")
        print(f"Max Exposure:       ₹{self.max_exposure:,.0f} (Using: ₹{state.total_exposure:,.0f})")

        print(f"\n--- POSITIONS ({state.open_positions}) ---")
        if self.position_risks:
            for symbol, pos in self.position_risks.items():
                pnl_sym = "+" if pos.unrealized_pnl >= 0 else ""
                print(f"  {symbol}: {pos.option_type} @ ₹{pos.entry_price:.2f} → ₹{pos.current_price:.2f} "
                      f"({pnl_sym}₹{pos.unrealized_pnl:.2f})")
        else:
            print("  No open positions")

        print("=" * 60)

    def get_trade_log(self) -> List[Dict]:
        """Get full trade log for the session."""
        return self.trade_log.copy()

    # ===== BACKTEST INTEGRATION =====

    def simulate_day_start(self, date_str: str, capital: float = None):
        """Start a new simulated day for backtesting."""
        if capital is not None:
            self.current_capital = capital
            self.initial_capital = capital

        self.risk_state = RiskState(
            date=date_str,
            initial_capital=self.initial_capital,
            current_capital=self.current_capital,
            available_margin=self.current_capital
        )
        self.position_risks.clear()

    def simulate_day_end(self) -> Dict:
        """End a simulated day and return summary."""
        return {
            "date": self.risk_state.date,
            "starting_capital": self.initial_capital,
            "ending_capital": self.current_capital,
            "daily_pnl": self.risk_state.daily_pnl,
            "trades": self.risk_state.trades_today,
            "daily_loss_limit_hit": self.risk_state.daily_loss_limit_hit,
            "max_trades_hit": self.risk_state.max_trades_hit
        }

