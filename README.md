> [!NOTE]
> Last updated: 2026-02-22

# AI Hedge Fund — Dual-Market Algorithmic Trading System

An AI-powered algorithmic trading platform that trades **Indian F&O (NSE)** and **Crypto perpetual futures (Bybit)** using the EOS (Exhaustion of Strength) contrarian strategy, with 2-tier AI validation via Claude Sonnet 4.5.

## Overview

This system implements a contrarian/mean-reversion trading strategy across two independent markets:

| Module | Market | Instrument | Data Source | API |
|--------|--------|-----------|------------|-----|
| **EOS** | Indian NSE F&O | Stock Options (CE/PE) | Real-time + Historical | Dhan API v2 |
| **CRYPTO** | Global Crypto | USDT Perpetual Futures | Real-time + Historical | Bybit API v5 |

Both modules share the same core architecture: **Data → Strategy → AI Validation → Risk Management → Execution → Portfolio Tracking**.

## Strategy: EOS (Exhaustion of Strength)

**Core Idea:** When a stock or crypto asset moves sharply in one direction, it often overshoots. We take the opposite side, expecting a reversion.

### EOS on Indian F&O
- Stock moves **UP > 2%** from previous close → Buy **PUT** option (expect drop)
- Stock moves **DOWN > 2%** from previous close → Buy **CALL** option (expect bounce)
- OI confirmation: Open Interest change > 1.75%
- Session: NSE market hours (09:15 – 15:30 IST)

### CRYPTO-EOS on Bybit Perps
- Crypto moves **DOWN > 4%** → Open **LONG** perpetual (expect bounce)
- Crypto moves **UP > 4%** → Open **SHORT** perpetual (expect retrace)
- Confirmation: Funding rate extreme (|rate| > 0.01%) OR volume spike (> 1.5× average)
- Session: Asia hours (05:30 – 14:00 IST)
- Leverage: 5× cross margin

### AI Validation (Both Modules)
Every trade signal goes through **2-tier validation**:
1. **Tier 1 — Rule-based** (<10ms): Hard filters on risk limits, duplicate checks, position caps
2. **Tier 2 — Claude AI** (~2-4s): Sends market context to Claude Sonnet 4.5 via OpenRouter, which returns APPROVE/REJECT with reasoning

### Exit Conditions (Priority Order)
1. Initial stop-loss (30% for options / 3% for crypto)
2. Trailing stop-loss
3. SMA crossover (8-SMA vs 20-SMA after 60 min hold)
4. Funding flip (crypto only)
5. Session-end force close

## Project Structure

```
AI-Hedge-Crypto/
├── README.md
├── .env                                # API credentials (git-ignored)
├── .gitignore
├── api-scrip-master.csv                # NSE instrument master data
├── dhan_expired_options.py             # Dhan expired options data pipeline
├── DHAN_EXPIRED_OPTIONS_DOCUMENTATION.md
│
├── EOS/                                # Indian F&O Module
│   ├── __init__.py
│   ├── config.py                       # Strategy params + Dhan API config
│   ├── data_fetcher.py                 # Dhan API integration
│   ├── eos_strategy_engine.py          # Signal generation
│   ├── eos_backtester.py               # Historical backtesting
│   ├── sim_engine.py                   # Synthetic backtest + paper live
│   ├── eos_live_runner.py              # Real live trading (Dhan WebSocket)
│   ├── eos_portfolio_manager.py        # SQLite portfolio tracking
│   ├── eos_risk_manager.py             # Risk management
│   ├── eos_ai_validator.py             # Claude AI trade validation
│   ├── eos_market_context.py           # Market data structures
│   ├── eos_option_chain.py             # Option chain manager
│   ├── eos_websocket_feed.py           # Dhan WebSocket feed
│   ├── eos_validator_cache.py          # AI validation caching
│   ├── eos_dashboard.py                # Unified Flask web dashboard
│   ├── run_backtest.py                 # Backtest entry point
│   ├── run_live.py                     # Live trading entry point
│   └── templates/dashboard.html        # Dashboard UI
│
├── CRYPTO/                             # Crypto Perps Module
│   ├── __init__.py
│   ├── config.py                       # Strategy params + Bybit API config
│   ├── data_fetcher.py                 # Bybit API v5 (REST + retry logic)
│   ├── strategy_engine.py              # Signal generation
│   ├── sim_engine.py                   # Synthetic backtest + paper live
│   ├── live_runner.py                  # Real live trading (Bybit WebSocket)
│   ├── portfolio_manager.py            # SQLite portfolio tracking
│   ├── risk_manager.py                 # Risk management
│   ├── ai_validator.py                 # Claude AI trade validation
│   ├── market_context.py               # Market data structures
│   ├── websocket_feed.py               # Bybit WebSocket feed
│   ├── run_crypto_backtest.py          # Backtest entry point
│   └── run_crypto_live.py              # Live trading entry point
```

## Quick Start

### Prerequisites
- Python 3.10+
- Dhan trading account (for EOS module)
- Bybit account (for CRYPTO module)
- OpenRouter API key (for AI validation)

### Installation
```bash
git clone <repo-url>
cd AI-Hedge-Crypto
pip install requests pytz python-dotenv flask websockets
```

### Configuration
Create a `.env` file in the project root:
```env
BYBIT_API_KEY=your_bybit_key
BYBIT_API_SECRET=your_bybit_secret
DHAN_ACCESS_TOKEN=your_dhan_token
DHAN_CLIENT_ID=your_dhan_client_id
OPENROUTER_API_KEY=your_openrouter_key
```

### Running

```bash
# ── EOS (Indian F&O) ──
python -m EOS.run_backtest                          # Backtest (last 30 days)
python -m EOS.run_live                              # Paper trade (live)
python -m EOS.eos_dashboard                         # Web dashboard (localhost:5000)

# ── CRYPTO (Bybit Perps) ──
python -m CRYPTO.run_crypto_backtest --days 30      # Backtest
python -m CRYPTO.run_crypto_live                    # Paper trade (live)
```

## Components

| Component | EOS (Indian F&O) | CRYPTO (Bybit Perps) |
|-----------|-------------------|---------------------|
| **Strategy Engine** | `eos_strategy_engine.py` | `strategy_engine.py` |
| **Backtester** | `eos_backtester.py` + `sim_engine.py` | `sim_engine.py` |
| **Live Runner** | `eos_live_runner.py` | `live_runner.py` |
| **Data Fetcher** | `data_fetcher.py` (Dhan API) | `data_fetcher.py` (Bybit API) |
| **AI Validator** | `eos_ai_validator.py` | `ai_validator.py` |
| **Risk Manager** | `eos_risk_manager.py` | `risk_manager.py` |
| **Portfolio** | `eos_portfolio_manager.py` | `portfolio_manager.py` |
| **WebSocket** | `eos_websocket_feed.py` | `websocket_feed.py` |

## Technology Stack

- **Language:** Python 3.10+
- **Data Sources:** Dhan API v2, Bybit API v5, Yahoo Finance
- **AI:** Claude Sonnet 4.5 via OpenRouter (2-tier validation)
- **Database:** SQLite with WAL mode
- **Dashboard:** Flask + HTML/JS (dark theme)
- **Real-time:** WebSocket feeds (Dhan + Bybit)

## Author

Shlok Nambiar
