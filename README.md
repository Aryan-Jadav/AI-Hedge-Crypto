# AI Hedge Fund V3

An AI-powered algorithmic trading system for Indian F&O markets, built with Python and Claude AI integration.

## Overview

This project implements automated trading strategies with backtesting capabilities using Dhan API for market data. The system is designed to be modular, allowing multiple strategies to coexist.

## Current Strategies

### EOS (Exhaustion of Strength)
A contrarian/mean reversion options trading strategy that identifies exhaustion points in price movements.

**Key Features:**
- Buys PUTs when stocks are UP > 2% (expects reversal)
- Buys CALLs when stocks are DOWN > 2% (expects reversal)
- AI-powered trade validation using Claude Sonnet 4.5
- Comprehensive risk management and position sizing
- SQLite-based portfolio tracking and analytics

[📖 EOS Strategy Documentation](EOS/README.md)

## Project Structure

```
AI-Hedge-Fund-V3/
├── README.md                           # This file
├── .gitignore                          # Git ignore file
├── DHAN_EXPIRED_OPTIONS_DOCUMENTATION.md  # Dhan API docs
├── api-scrip-master.csv                # Instrument master data
├── dhan_expired_options.py             # Base Dhan API integration
├── eos_data_fetcher.py                 # Data fetching utilities
│
└── EOS/                                # EOS Strategy Package
    ├── __init__.py                     # Package exports
    ├── config.py                       # Strategy configuration
    ├── data_fetcher.py                 # Options data fetcher
    ├── eos_strategy_engine.py          # Signal generation
    ├── eos_backtester.py               # Backtesting framework
    ├── eos_portfolio_manager.py        # SQLite portfolio tracking
    ├── eos_risk_manager.py             # Risk management
    ├── eos_ai_validator.py             # Claude AI trade validation
    ├── eos_market_context.py           # Market data structures
    ├── eos_validator_cache.py          # Caching layer
    ├── README.md                       # EOS quick start guide
    ├── RULES.md                        # Strategy rules
    └── EOS_STRATEGY_DOCUMENTATION.md   # Full documentation
```

## Quick Start

### Prerequisites

- Python 3.10+
- Dhan trading account with API access
- OpenRouter API key (for AI validation)

### Installation

```bash
# Clone the repository
git clone https://github.com/ShlokNambiar/Ai-Hedge-Fund-V3.git
cd Ai-Hedge-Fund-V3

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install requests pandas
```

### Configuration

1. Copy your Dhan API credentials to `EOS/config.py`:
   - `DHAN_CLIENT_ID`
   - `DHAN_ACCESS_TOKEN`

2. Set OpenRouter API key for AI validation (optional):
   - Pass directly to `EOSAIValidator(api_key="your_key")`

### Running a Backtest

```python
from EOS import EOSBacktester

backtester = EOSBacktester()
result = backtester.run_backtest(
    symbols=['RELIANCE', 'TATASTEEL'],
    start_date='2025-12-01',
    end_date='2025-12-31',
    expiry_code=1
)
backtester.print_summary(result)
```

### With AI Validation

```python
from EOS import EOSBacktester, EOSAIValidator, SignalData

backtester = EOSBacktester()
validator = EOSAIValidator(api_key="your_openrouter_key")

result = backtester.run_backtest(...)

for trade in result.trades:
    signal = SignalData(
        symbol=trade.symbol,
        signal_type=trade.option_type,
        entry_price=trade.entry_price,
        # ... other fields
    )
    validation = validator.validate(signal)
    print(f"{trade.symbol}: {validation.result.value}")
```

## Components

| Component | Description |
|-----------|-------------|
| **EOSBacktester** | Runs historical backtests using expired options data |
| **EOSRiskManager** | Tracks daily P&L, enforces loss limits |
| **EOSPortfolioManager** | SQLite database for trade history and analytics |
| **EOSAIValidator** | Claude-powered trade validation (APPROVE/REJECT) |

## API Documentation

- [Dhan Expired Options API](DHAN_EXPIRED_OPTIONS_DOCUMENTATION.md)
- [EOS Strategy Rules](EOS/RULES.md)

## Technology Stack

- **Language:** Python 3.10+
- **Data Source:** Dhan API v2
- **AI Integration:** Claude Sonnet 4.5 via OpenRouter
- **Database:** SQLite (for portfolio tracking)

## Important Notes

1. **API Rate Limits:** Dhan API has rate limits - the backtester handles this automatically
2. **Data Availability:** Expired options data is available for the past 5 years
3. **No Fake Data:** The system uses only authentic market data - no simulated or placeholder data

## License

Private repository - All rights reserved.

## Author

Shlok Nambiar

