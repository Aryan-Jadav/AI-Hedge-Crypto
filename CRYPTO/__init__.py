"""
CRYPTO Module - Bybit USDT Perpetual Futures Trading
CRYPTO-EOS Contrarian Strategy

Mirrors EOS/__init__.py package structure exactly.

Usage:
    from CRYPTO import CryptoLiveRunner, CRYPTO_CONFIG, CRYPTO_PAIRS
    runner = CryptoLiveRunner(paper_trade=True)
    runner.start()

Environment Variables Required:
    BYBIT_API_KEY      - Bybit API key (required for live trading)
    BYBIT_API_SECRET   - Bybit API secret (required for live trading)
    OPENROUTER_API_KEY - OpenRouter key for Claude AI validation

Strategy: CRYPTO-EOS (Exhaustion of Strength on Perps)
    - Price drops >4%  → LONG perp (expect bounce)
    - Price spikes >4% → SHORT perp (expect retrace)
    - Confirmation: funding rate extreme (abs > 0.01%) OR volume spike (>1.5x avg)
    - Stop loss: 3% from entry price
    - Trailing SL: 1% trail per 1% favorable move
    - Session: Asia session 05:30–14:00 IST
    - Pairs: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT
"""

__version__ = "1.0.0"
__author__  = "AI Hedge Fund"

from .config import CRYPTO_CONFIG, CRYPTO_PAIRS, BYBIT_API_KEY, BYBIT_API_SECRET

from .data_fetcher import CryptoDataFetcher

from .strategy_engine import (
    CryptoStrategyEngine,
    CryptoSignal,
    CryptoSignalType,
    CryptoPosition,
    CryptoExitReason,
)

from .portfolio_manager import (
    CryptoPortfolioManager,
    CryptoTradeRecord,
    CryptoDailySnapshot,
)

from .risk_manager import (
    CryptoRiskManager,
    CryptoRiskState,
    CryptoPositionRisk,
)

from .ai_validator import (
    CryptoAIValidator,
    CryptoValidationResult,
    CryptoValidationResponse,
    CryptoSignalData,
)

from .market_context import (
    CryptoMarketContext,
    CryptoMarketContextData,
    CryptoValidatorCache,
    PAIR_CATEGORIES,
)

from .websocket_feed import (
    CryptoWebSocketFeed,
    CryptoTickData,
)

from .live_runner import (
    CryptoLiveRunner,
    CryptoRunnerState,
    CryptoLivePosition,
    CryptoLiveTrade,
)

__all__ = [
    # Config
    "CRYPTO_CONFIG",
    "CRYPTO_PAIRS",
    "BYBIT_API_KEY",
    "BYBIT_API_SECRET",

    # Data
    "CryptoDataFetcher",

    # Strategy
    "CryptoStrategyEngine",
    "CryptoSignal",
    "CryptoSignalType",
    "CryptoPosition",
    "CryptoExitReason",

    # Portfolio
    "CryptoPortfolioManager",
    "CryptoTradeRecord",
    "CryptoDailySnapshot",

    # Risk
    "CryptoRiskManager",
    "CryptoRiskState",
    "CryptoPositionRisk",

    # AI
    "CryptoAIValidator",
    "CryptoValidationResult",
    "CryptoValidationResponse",
    "CryptoSignalData",

    # Market Context
    "CryptoMarketContext",
    "CryptoMarketContextData",
    "CryptoValidatorCache",
    "PAIR_CATEGORIES",

    # WebSocket
    "CryptoWebSocketFeed",
    "CryptoTickData",

    # Live Runner
    "CryptoLiveRunner",
    "CryptoRunnerState",
    "CryptoLivePosition",
    "CryptoLiveTrade",
]
