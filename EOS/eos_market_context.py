"""
EOS Market Context - Data structures and utilities for market context

NOTE: This module provides data structures and helper functions ONLY.
Real market data (NIFTY, VIX, news, etc.) must be fetched from actual APIs.
A data pipeline will be built later to provide real-time market data.

NO FAKE/GENERATED/PLACEHOLDER DATA IS USED.
"""

from datetime import datetime, date, timedelta
from typing import Dict, Optional
from dataclasses import dataclass
from .eos_validator_cache import EOSValidatorCache


@dataclass
class MarketContext:
    """
    Market context data for AI validation.

    All fields must be populated with REAL data from APIs.
    None values indicate data not available.
    """
    timestamp: str
    # Index data - must come from real API
    nifty_level: Optional[float] = None
    nifty_change_pct: Optional[float] = None
    banknifty_level: Optional[float] = None
    banknifty_change_pct: Optional[float] = None
    # VIX - must come from real API
    vix: Optional[float] = None
    vix_change_pct: Optional[float] = None
    # Derived from real data
    market_trend: Optional[str] = None  # "BULLISH", "BEARISH", "NEUTRAL"
    volatility_regime: Optional[str] = None  # "LOW", "MEDIUM", "HIGH"
    # Time-based (can be derived from timestamp)
    trading_session: Optional[str] = None  # "OPENING", "MORNING", "AFTERNOON", "CLOSING"
    day_of_week: Optional[str] = None
    is_expiry_day: Optional[bool] = None
    is_monthly_expiry: Optional[bool] = None

    def to_dict(self) -> Dict:
        """Convert to dict for JSON serialization."""
        return {
            "ts": self.timestamp,
            "nifty": {"lvl": self.nifty_level, "chg": self.nifty_change_pct},
            "bnf": {"lvl": self.banknifty_level, "chg": self.banknifty_change_pct},
            "vix": {"lvl": self.vix, "chg": self.vix_change_pct},
            "trend": self.market_trend,
            "vol": self.volatility_regime,
            "session": self.trading_session,
            "dow": self.day_of_week,
            "expiry": self.is_expiry_day,
            "monthly_exp": self.is_monthly_expiry
        }

    def has_market_data(self) -> bool:
        """Check if essential market data is available."""
        return self.nifty_level is not None and self.vix is not None


# Sector mapping - this is static reference data, not generated
STOCK_SECTORS = {
    "RELIANCE": "Energy",
    "HDFCBANK": "Banking",
    "ICICIBANK": "Banking",
    "SBIN": "Banking",
    "AXISBANK": "Banking",
    "KOTAKBANK": "Banking",
    "TCS": "IT",
    "INFY": "IT",
    "WIPRO": "IT",
    "HCLTECH": "IT",
    "LTIM": "IT",
    "TATASTEEL": "Metals",
    "JSWSTEEL": "Metals",
    "HINDALCO": "Metals",
    "SUNPHARMA": "Pharma",
    "BAJFINANCE": "NBFC",
    "MARUTI": "Auto",
    "M&M": "Auto",
    "TITAN": "Consumer",
    "ADANIENT": "Infra",
}


class EOSMarketContext:
    """
    Market context manager.

    This class provides:
    1. Helper functions to derive values from timestamps (session, expiry day)
    2. Sector lookup (static reference data)
    3. Cache management for real market data

    IMPORTANT: This class does NOT generate any market data.
    Real data must be provided via set_real_market_data() or fetched from APIs.
    """

    def __init__(self, cache: EOSValidatorCache = None):
        self.cache = cache or EOSValidatorCache()
        self._real_market_data: Optional[MarketContext] = None

    # ===== TIME-BASED HELPERS (derived from timestamp, not fake data) =====

    def get_trading_session(self, time_str: str = None) -> str:
        """
        Determine trading session based on time.
        This is derived from actual time, not generated.
        """
        if time_str:
            hour, minute = map(int, time_str.split(":")[:2])
        else:
            now = datetime.now()
            hour, minute = now.hour, now.minute

        total_minutes = hour * 60 + minute

        if total_minutes < 9 * 60 + 30:  # Before 9:30
            return "OPENING"
        elif total_minutes < 12 * 60:  # Before 12:00
            return "MORNING"
        elif total_minutes < 14 * 60 + 30:  # Before 2:30
            return "AFTERNOON"
        else:
            return "CLOSING"

    def is_expiry_day(self, date_obj: date = None) -> bool:
        """Check if given date is Thursday (weekly expiry)."""
        if date_obj is None:
            date_obj = date.today()
        return date_obj.weekday() == 3  # Thursday

    def is_monthly_expiry(self, date_obj: date = None) -> bool:
        """Check if given date is last Thursday of month."""
        if date_obj is None:
            date_obj = date.today()
        if date_obj.weekday() != 3:
            return False
        next_thursday = date_obj + timedelta(days=7)
        return next_thursday.month != date_obj.month

    # ===== CLASSIFICATION HELPERS (derive from real data) =====

    @staticmethod
    def classify_volatility(vix: float) -> str:
        """Classify volatility regime based on VIX level."""
        if vix is None:
            return "UNKNOWN"
        if vix < 13:
            return "LOW"
        elif vix < 20:
            return "MEDIUM"
        else:
            return "HIGH"

    @staticmethod
    def classify_trend(change_pct: float) -> str:
        """Classify market trend based on index change %."""
        if change_pct is None:
            return "UNKNOWN"
        if change_pct > 0.5:
            return "BULLISH"
        elif change_pct < -0.5:
            return "BEARISH"
        else:
            return "NEUTRAL"

    # ===== SECTOR LOOKUP (static reference data) =====

    def get_stock_sector(self, symbol: str) -> str:
        """Get sector for a stock. This is static reference data."""
        return STOCK_SECTORS.get(symbol, "Unknown")

    # ===== REAL DATA MANAGEMENT =====

    def set_real_market_data(self, context: MarketContext):
        """
        Set real market data fetched from APIs.
        This is the ONLY way to provide market data.
        """
        self._real_market_data = context
        # Cache it
        if context.timestamp:
            self.cache.set(f"market_context_{context.timestamp}", context, category="market_data")

    def get_context(self, date_str: str = None, time_str: str = None) -> Optional[MarketContext]:
        """
        Get market context.

        Returns None if no real data is available.
        Time-based fields are derived from the timestamp.
        """
        # Check cache first
        cache_key = f"market_context_{date_str}_{time_str}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Return stored real data if available
        if self._real_market_data:
            return self._real_market_data

        # No real data available - return minimal context with only time-based fields
        if date_str and time_str:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            return MarketContext(
                timestamp=f"{date_str} {time_str}",
                trading_session=self.get_trading_session(time_str),
                day_of_week=date_obj.strftime("%A"),
                is_expiry_day=self.is_expiry_day(date_obj),
                is_monthly_expiry=self.is_monthly_expiry(date_obj)
                # All market data fields remain None - NO FAKE DATA
            )

        return None

    def get_stock_context(self, symbol: str) -> Dict:
        """
        Get stock-specific context.

        Only returns sector (static reference data).
        News, earnings, corporate actions must come from real APIs.
        """
        return {
            "symbol": symbol,
            "sector": self.get_stock_sector(symbol)
            # NO fake news/earnings/corporate action data
        }
