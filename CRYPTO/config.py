"""
CRYPTO Strategy Configuration
Mirrors EOS/config.py structure exactly.

SECURITY NOTE:
- NEVER hardcode API credentials here.
- Set environment variables before running:
    export BYBIT_API_KEY="your_key"
    export BYBIT_API_SECRET="your_secret"
    export OPENROUTER_API_KEY="your_openrouter_key"
"""

import os
from typing import Dict, Any

# ===== BYBIT API CONFIGURATION =====
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

API_BASE_URL = "https://api.bybit.com/v5"

# ===== CRYPTO STRATEGY PARAMETERS =====
# CRITICAL: Do not change entry/exit logic parameters without full review.
# These mirror EOS_CONFIG structure exactly.
CRYPTO_CONFIG: Dict[str, Any] = {
    # ------- Entry Thresholds -------
    "price_change_threshold": 4.0,        # >4% price change (higher vol than stocks; EOS uses 2%)
    "funding_rate_threshold": 0.0001,     # abs(funding_rate) > 0.01% = elevated sentiment
    "volume_spike_multiplier": 1.5,       # current_volume > 1.5x avg_volume (20-period)

    # ------- Candle / SMA Settings -------
    "candle_interval": "5",               # 5-minute candles for SMA calculation
    "sma_fast": 8,                        # Fast SMA period (same as EOS)
    "sma_slow": 20,                       # Slow SMA period (same as EOS)
    "sma_active_after_minutes": 60,       # SMA exit check active 60 min after entry (same as EOS)
    "avg_volume_periods": 20,             # Periods to calculate average volume

    # ------- Session Settings (IST) -------
    "session_start": "05:30",            # Asia session open
    "session_end": "14:00",              # Asia session close
    "final_exit_time": "13:50",          # Mandatory exit 10 min before session end
    "pre_session_buffer": "05:20",       # Pre-session init start

    # ------- Risk Management -------
    "initial_stop_loss_pct": 3.0,        # 3% SL from entry (vs 30% of premium in EOS)
    "trailing_sl_trigger_pct": 1.0,      # Activate trail after 1% profit
    "trailing_sl_amount_pct": 1.0,       # Trail by 1% for every 1% favorable move
    "max_loss_per_day_usdt": 500.0,      # $500 max daily loss
    "total_capital_usdt": 10000.0,       # $10,000 total capital

    # ------- Position Sizing -------
    "contracts_per_trade": 1,            # 1 lot (1x min_qty from CRYPTO_PAIRS)
    "max_trades_per_day": 5,             # Max 5 trades per day (same as EOS)
    "allow_reentry": False,              # No re-entry on same symbol same day

    # ------- Leverage -------
    "leverage": 5,                       # 5x cross margin (used for margin calculation)

    # ------- Market Type -------
    "market_type": "24_7",              # Crypto never closes; use session windows
    "primary_session": "ASIA",

    # ------- Paper Trade Flag -------
    # Set to False for live trading (needs real BYBIT_API_KEY + BYBIT_API_SECRET)
    "paper_trade": True,
}

# ===== CRYPTO PAIRS UNIVERSE =====
# Mirrors FNO_STOCKS: symbol -> metadata dict
# category: Bybit category for linear (USDT-settled) perpetual futures
# min_qty:  minimum order size in base asset units
# tick_size: minimum price increment in USDT
CRYPTO_PAIRS: Dict[str, Dict[str, Any]] = {
    "BTCUSDT": {
        "category":       "linear",
        "min_qty":        0.001,         # 0.001 BTC per lot
        "tick_size":      0.10,          # $0.10 min price increment
        "contract_value": 1.0,           # 1 BTC per unit
        "base_currency":  "BTC",
        "quote_currency": "USDT",
    },
    "ETHUSDT": {
        "category":       "linear",
        "min_qty":        0.01,          # 0.01 ETH per lot
        "tick_size":      0.05,
        "contract_value": 1.0,
        "base_currency":  "ETH",
        "quote_currency": "USDT",
    },
    "SOLUSDT": {
        "category":       "linear",
        "min_qty":        1.0,           # 1 SOL per lot
        "tick_size":      0.001,
        "contract_value": 1.0,
        "base_currency":  "SOL",
        "quote_currency": "USDT",
    },
    "BNBUSDT": {
        "category":       "linear",
        "min_qty":        0.1,           # 0.1 BNB per lot
        "tick_size":      0.01,
        "contract_value": 1.0,
        "base_currency":  "BNB",
        "quote_currency": "USDT",
    },
    "XRPUSDT": {
        "category":       "linear",
        "min_qty":        10.0,          # 10 XRP per lot
        "tick_size":      0.0001,
        "contract_value": 1.0,
        "base_currency":  "XRP",
        "quote_currency": "USDT",
    },
}

# ===== EXCHANGE INFO =====
EXCHANGE_INFO = {
    "name": "Bybit",
    "api_version": "v5",
    "market_type": "linear_perpetual",
    "settlement": "USDT",
}
