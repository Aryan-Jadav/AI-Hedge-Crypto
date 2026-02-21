> [!NOTE]
> Last updated: 2026-02-21

# EOS Strategy - Exhaustion of Strength

A contrarian/mean reversion options trading strategy that identifies exhaustion points in price movements.

## Quick Start

### Running a Backtest

```python
from EOS import EOSBacktester

# Initialize
backtester = EOSBacktester()

# Run backtest
result = backtester.run_backtest(
    symbols=['RELIANCE'],           # Stock symbols to test
    start_date='2025-12-01',        # Start date (YYYY-MM-DD)
    end_date='2025-12-31',          # End date (YYYY-MM-DD)
    expiry_code=1                   # 1=last month, 2=2nd last month
)

# Print results
backtester.print_summary(result)
```

### With AI Validation (Optional)

```python
from EOS import EOSBacktester, EOSAIValidator, SignalData

OPENROUTER_API_KEY = "your_api_key"

backtester = EOSBacktester()
validator = EOSAIValidator(api_key=OPENROUTER_API_KEY)

result = backtester.run_backtest(['RELIANCE'], '2025-12-01', '2025-12-31', 1)

for trade in result.trades:
    signal = SignalData(
        symbol=trade.symbol,
        signal_type=trade.option_type,
        entry_price=trade.entry_price,
        stop_loss=trade.entry_price * 0.7,
        price_change_pct=trade.price_change_at_entry,
        oi_change_pct=trade.oi_change_at_entry,
        entry_date=trade.entry_date,
        entry_time=trade.entry_time,
        lot_size=trade.lot_size
    )
    validation = validator.validate(signal)
    print(f"{trade.symbol}: {validation.result.value} - {validation.reason}")
```

### Terminal Command

```bash
source venv/bin/activate
python -c "
from EOS import EOSBacktester
backtester = EOSBacktester()
result = backtester.run_backtest(['RELIANCE'], '2025-12-01', '2025-12-31', 1)
backtester.print_summary(result)
"
```

## Strategy Logic

### Entry Conditions (ALL must be met)
| Condition | Threshold |
|-----------|-----------|
| Price Change from Previous Close | > 2.0% |
| OI Change from Previous Day | > 1.75% |
| Time Window | 9:15 AM - 2:30 PM |

### Signal Direction
- **Price UP > 2%** → BUY PUT (expect reversal down)
- **Price DOWN > 2%** → BUY CALL (expect reversal up)

### Exit Conditions (First one triggered)
| Exit Type | Condition |
|-----------|-----------|
| Initial Stop Loss | 30% below entry |
| Trailing Stop Loss | ₹10 below highest price (after profit) |
| SMA Crossover | 8-SMA crosses below 20-SMA (after 1 hour) |
| Time Exit | 3:18 PM (mandatory) |
| Max Daily Loss | ₹5,000 per day |

## Available Stocks (FNO Universe)

```
RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, AXISBANK, KOTAKBANK,
BAJFINANCE, TATAMOTORS, MARUTI, SUNPHARMA, TITAN, WIPRO, LTIM,
HCLTECH, ADANIENT, TATASTEEL, JSWSTEEL, HINDALCO
```

## Configuration

Edit `EOS/config.py` to modify:
- `price_change_threshold_pct`: Entry threshold (default: 2.0%)
- `oi_change_threshold_pct`: OI threshold (default: 1.75%)
- `initial_stop_loss_pct`: Stop loss (default: 30%)
- `trailing_stop_loss_amount`: Trailing SL (default: ₹10)

## Output Metrics

| Metric | Description |
|--------|-------------|
| Win Rate | % of profitable trades |
| Total P&L | Net profit/loss in ₹ |
| Profit Factor | Gross profit / Gross loss |
| Max Drawdown | Largest peak-to-trough decline |
| Sharpe Ratio | Risk-adjusted return |
| Avg Hold Time | Average trade duration |

## Components

| Component | File | Description |
|-----------|------|-------------|
| **EOSBacktester** | `eos_backtester.py` | Backtesting framework with trade simulation |
| **EOSStrategyEngine** | `eos_strategy_engine.py` | Signal generation logic |
| **EOSRiskManager** | `eos_risk_manager.py` | Position sizing, daily loss limits |
| **EOSPortfolioManager** | `eos_portfolio_manager.py` | SQLite database for trade history |
| **EOSAIValidator** | `eos_ai_validator.py` | Claude-powered trade validation |
| **EOSDataFetcher** | `data_fetcher.py` | Dhan API integration |

## File Structure

```
EOS/
├── __init__.py                    # Package exports
├── config.py                      # Strategy configuration & API keys
├── data_fetcher.py                # Dhan API integration
├── eos_strategy_engine.py         # Signal generation
├── eos_backtester.py              # Backtesting framework (688 lines)
├── eos_portfolio_manager.py       # SQLite portfolio tracking (856 lines)
├── eos_risk_manager.py            # Risk management (406 lines)
├── eos_ai_validator.py            # AI trade validation (412 lines)
├── eos_market_context.py          # Market data structures (230 lines)
├── eos_validator_cache.py         # Caching layer (110 lines)
├── README.md                      # This file
├── RULES.md                       # Mandatory strategy rules
└── EOS_STRATEGY_DOCUMENTATION.md  # Full documentation (878 lines)
```

## AI Validator

The AI Validator uses Claude Sonnet 4.5 to provide intelligent trade validation:

- **Tier 1**: Quick rule-based checks (FREE, <10ms)
  - VIX > 25 rejection (only if real VIX data provided)
  - Early morning rejection (9:15-9:20)
  - Low premium rejection (< ₹1)
- **Tier 2**: Claude AI validation (~3000ms, ~450 tokens)
  - Analyzes signal strength, sector context
  - Returns APPROVE or REJECT with confidence score

**Important**: The AI validator only uses REAL market data - no fake/simulated data.

## Important Notes

1. **Data Availability**: Expired options data available for past 5 years
2. **Rate Limits**: Dhan API has rate limits - backtester handles automatically
3. **Market Hours**: Strategy trades 9:15 AM - 3:30 PM IST
4. **No Fake Data**: System uses only authentic API data - no placeholders

See `RULES.md` for mandatory rules when running backtests.

