# EOS (Exhaustion of Strength) Trading Strategy

## Complete Trading Strategy Documentation

**Version:** 1.0
**Last Updated:** February 4, 2025
**Strategy Type:** Intraday Options (Mean Reversion / Contrarian)
**Strategy Code:** EOS

---

## Table of Contents

1. [Strategy Overview](#1-strategy-overview)
2. [Core Philosophy](#2-core-philosophy)
3. [Confirmed Parameters](#3-confirmed-parameters)
4. [Entry Conditions](#4-entry-conditions)
5. [Entry Execution Rules](#5-entry-execution-rules)
6. [Exit Conditions](#6-exit-conditions)
7. [Risk Management](#7-risk-management)
8. [Trailing Stop Loss Mechanics](#8-trailing-stop-loss-mechanics)
9. [SMA Exit Logic (After 1 Hour)](#9-sma-exit-logic-after-1-hour)
10. [Day Limits & Capital Management](#10-day-limits--capital-management)
11. [Complete Trade Flow Examples](#11-complete-trade-flow-examples)
12. [Quick Reference Cheat Sheet](#12-quick-reference-cheat-sheet)
13. [Implementation Checklist](#13-implementation-checklist)

---

## 1. Strategy Overview

### What is EOS?

**EOS = Exhaustion of Strength**

The name reflects the core thesis: when a stock shows excessive strength (or weakness) with high OI buildup, it often signals exhaustion of that move, creating a reversal opportunity.

### What This Strategy Does

This is a **CONTRARIAN / MEAN REVERSION** intraday options trading strategy that:

1. **Scans** FNO stocks for significant price movement (>2%) with OI buildup (>1.75%)
2. **Bets AGAINST** the move (expects reversal)
3. **Buys PUTS** when stock is moving UP strongly
4. **Buys CALLS** when stock is moving DOWN strongly

### Why It Works (Hypothesis)

When a stock shows:
- Sharp directional move (>2% from previous close)
- Significant Open Interest buildup (>1.75% from previous day)

It often indicates:
- Over-extension / exhaustion of the move
- Potential for mean reversion
- Opportunity to profit from the pullback

---

## 2. Core Philosophy

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        EOS - CONTRARIAN LOGIC                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Stock UP >2% + OI UP >1.75%  ──────►  BUY PUTS (Expect reversal DOWN) │
│                                                                          │
│   Stock DOWN >2% + OI UP >1.75% ──────►  BUY CALLS (Expect reversal UP) │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key Insight:** We are NOT following the trend. We are betting that the strong move will reverse.

---

## 3. Confirmed Parameters

### Trading Universe & Instruments

| Parameter | Value |
|-----------|-------|
| **Universe** | FNO Stocks only (NSE F&O segment) |
| **Instrument** | Stock Options |
| **Strike Selection** | Nearest OTM (1 strike Out-of-The-Money) |
| **Expiry** | Monthly expiry |
| **Time Frame** | 10-minute candles |

### Baseline Calculations

| Metric | Baseline | Threshold |
|--------|----------|-----------|
| **Price Change %** | Previous day's closing price | > 2% |
| **OI Change %** | Previous day's closing OI | > 1.75% |
| **SMA Calculation** | Futures price (not spot, not option) | 8-SMA, 20-SMA |

### Capital & Position Sizing

| Parameter | Value |
|-----------|-------|
| **Total Capital** | ₹5,00,000 |
| **Position Size** | 1 lot per trade |
| **Max Trades/Day** | 5 trades |
| **Max Loss/Day** | ₹25,000 (5% of capital) |
| **Max Exposure** | ₹3,00,000 (60% of capital) |

### Time Rules

| Rule | Time |
|------|------|
| **Market Open** | 9:15 AM |
| **No Entry Zone** | 9:15 AM - 9:25 AM |
| **Earliest Entry** | 9:35 AM (after 9:25-9:35 candle closes) |
| **Final Exit** | 3:18 PM (mandatory) |
| **Market Close** | 3:30 PM |

---

## 4. Entry Conditions

### Primary Screening Conditions (ALL must be TRUE)

```python
# Pseudo-code for entry screening
def check_entry_conditions(stock, current_candle):
    
    # Condition 1: Must be FNO stock
    is_fno = stock in FNO_STOCK_LIST
    
    # Condition 2: OI change > 1.75% from previous day close
    oi_change_pct = (current_oi - prev_day_close_oi) / prev_day_close_oi * 100
    oi_condition = oi_change_pct > 1.75
    
    # Condition 3: Price change > 2% from previous day close
    price_change_pct = (current_price - prev_day_close) / prev_day_close * 100
    price_condition = abs(price_change_pct) > 2.0
    
    # Condition 4: Not in no-entry zone
    time_condition = current_time > "09:25"
    
    return is_fno and oi_condition and price_condition and time_condition
```

### Determining Trade Direction (THE FLIP)

```python
def get_trade_direction(price_change_pct):
    if price_change_pct > 2.0:
        # Stock is UP strongly → BUY PUTS (bet on reversal down)
        return "BUY_PUT"
    elif price_change_pct < -2.0:
        # Stock is DOWN strongly → BUY CALLS (bet on reversal up)
        return "BUY_CALL"
```

### Strike Selection Logic

```python
def select_strike(spot_price, direction, strike_gap):
    atm_strike = round(spot_price / strike_gap) * strike_gap

    if direction == "BUY_PUT":
        # Nearest OTM PUT = 1 strike BELOW ATM
        return atm_strike - strike_gap
    elif direction == "BUY_CALL":
        # Nearest OTM CALL = 1 strike ABOVE ATM
        return atm_strike + strike_gap
```

---

## 5. Entry Execution Rules

### The "Conditions Met Candle" Concept

When all entry conditions are satisfied on a particular 10-minute candle, that candle is marked as the **"Conditions Met Candle"**. We do NOT enter immediately. We wait for confirmation.

### Time Round-Off Rule

The strategy uses a time round-off system for entry:

| Conditions Met At | Wait For Candle | Enter After |
|-------------------|-----------------|-------------|
| 9:25 | 9:25-9:35 | 9:35 (if confirmed) |
| 9:30 | 9:35-9:45 | 9:45 (if confirmed) |
| 9:35 | 9:45-9:55 | 9:55 (if confirmed) |
| 9:40 | 9:45-9:55 | 9:55 (if confirmed) |
| 9:45 | 9:55-10:05 | 10:05 (if confirmed) |

**Rule:** Round to the next 10-minute boundary, then wait for that candle to close.

### Entry Confirmation (Primary Method - "a")

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ENTRY CONFIRMATION LOGIC                                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│ For BUY PUT (stock moving UP):                                          │
│   → Next candle must close ABOVE the HIGH of "Conditions Met Candle"    │
│   → This confirms the upward momentum (which we bet against)            │
│                                                                          │
│ For BUY CALL (stock moving DOWN):                                       │
│   → Next candle must close BELOW the LOW of "Conditions Met Candle"     │
│   → This confirms the downward momentum (which we bet against)          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Alternative Entry (Method "b") - If Primary Fails

If the next 10-minute candle does NOT confirm (closes in opposite direction):

1. **Wait until 10:35 AM**
2. Look for ANY candle to close above/below the **day's high/low** (up to that point)
3. If breakout occurs → Enter trade
4. If no breakout by 10:35 → **No trade for this stock today**

### Entry Flow Diagram

```
Conditions Met at 9:35
        │
        ▼
Wait for 9:35-9:45 candle to close
        │
        ├──► Closes ABOVE high (for PUT) ──► ENTER TRADE
        │
        └──► Closes BELOW high (for PUT) ──► Wait till 10:35
                                                    │
                                                    ├──► Breakout occurs ──► ENTER
                                                    │
                                                    └──► No breakout ──► NO TRADE
```

### No Entry Zone

**CRITICAL:** No entries are allowed during 9:15 AM - 9:25 AM (first 10 minutes of market).

Even if conditions are met at 9:15 or 9:20, we must wait for the 9:25 candle to close and then apply the confirmation logic.

---

## 6. Exit Conditions

### Exit Condition Timeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXIT CONDITION TIMELINE                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ENTRY ◄────── FIRST 1 HOUR ──────►│◄──── AFTER 1 HOUR ────► 3:18 PM   │
│    │                                │                            │       │
│    │  • Only 30% SL active          │  • 8-SMA exit logic        │       │
│    │  • Trailing SL active          │  • 20-SMA support/resist   │       │
│    │  • NO other exit conditions    │  • Trailing SL continues   │       │
│    │                                │  • 30% SL continues        │       │
│                                                                          │
│                                                        MANDATORY EXIT ──►│
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Exit Conditions Summary

| Condition | First 1 Hour | After 1 Hour |
|-----------|--------------|--------------|
| 30% Stop Loss | ✅ Active | ✅ Active |
| Trailing Stop Loss | ✅ Active | ✅ Active |
| 8-SMA Exit Logic | ❌ Ignored | ✅ Active |
| 20-SMA Support/Resist | ❌ Ignored | ✅ Active |
| 3:18 PM Mandatory Exit | ✅ Active | ✅ Active |

---

## 7. Risk Management

### Initial Stop Loss (30% Rule)

```python
def calculate_initial_sl(entry_price):
    """
    Stop Loss = 30% of the option purchase price
    """
    sl_amount = entry_price * 0.30
    stop_loss = entry_price - sl_amount
    return stop_loss

# Example:
# Entry Price: ₹100
# SL Amount: ₹100 × 0.30 = ₹30
# Stop Loss: ₹100 - ₹30 = ₹70
```

### Stop Loss Examples

| Entry Price | SL Amount (30%) | Stop Loss Price |
|-------------|-----------------|-----------------|
| ₹50 | ₹15 | ₹35 |
| ₹100 | ₹30 | ₹70 |
| ₹150 | ₹45 | ₹105 |
| ₹200 | ₹60 | ₹140 |
| ₹500 | ₹150 | ₹350 |

---

## 8. Trailing Stop Loss Mechanics

### The ₹10 Trailing Rule

**For every ₹10 move in option price (in favor), trail the stop loss by ₹10.**

### Trailing SL Formula

```python
def calculate_trailing_sl(entry_price, current_price, initial_sl):
    """
    Trail SL by the same amount the price has moved from entry.
    SL only moves UP, never DOWN.
    """
    price_move = current_price - entry_price

    if price_move > 0:
        # Calculate how many ₹10 increments
        trail_increments = int(price_move / 10)
        trail_amount = trail_increments * 10
        new_sl = initial_sl + trail_amount
        return max(new_sl, initial_sl)  # SL never goes down
    else:
        return initial_sl
```

### Complete Trailing SL Example

```
ENTRY: ₹100
INITIAL SL: ₹70 (30% of ₹100)

Price Movement Timeline:
─────────────────────────────────────────────────────────────────────────
Time     Price    Move from Entry    Trail Amount    New SL    Status
─────────────────────────────────────────────────────────────────────────
10:00    ₹100     ₹0                 ₹0              ₹70       Entry
10:10    ₹105     +₹5                ₹0              ₹70       No trail (< ₹10)
10:20    ₹112     +₹12               ₹10             ₹80       Trail! (+₹10)
10:30    ₹108     +₹8                ₹10             ₹80       SL stays (no decrease)
10:40    ₹120     +₹20               ₹20             ₹90       Trail! (+₹10 more)
10:50    ₹125     +₹25               ₹20             ₹90       No trail (< ₹30 total)
11:00    ₹132     +₹32               ₹30             ₹100      Trail! (Breakeven!)
11:10    ₹128     +₹28               ₹30             ₹100      SL stays
11:20    ₹145     +₹45               ₹40             ₹110      Trail! (+₹10 more)
11:30    ₹140     +₹40               ₹40             ₹110      SL stays
11:40    ₹108     -                  -               ₹110      SL HIT! Exit at ₹110
─────────────────────────────────────────────────────────────────────────
RESULT: Entry ₹100, Exit ₹110, Profit = ₹10 per unit
```

### Key Points About Trailing SL

1. **SL only moves UP**, never down
2. **Trail in ₹10 increments** based on move from entry price
3. **Continues throughout the trade** (both first hour and after)
4. **Works alongside** the 30% initial SL and 8-SMA exit logic

---

## 9. SMA Exit Logic (After 1 Hour)

### Overview

After the first 1 hour from entry, the 8-SMA and 20-SMA based exit logic becomes active. This is calculated on the **FUTURES price** of the underlying stock.

### 8-SMA Exit Rule (Conditions 10-12)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         8-SMA EXIT LOGIC                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Candle 1 closes BELOW 8-SMA ──► ALERT (Prepare for exit)               │
│           │                                                              │
│           ▼                                                              │
│  Candle 2 closes BELOW 8-SMA ──► WARNING (Exit preparation)             │
│           │                                                              │
│           ▼                                                              │
│  Candle 3 closes BELOW 8-SMA ──► EXIT TRADE                             │
│           │                                                              │
│           └──► Candle 3 closes ABOVE/ON 8-SMA ──► CONTINUE TRADE        │
│                      │                                                   │
│                      └──► Reset exit counter, keep SL active            │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8-SMA Exit Examples

**Example 1: Exit Triggered**
```
Candle 1: Futures close = 1500, 8-SMA = 1505 → BELOW SMA (Count: 1)
Candle 2: Futures close = 1498, 8-SMA = 1503 → BELOW SMA (Count: 2)
Candle 3: Futures close = 1495, 8-SMA = 1500 → BELOW SMA (Count: 3)
→ EXIT TRADE
```

**Example 2: Exit Avoided**
```
Candle 1: Futures close = 1500, 8-SMA = 1505 → BELOW SMA (Count: 1)
Candle 2: Futures close = 1498, 8-SMA = 1503 → BELOW SMA (Count: 2)
Candle 3: Futures close = 1506, 8-SMA = 1502 → ABOVE SMA (Count: RESET)
→ CONTINUE TRADE (Reset counter, keep SL active)
```

### 20-SMA Support/Resistance Rule (Conditions 13-15)

This rule is invoked when the **gap between 8-SMA and 20-SMA is small (0.01% - 0.35%)**.

```python
def check_sma_gap_condition(sma_8, sma_20):
    """
    Check if 20-SMA support/resistance rule should be invoked
    """
    gap_pct = abs(sma_8 - sma_20) / sma_20 * 100
    return 0.01 <= gap_pct <= 0.35
```

### 20-SMA Logic Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    20-SMA SUPPORT/RESISTANCE LOGIC                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  At 3rd candle (potential exit), check SMA gap:                         │
│           │                                                              │
│           ├──► Gap > 0.35% ──► Follow normal 8-SMA exit (Condition 11)  │
│           │                                                              │
│           └──► Gap 0.01% - 0.35% ──► Invoke 20-SMA rule (Condition 13)  │
│                      │                                                   │
│                      ▼                                                   │
│              Price takes support at 20-SMA?                              │
│                      │                                                   │
│                      ├──► YES ──► CONTINUE TRADE (SL active)            │
│                      │                                                   │
│                      └──► NO ──► EXIT TRADE                             │
│                                                                          │
│  If trade continues and moves ABOVE 20-SMA without exit:                │
│  ──► Continue until trailing SL hits OR 3:18 PM exit                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Price Position Scenarios (After 1 Hour)

| Futures Price Position | Action |
|------------------------|--------|
| Above 8-SMA | Continue trade, all good |
| Between 8-SMA and 20-SMA | Wait and watch, keep SL active |
| At/Near 20-SMA (support) | If SMA gap is 0.01-0.35%, continue trade |
| Below 20-SMA | Likely exit (unless reversal) |

---

## 10. Day Limits & Capital Management

### Capital Allocation

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      CAPITAL ALLOCATION                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Total Capital: ₹5,00,000                                               │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Max Exposure (60%)           │  ₹3,00,000                      │    │
│  ├───────────────────────────────┼─────────────────────────────────┤    │
│  │  Reserved Buffer (40%)        │  ₹2,00,000                      │    │
│  └───────────────────────────────┴─────────────────────────────────┘    │
│                                                                          │
│  Per Trade: 1 Lot (regardless of stock)                                 │
│  Max Trades: 5 per day                                                  │
│  Max Loss: ₹25,000 per day (5% of capital)                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Day Limit Rules

| Limit | Value | Action When Hit |
|-------|-------|-----------------|
| **Max Trades** | 5 | No new entries for the day |
| **Max Loss** | ₹25,000 | Stop trading, exit all positions |
| **Max Exposure** | ₹3,00,000 | No new entries until exposure reduces |

### Position Sizing

```python
def get_position_size(stock_symbol):
    """
    Always trade 1 lot regardless of stock
    """
    return 1  # 1 lot

def check_can_enter_trade(current_exposure, current_loss, trade_count):
    """
    Check if new trade is allowed
    """
    if trade_count >= 5:
        return False, "Max trades reached (5)"

    if current_loss >= 25000:
        return False, "Max daily loss reached (₹25,000)"

    if current_exposure >= 300000:
        return False, "Max exposure reached (₹3,00,000)"

    return True, "Trade allowed"
```

### Re-Entry Rule

**CRITICAL: NO RE-ENTRY ALLOWED**

Once a stock is traded (whether profit or loss), that stock is **BLOCKED for the rest of the day**. No re-entry under any circumstances.

```python
traded_stocks_today = set()

def can_trade_stock(stock_symbol):
    if stock_symbol in traded_stocks_today:
        return False, "Stock already traded today - no re-entry"
    return True, "Stock available for trading"

def mark_stock_traded(stock_symbol):
    traded_stocks_today.add(stock_symbol)
```

---

## 11. Complete Trade Flow Examples

### Example 1: Successful PUT Trade (Stock Moving UP)

```
═══════════════════════════════════════════════════════════════════════════
TRADE EXAMPLE 1: RELIANCE - Successful PUT Trade
═══════════════════════════════════════════════════════════════════════════

PREVIOUS DAY DATA:
- Closing Price: ₹2,400
- Closing OI: 10,00,000 contracts

CURRENT DAY:
─────────────────────────────────────────────────────────────────────────
09:25 Candle Close:
- Current Price: ₹2,460 (+2.5% from prev close) ✓
- Current OI: 10,20,000 (+2.0% from prev close) ✓
- CONDITIONS MET! Mark as "Conditions Met Candle"
- High of this candle: ₹2,465
- Direction: Stock UP → BUY PUT

09:35 Candle Close:
- Close: ₹2,470 (ABOVE ₹2,465 high) ✓
- CONFIRMATION! Enter trade

ENTRY:
- Stock: RELIANCE
- Direction: BUY PUT
- Strike: ₹2,450 PUT (Nearest OTM - 1 strike below ATM of ₹2,500)
- Expiry: Monthly
- Entry Price: ₹80
- Lots: 1 (250 shares)
- Investment: ₹80 × 250 = ₹20,000
- Initial SL: ₹80 × 0.70 = ₹56

TRADE PROGRESSION:
─────────────────────────────────────────────────────────────────────────
Time     Option Price    Move    Trailing SL    Status
─────────────────────────────────────────────────────────────────────────
09:35    ₹80            -       ₹56            Entry
09:45    ₹85            +₹5     ₹56            Holding
09:55    ₹92            +₹12    ₹66            Trail (+₹10)
10:05    ₹88            +₹8     ₹66            SL holds
10:15    ₹95            +₹15    ₹66            Holding
10:25    ₹102           +₹22    ₹76            Trail (+₹10)
10:35    ₹110           +₹30    ₹86            Trail (+₹10) - 1 hour mark
─────────────────────────────────────────────────────────────────────────
After 1 hour: 8-SMA exit logic now active

10:45    ₹115           +₹35    ₹86            Above 8-SMA ✓
10:55    ₹108           +₹28    ₹86            Above 8-SMA ✓
11:05    ₹120           +₹40    ₹96            Trail (+₹10), Above 8-SMA ✓
11:15    ₹118           +₹38    ₹96            Above 8-SMA ✓
11:25    ₹125           +₹45    ₹96            Above 8-SMA ✓
11:35    ₹132           +₹52    ₹106           Trail (+₹10), Above 8-SMA ✓
11:45    ₹128           +₹48    ₹106           Above 8-SMA ✓
11:55    ₹105           -       ₹106           SL HIT! Exit at ₹106
─────────────────────────────────────────────────────────────────────────

RESULT:
- Entry: ₹80
- Exit: ₹106 (Trailing SL hit)
- Profit per unit: ₹26
- Total Profit: ₹26 × 250 = ₹6,500
- ROI: 32.5%
═══════════════════════════════════════════════════════════════════════════
```

### Example 2: CALL Trade with 8-SMA Exit

```
═══════════════════════════════════════════════════════════════════════════
TRADE EXAMPLE 2: HDFCBANK - CALL Trade with 8-SMA Exit
═══════════════════════════════════════════════════════════════════════════

PREVIOUS DAY DATA:
- Closing Price: ₹1,600
- Closing OI: 8,00,000 contracts

CURRENT DAY:
─────────────────────────────────────────────────────────────────────────
09:35 Candle Close:
- Current Price: ₹1,560 (-2.5% from prev close) ✓
- Current OI: 8,20,000 (+2.5% from prev close) ✓
- CONDITIONS MET!
- Low of this candle: ₹1,555
- Direction: Stock DOWN → BUY CALL

09:45 Candle Close:
- Close: ₹1,550 (BELOW ₹1,555 low) ✓
- CONFIRMATION! Enter trade

ENTRY:
- Stock: HDFCBANK
- Direction: BUY CALL
- Strike: ₹1,560 CALL (Nearest OTM - 1 strike above ATM of ₹1,550)
- Entry Price: ₹45
- Lots: 1 (550 shares)
- Investment: ₹45 × 550 = ₹24,750
- Initial SL: ₹45 × 0.70 = ₹31.50

TRADE PROGRESSION (After 1 hour - 8-SMA Exit):
─────────────────────────────────────────────────────────────────────────
Time     Option    Futures    8-SMA      Position vs SMA    Action
─────────────────────────────────────────────────────────────────────────
10:45    ₹52       ₹1,565     ₹1,560     ABOVE              Continue
10:55    ₹48       ₹1,558     ₹1,561     BELOW              Count: 1
11:05    ₹45       ₹1,555     ₹1,560     BELOW              Count: 2
11:15    ₹42       ₹1,552     ₹1,558     BELOW              Count: 3 → EXIT!
─────────────────────────────────────────────────────────────────────────

RESULT:
- Entry: ₹45
- Exit: ₹42 (8-SMA exit after 3 candles below)
- Loss per unit: ₹3
- Total Loss: ₹3 × 550 = ₹1,650
- ROI: -6.7%
═══════════════════════════════════════════════════════════════════════════
```

### Example 3: Stop Loss Hit (First Hour)

```
═══════════════════════════════════════════════════════════════════════════
TRADE EXAMPLE 3: TCS - Stop Loss Hit in First Hour
═══════════════════════════════════════════════════════════════════════════

ENTRY at 09:45:
- Direction: BUY PUT (stock was UP >2%)
- Entry Price: ₹120
- Initial SL: ₹84 (30% of ₹120)

TRADE PROGRESSION:
─────────────────────────────────────────────────────────────────────────
Time     Option Price    Trailing SL    Status
─────────────────────────────────────────────────────────────────────────
09:45    ₹120           ₹84            Entry
09:55    ₹115           ₹84            Holding (no trail, price down)
10:05    ₹105           ₹84            Holding
10:15    ₹95            ₹84            Holding
10:25    ₹82            ₹84            SL HIT! Exit at ₹84
─────────────────────────────────────────────────────────────────────────

RESULT:
- Entry: ₹120
- Exit: ₹84 (30% SL hit)
- Loss per unit: ₹36
- Total Loss: ₹36 × lot_size
- ROI: -30%

NOTE: 8-SMA exit was NOT active (within first hour), only 30% SL was active.
═══════════════════════════════════════════════════════════════════════════
```

---

## 12. Quick Reference Cheat Sheet

### Entry Checklist

```
□ Is it an FNO stock?
□ Is OI change > 1.75% from previous day close?
□ Is Price change > 2% from previous day close?
□ Is current time after 9:25 AM?
□ Has the "Conditions Met Candle" been identified?
□ Has the next candle confirmed (closed above high / below low)?
□ Is this stock NOT already traded today?
□ Is trade count < 5 for the day?
□ Is daily loss < ₹25,000?
□ Is current exposure < ₹3,00,000?
```

### Direction Quick Reference

| Stock Movement | OI Change | Action |
|----------------|-----------|--------|
| UP > 2% | UP > 1.75% | BUY PUT (Nearest OTM) |
| DOWN > 2% | UP > 1.75% | BUY CALL (Nearest OTM) |

### Exit Quick Reference

| Condition | First Hour | After 1 Hour |
|-----------|------------|--------------|
| 30% SL | ✅ EXIT | ✅ EXIT |
| Trailing SL Hit | ✅ EXIT | ✅ EXIT |
| 3 candles below 8-SMA | ❌ IGNORE | ✅ EXIT |
| 3:18 PM | ✅ EXIT | ✅ EXIT |

### Key Numbers to Remember

| Parameter | Value |
|-----------|-------|
| Price Change Threshold | > 2% |
| OI Change Threshold | > 1.75% |
| Initial Stop Loss | 30% of entry |
| Trailing Increment | ₹10 |
| SMA for Exit | 8-SMA on Futures |
| SMA for Support | 20-SMA on Futures |
| SMA Gap for Rule 13 | 0.01% - 0.35% |
| Max Trades/Day | 5 |
| Max Loss/Day | ₹25,000 |
| Final Exit Time | 3:18 PM |
| No Entry Zone | 9:15 - 9:25 AM |

---

## 13. Implementation Checklist

### Data Requirements

```
□ Previous day closing prices for all FNO stocks
□ Previous day closing OI for all FNO stocks
□ Real-time 10-minute candle data (OHLC)
□ Real-time OI data
□ Futures price data for SMA calculation
□ Option chain data for strike selection
□ Lot size information for all FNO stocks
```

### Calculations Needed

```
□ Price change % = (Current Price - Prev Close) / Prev Close × 100
□ OI change % = (Current OI - Prev Day OI) / Prev Day OI × 100
□ 8-SMA on Futures price (10-min candles)
□ 20-SMA on Futures price (10-min candles)
□ ATM strike calculation
□ Nearest OTM strike selection
□ Trailing SL calculation
```

### System Components

```
□ Stock screener (FNO universe)
□ Condition checker (Price + OI thresholds)
□ Entry signal generator
□ Position manager
□ Stop loss manager (Initial + Trailing)
□ SMA calculator and exit logic
□ Trade logger
□ Day limit tracker
□ P&L calculator
```

---

## Appendix A: FNO Stock List

The strategy applies to all stocks in the NSE F&O segment. As of 2025, this includes approximately 180+ stocks. Key stocks include:

**Banking & Finance:**
HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK, INDUSINDBK, BANKBARODA, PNB, IDFCFIRSTB, BANDHANBNK

**IT:**
TCS, INFY, WIPRO, HCLTECH, TECHM, LTIM, MPHASIS, COFORGE, PERSISTENT

**Auto:**
MARUTI, TATAMOTORS, M&M, BAJAJ-AUTO, HEROMOTOCO, EICHERMOT, ASHOKLEY, TVSMOTOR

**Pharma:**
SUNPHARMA, DRREDDY, CIPLA, DIVISLAB, APOLLOHOSP, BIOCON, AUROPHARMA

**Energy:**
RELIANCE, ONGC, BPCL, IOC, GAIL, NTPC, POWERGRID, TATAPOWER, ADANIGREEN

**Metals:**
TATASTEEL, HINDALCO, JSWSTEEL, VEDL, COALINDIA, NMDC

**FMCG:**
HINDUNILVR, ITC, NESTLEIND, BRITANNIA, DABUR, MARICO, COLPAL, GODREJCP

**Others:**
LT, TITAN, BAJFINANCE, BAJAJFINSV, ADANIENT, ADANIPORTS, ULTRACEMCO, GRASIM, SHREECEM

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **FNO** | Futures & Options segment of NSE |
| **OI** | Open Interest - total outstanding derivative contracts |
| **ATM** | At-The-Money - strike closest to current price |
| **OTM** | Out-of-The-Money - strike away from current price |
| **SMA** | Simple Moving Average |
| **Trailing SL** | Stop loss that moves with price in favorable direction |
| **Conditions Met Candle** | The candle where all entry conditions are satisfied |
| **Flip** | Taking opposite position to the market direction |

---

## Appendix C: Formula Reference

### Price Change Calculation
```
Price Change % = ((Current Price - Previous Day Close) / Previous Day Close) × 100
```

### OI Change Calculation
```
OI Change % = ((Current OI - Previous Day Close OI) / Previous Day Close OI) × 100
```

### Initial Stop Loss
```
Stop Loss Price = Entry Price × (1 - 0.30) = Entry Price × 0.70
```

### Trailing Stop Loss
```
Trail Amount = floor((Current Price - Entry Price) / 10) × 10
New SL = Initial SL + Trail Amount
Final SL = max(New SL, Previous SL)  # Never decrease
```

### SMA Gap Percentage
```
SMA Gap % = |8-SMA - 20-SMA| / 20-SMA × 100
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-02-04 | Initial documentation |

---

**Document End**

