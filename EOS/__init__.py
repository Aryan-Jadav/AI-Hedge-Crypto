# EOS (Exhaustion of Strength) Trading Strategy Package
# This package contains the strategy engine, backtester, portfolio manager, and risk manager

__version__ = "1.0.0"
__author__ = "AI Hedge Fund"

from .config import EOS_CONFIG, FNO_STOCKS
from .data_fetcher import EOSDataFetcher
from .eos_strategy_engine import EOSStrategyEngine, Signal, SignalType, Position, ExitReason
from .eos_backtester import EOSBacktester, BacktestResult, BacktestTrade
from .eos_portfolio_manager import EOSPortfolioManager, TradeRecord, DailySnapshot
from .eos_risk_manager import EOSRiskManager, RiskState, PositionRisk
from .eos_validator_cache import EOSValidatorCache
from .eos_market_context import EOSMarketContext, MarketContext, STOCK_SECTORS
from .eos_ai_validator import EOSAIValidator, ValidationResult, ValidationResponse, SignalData

__all__ = [
    # Config
    "EOS_CONFIG",
    "FNO_STOCKS",
    # Data
    "EOSDataFetcher",
    # Strategy
    "EOSStrategyEngine",
    "Signal",
    "SignalType",
    "Position",
    "ExitReason",
    # Backtester
    "EOSBacktester",
    "BacktestResult",
    "BacktestTrade",
    # Portfolio Manager
    "EOSPortfolioManager",
    "TradeRecord",
    "DailySnapshot",
    # Risk Manager
    "EOSRiskManager",
    "RiskState",
    "PositionRisk",
    # AI Validator
    "EOSValidatorCache",
    "EOSMarketContext",
    "MarketContext",
    "STOCK_SECTORS",
    "EOSAIValidator",
    "ValidationResult",
    "ValidationResponse",
    "SignalData",
]

