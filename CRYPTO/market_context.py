"""
CRYPTO Market Context
Mirrors EOS/eos_market_context.py structure exactly.

Provides:
- Session classification (ASIA / EUROPE / US based on IST)
- Market trend classification from BTC price change
- Funding rate sentiment classification
- Volatility regime classification
- CryptoValidatorCache (mirrors EOSValidatorCache)
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, Dict, Any

import pytz

IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# VALIDATOR CACHE (mirrors eos_validator_cache.py embedded here)
# =============================================================================

@dataclass
class _CacheEntry:
    data: Any
    timestamp: float
    ttl: int

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class CryptoValidatorCache:
    """
    TTL-based in-memory cache. Mirrors EOSValidatorCache exactly.
    Used by CryptoMarketContext and CryptoAIValidator.
    """

    DEFAULT_TTLS: Dict[str, int] = {
        "market_data":   60,    # BTC/ETH prices, funding rates
        "ticker":        30,    # Individual ticker cache
        "fear_greed":    300,   # Fear & greed index (5 min)
        "pair_context":  120,   # Static pair metadata (2 min)
        "validation":    3600,  # AI validation history (1 hr)
    }

    def __init__(self) -> None:
        self._cache: Dict[str, _CacheEntry] = {}
        self._hits: int = 0
        self._misses: int = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None or entry.is_expired():
            self._misses += 1
            if key in self._cache:
                del self._cache[key]
            return None
        self._hits += 1
        return entry.data

    def set(self, key: str, data: Any, ttl: int = 60, category: str = "") -> None:
        if ttl == 0 and category:
            ttl = self.DEFAULT_TTLS.get(category, 60)
        self._cache[key] = _CacheEntry(data=data, timestamp=time.time(), ttl=ttl)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def invalidate_category(self, prefix: str) -> None:
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            del self._cache[k]

    def get_stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits":      self._hits,
            "misses":    self._misses,
            "hit_rate":  round(self._hits / total * 100, 1) if total else 0,
            "size":      len(self._cache),
        }


# =============================================================================
# MARKET CONTEXT DATACLASS
# =============================================================================

@dataclass
class CryptoMarketContextData:
    """
    Crypto market context snapshot. Mirrors EOS MarketContext.
    All fields populated with REAL data; None = unavailable.
    """
    timestamp: str                           # ISO format string

    # Index-like reference data
    btc_price: Optional[float] = None
    btc_change_pct: Optional[float] = None
    eth_price: Optional[float] = None
    eth_change_pct: Optional[float] = None

    # Sentiment / volatility
    btc_funding_rate: Optional[float] = None  # decimal (e.g., 0.0001)
    market_trend: Optional[str] = None        # "BULLISH", "BEARISH", "NEUTRAL"
    volatility_regime: Optional[str] = None   # "LOW", "MEDIUM", "HIGH"
    funding_sentiment: Optional[str] = None   # "LONG_HEAVY","SHORT_HEAVY","NEUTRAL"

    # Session & time
    trading_session: Optional[str] = None    # "ASIA", "EUROPE", "US", "OFF"
    day_of_week: Optional[str] = None
    is_weekend: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp":          self.timestamp,
            "btc_price":          self.btc_price,
            "btc_change_pct":     self.btc_change_pct,
            "eth_price":          self.eth_price,
            "eth_change_pct":     self.eth_change_pct,
            "btc_funding_rate":   self.btc_funding_rate,
            "market_trend":       self.market_trend,
            "volatility_regime":  self.volatility_regime,
            "funding_sentiment":  self.funding_sentiment,
            "trading_session":    self.trading_session,
            "day_of_week":        self.day_of_week,
            "is_weekend":         self.is_weekend,
        }

    def has_market_data(self) -> bool:
        return self.btc_price is not None and self.btc_price > 0


# =============================================================================
# STATIC PAIR REFERENCE (mirrors STOCK_SECTORS in EOS)
# =============================================================================

PAIR_CATEGORIES: Dict[str, str] = {
    "BTCUSDT": "Layer1",
    "ETHUSDT": "Layer1",
    "SOLUSDT": "Layer1",
    "BNBUSDT": "Exchange",
    "XRPUSDT": "Payments",
}


# =============================================================================
# MARKET CONTEXT MANAGER
# =============================================================================

class CryptoMarketContext:
    """
    Crypto Market Context Manager. Mirrors EOSMarketContext.

    Session classification (IST):
        ASIA:   05:30 - 14:00
        EUROPE: 13:30 - 22:00
        US:     18:30 - 03:00 (next day)
        OFF:    Everything else (low-volume)

    Usage:
        ctx_mgr = CryptoMarketContext()
        ctx = ctx_mgr.build_context(btc_price=95000, btc_change_pct=-4.2,
                                     btc_funding_rate=-0.0003)
    """

    def __init__(self, cache: Optional[CryptoValidatorCache] = None) -> None:
        self.cache = cache or CryptoValidatorCache()
        self._last_context: Optional[CryptoMarketContextData] = None

    # -------------------------------------------------------------------------
    # Static classification methods (mirrors EOSMarketContext statics)
    # -------------------------------------------------------------------------

    @staticmethod
    def get_trading_session(time_str: str = None) -> str:
        """
        Classify IST time into trading session.
        Uses current time if time_str is None.
        """
        if time_str:
            try:
                h, m = map(int, time_str.split(":"))
                total_minutes = h * 60 + m
            except ValueError:
                total_minutes = datetime.now(IST).hour * 60 + datetime.now(IST).minute
        else:
            now = datetime.now(IST)
            total_minutes = now.hour * 60 + now.minute

        # ASIA: 05:30 - 14:00
        if 330 <= total_minutes < 840:
            return "ASIA"
        # EUROPE: 13:30 - 22:00
        elif 810 <= total_minutes < 1320:
            return "EUROPE"
        # US: 18:30 - 23:59 or 00:00 - 03:00
        elif total_minutes >= 1110 or total_minutes < 180:
            return "US"
        else:
            return "OFF"

    @staticmethod
    def classify_volatility(btc_change_pct: float) -> str:
        """
        Classify market volatility from BTC 24h % change.
        HIGH if abs > 3%, MEDIUM if abs > 1%, else LOW.
        """
        abs_chg = abs(btc_change_pct or 0)
        if abs_chg > 3.0:
            return "HIGH"
        elif abs_chg > 1.0:
            return "MEDIUM"
        else:
            return "LOW"

    @staticmethod
    def classify_trend(btc_change_pct: float) -> str:
        """
        BULLISH if BTC > +1%, BEARISH if BTC < -1%, else NEUTRAL.
        Mirrors EOSMarketContext.classify_trend() but with crypto thresholds.
        """
        if (btc_change_pct or 0) > 1.0:
            return "BULLISH"
        elif (btc_change_pct or 0) < -1.0:
            return "BEARISH"
        else:
            return "NEUTRAL"

    @staticmethod
    def classify_funding_sentiment(funding_rate: float) -> str:
        """
        LONG_HEAVY: funding > 0.01% (longs pay shorts, market over-leveraged long)
        SHORT_HEAVY: funding < -0.01% (shorts pay longs, market over-leveraged short)
        NEUTRAL: otherwise
        """
        threshold = 0.0001  # 0.01%
        if funding_rate > threshold:
            return "LONG_HEAVY"
        elif funding_rate < -threshold:
            return "SHORT_HEAVY"
        else:
            return "NEUTRAL"

    # -------------------------------------------------------------------------
    # Context builder
    # -------------------------------------------------------------------------

    def build_context(
        self,
        btc_price: float = None,
        btc_change_pct: float = None,
        eth_price: float = None,
        eth_change_pct: float = None,
        btc_funding_rate: float = None,
        date_str: str = None,
        time_str: str = None,
    ) -> CryptoMarketContextData:
        """
        Build a CryptoMarketContextData from real market data.
        Mirrors EOSMarketContext.get_context().

        All classification is derived from real inputs; no synthetic data.
        """
        now = datetime.now(IST)
        if date_str is None:
            date_str = now.strftime("%Y-%m-%d")
        if time_str is None:
            time_str = now.strftime("%H:%M")

        timestamp = now.isoformat()
        day_of_week = now.strftime("%A")
        weekday_num = now.weekday()
        is_weekend = weekday_num >= 5

        market_trend = self.classify_trend(btc_change_pct or 0)
        volatility_regime = self.classify_volatility(btc_change_pct or 0)
        trading_session = self.get_trading_session(time_str)
        funding_sentiment = self.classify_funding_sentiment(btc_funding_rate or 0)

        ctx = CryptoMarketContextData(
            timestamp=timestamp,
            btc_price=btc_price,
            btc_change_pct=btc_change_pct,
            eth_price=eth_price,
            eth_change_pct=eth_change_pct,
            btc_funding_rate=btc_funding_rate,
            market_trend=market_trend,
            volatility_regime=volatility_regime,
            funding_sentiment=funding_sentiment,
            trading_session=trading_session,
            day_of_week=day_of_week,
            is_weekend=is_weekend,
        )

        self._last_context = ctx
        return ctx

    def get_last_context(self) -> Optional[CryptoMarketContextData]:
        """Return most recently built context."""
        return self._last_context

    @staticmethod
    def get_pair_context(symbol: str) -> Dict[str, str]:
        """Static reference data for a pair. Mirrors EOSMarketContext sector lookup."""
        from .config import CRYPTO_PAIRS
        pair_info = CRYPTO_PAIRS.get(symbol, {})
        return {
            "symbol":       symbol,
            "category":     PAIR_CATEGORIES.get(symbol, "Unknown"),
            "base":         pair_info.get("base_currency", ""),
            "quote":        pair_info.get("quote_currency", "USDT"),
            "market_type":  "linear_perpetual",
        }
