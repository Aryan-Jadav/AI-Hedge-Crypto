> [!NOTE]
> Last updated: 2026-02-21

# EOS Strategy - MANDATORY RULES

## ⚠️ CRITICAL RULES - DO NOT VIOLATE

### 1. NEVER CHANGE THE STRATEGY PARAMETERS

```
❌ DO NOT modify these values under ANY condition:
   - price_change_threshold_pct: 2.0%
   - oi_change_threshold_pct: 1.75%
   - initial_stop_loss_pct: 30%
   - trailing_stop_loss_amount: ₹10
   - time_exit: 3:18 PM
```

The strategy has been designed and tested with these specific parameters. Changing them invalidates all backtesting results.

### 2. NEVER OVERRIDE ENTRY CONDITIONS

```
❌ DO NOT enter trades that don't meet ALL conditions:
   - Price change > 2% from previous close
   - OI change > 1.75% from previous day
   - Time between 9:15 AM and 2:30 PM
```

### 3. NEVER SKIP EXIT RULES

```
❌ DO NOT hold positions past 3:18 PM
❌ DO NOT ignore stop loss triggers
❌ DO NOT disable trailing stop loss
❌ DO NOT exceed ₹5,000 daily loss limit
```

### 4. NEVER MODIFY SIGNAL LOGIC

```
❌ DO NOT change:
   - Price UP → BUY PUT
   - Price DOWN → BUY CALL
   
This is the core contrarian logic. Reversing it defeats the strategy purpose.
```

---

## ✅ ALLOWED MODIFICATIONS

### For Testing Only (Revert After)

```
✅ Temporarily lower thresholds to verify backtester works
✅ Change date ranges for different test periods
✅ Test different symbols from FNO universe
✅ Adjust slippage/commission for realistic simulation
```

### For Code Improvements

```
✅ Add more logging/debugging output
✅ Improve error handling
✅ Add new metrics to backtest results
✅ Optimize API calls for performance
✅ Add data caching to reduce API usage
```

---

## 📋 BACKTEST CHECKLIST

Before running any backtest, verify:

- [ ] Strategy parameters are at default values
- [ ] Using valid date range (expired options available)
- [ ] Using valid symbols from FNO_STOCKS list
- [ ] expiry_code is appropriate (1=last month, 2=2nd last)
- [ ] Virtual environment is activated
- [ ] API credentials are valid

---

## 🚫 COMMON MISTAKES TO AVOID

| Mistake | Why It's Wrong |
|---------|----------------|
| Lowering 2% threshold | Generates false signals, not exhaustion |
| Removing OI condition | OI confirms institutional activity |
| Holding past 3:18 PM | Overnight risk, theta decay |
| Ignoring stop loss | Can lead to catastrophic losses |
| Testing on live data | Use expired options only for backtesting |
| Changing PUT/CALL logic | Destroys contrarian edge |

---

## 📊 INTERPRETING RESULTS

### Good Backtest Signs
- Win rate > 45%
- Profit factor > 1.2
- Sharpe ratio > 1.0
- Max drawdown < 20% of capital

### Warning Signs
- Win rate < 30% (strategy may not suit current market)
- Profit factor < 0.8 (losing money consistently)
- All exits are TIME_EXIT (SL/trailing not triggering)
- Very few trades (market too calm for EOS)

---

## 🔒 VERSION CONTROL

When making ANY changes to strategy files:

1. Document the change with date and reason
2. Test thoroughly before committing
3. Keep backup of original parameters
4. Never push untested changes to production

---

## 📞 ESCALATION

If you encounter:
- Unexpected backtest results → Review data quality first
- API errors → Check rate limits and credentials
- Strategy questions → Refer to EOS_STRATEGY_DOCUMENTATION.md

**Remember: The strategy is the strategy. Trust the process.**

