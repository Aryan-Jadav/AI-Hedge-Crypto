"""
EOS Strategy Configuration
Contains all strategy parameters, stock universe, and API configuration.

SECURITY NOTE:
- For production, move API credentials to environment variables
- Never commit real API keys to public repositories
- Use: export DHAN_CLIENT_ID="your_id" and export DHAN_ACCESS_TOKEN="your_token"
"""

import os
from typing import Dict, Any

# ===== DHAN API CONFIGURATION =====
# Load credentials from environment variables.
# SECURITY: do not hardcode real credentials in the repository.
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
API_BASE_URL = "https://api.dhan.co/v2"

# ===== EOS STRATEGY PARAMETERS =====
EOS_CONFIG = {
    # Entry Thresholds
    "price_change_threshold": 2.0,      # >2% price change from prev day close
    "oi_change_threshold": 1.75,        # >1.75% OI change from prev day close

    # Time Settings
    "candle_interval": "5",             # 5-minute candles (aggregate 2 for 10-min)
    "market_open": "09:15",
    "market_close": "15:30",
    "final_exit_time": "15:18",         # 3:18 PM - final exit
    "sma_active_after_minutes": 60,     # SMA exit active after 1 hour from entry

    # SMA Settings (calculated on FUTURES price)
    "sma_fast": 8,                      # 8-period SMA
    "sma_slow": 20,                     # 20-period SMA

    # Risk Management
    "initial_stop_loss_pct": 30.0,      # 30% of option purchase price
    "trailing_sl_trigger": 10.0,        # Trail SL by ₹10 for every ₹10 move
    "trailing_sl_amount": 10.0,

    # Position Sizing
    "lots_per_trade": 1,
    "max_trades_per_day": 5,
    "max_loss_per_day": 25000,          # ₹25,000 max loss per day
    "total_capital": 500000,            # ₹5,00,000 total capital

    # Strike Selection
    "strike_selection": "NEAREST_OTM",  # 1 strike out-of-the-money
    "expiry_type": "MONTH",             # Monthly expiry only

    # Re-entry Rules
    "allow_reentry": False,             # No re-entry after stop loss
}

# ===== FNO STOCKS UNIVERSE =====
# Contains equity_id, futures_id (current month), lot_size for each stock
# Futures IDs are for Feb 2026 expiry - need to update monthly
FNO_STOCKS: Dict[str, Dict[str, Any]] = {
    "RELIANCE": {"equity_id": 2885, "futures_id": 59460, "lot_size": 250, "segment": "NSE_EQ"},
    "HDFCBANK": {"equity_id": 1333, "futures_id": 59345, "lot_size": 550, "segment": "NSE_EQ"},
    "ICICIBANK": {"equity_id": 4963, "futures_id": 59353, "lot_size": 700, "segment": "NSE_EQ"},
    "TCS": {"equity_id": 11536, "futures_id": 59489, "lot_size": 175, "segment": "NSE_EQ"},
    "INFY": {"equity_id": 1594, "futures_id": 59375, "lot_size": 400, "segment": "NSE_EQ"},
    "SBIN": {"equity_id": 3045, "futures_id": 59466, "lot_size": 750, "segment": "NSE_EQ"},
    "AXISBANK": {"equity_id": 5900, "futures_id": 59215, "lot_size": 625, "segment": "NSE_EQ"},
    "KOTAKBANK": {"equity_id": 1922, "futures_id": 59397, "lot_size": 2000, "segment": "NSE_EQ"},
    "BAJFINANCE": {"equity_id": 317, "futures_id": 59255, "lot_size": 750, "segment": "NSE_EQ"},
    "MARUTI": {"equity_id": 10999, "futures_id": 59419, "lot_size": 50, "segment": "NSE_EQ"},
    "SUNPHARMA": {"equity_id": 3351, "futures_id": 59473, "lot_size": 350, "segment": "NSE_EQ"},
    "TITAN": {"equity_id": 3506, "futures_id": 59492, "lot_size": 175, "segment": "NSE_EQ"},
    "WIPRO": {"equity_id": 3787, "futures_id": 59519, "lot_size": 3000, "segment": "NSE_EQ"},
    "LTIM": {"equity_id": 17818, "futures_id": 59413, "lot_size": 150, "segment": "NSE_EQ"},
    "HCLTECH": {"equity_id": 7229, "futures_id": 59343, "lot_size": 350, "segment": "NSE_EQ"},
    "ADANIENT": {"equity_id": 25, "futures_id": 59194, "lot_size": 309, "segment": "NSE_EQ"},
    "TATASTEEL": {"equity_id": 3499, "futures_id": 59484, "lot_size": 5500, "segment": "NSE_EQ"},
    "JSWSTEEL": {"equity_id": 11723, "futures_id": 59391, "lot_size": 675, "segment": "NSE_EQ"},
    "HINDALCO": {"equity_id": 1363, "futures_id": 59348, "lot_size": 700, "segment": "NSE_EQ"},
    "M&M": {"equity_id": 2031, "futures_id": 59415, "lot_size": 200, "segment": "NSE_EQ"},
}

# Index configurations (for future use)
INDEX_CONFIG = {
    "NIFTY": {"security_id": 13, "segment": "IDX_I", "lot_size": 25},
    "BANKNIFTY": {"security_id": 25, "segment": "IDX_I", "lot_size": 15},
}

# Exchange segment mappings
EXCHANGE_SEGMENTS = {
    "NSE_EQ": 1,    # NSE Equity Cash
    "NSE_FNO": 2,   # NSE Futures & Options
    "IDX_I": 0,     # Index
}

# Instrument types
INSTRUMENTS = {
    "EQUITY": "EQUITY",
    "FUTSTK": "FUTSTK",
    "FUTIDX": "FUTIDX",
    "OPTSTK": "OPTSTK",
    "OPTIDX": "OPTIDX",
}
