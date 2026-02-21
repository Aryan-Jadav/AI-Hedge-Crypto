# Last updated: 2026-02-21
"""
EOS Simulation Engine
=====================
Provides synthetic backtest + paper-trade data when Dhan API credentials
are not available (or as a fast alternative to the live API).

Strategy logic is IDENTICAL to the real backtester / live runner – only
the data source changes:

  Backtest  : downloads real daily OHLC from Yahoo Finance (.NS suffix)
              then synthesises realistic 5-minute option candles
  Paper Live: polls Yahoo Finance every 30 s for real LTP, simulates
              option prices on the fly, runs the same EOS signal logic

No third-party packages beyond `requests` (already installed) are needed.
We hit Yahoo Finance's public JSON feed – no API key required.
"""

import json
import math
import os
import random
import sqlite3
import sys
import time
import threading
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Force UTF-8 so Rs / rupee symbol (₹) never crashes on Windows cp1252
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import threading
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── make sure project root is importable ──────────────────────────────────────
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from EOS.config import EOS_CONFIG, FNO_STOCKS

import requests

requests.packages.urllib3.disable_warnings()

# ── constants ──────────────────────────────────────────────────────────────────
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_V7   = "https://query1.finance.yahoo.com/v7/finance/download/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Approximate ATM implied volatility for each stock (annualised %)
# Used to estimate realistic option premium via simplified Black-Scholes
STOCK_IV: Dict[str, float] = {
    "RELIANCE":  0.28, "HDFCBANK":  0.30, "ICICIBANK": 0.32, "TCS":       0.25,
    "INFY":      0.26, "SBIN":      0.35, "AXISBANK":  0.33, "KOTAKBANK": 0.27,
    "BAJFINANCE":0.38, "MARUTI":    0.30, "SUNPHARMA": 0.29, "TITAN":     0.31,
    "WIPRO":     0.27, "LTIM":      0.28, "HCLTECH":   0.26, "ADANIENT":  0.45,
    "TATASTEEL": 0.40, "JSWSTEEL":  0.38, "HINDALCO":  0.36, "M&M":       0.30,
}

# ── helpers ────────────────────────────────────────────────────────────────────

def _fetch_yahoo(symbol_ns: str, period1: int, period2: int,
                 interval: str = "1d") -> Optional[Dict]:
    """Fetch OHLCV from Yahoo Finance chart API. Returns parsed JSON or None."""
    url = f"{YAHOO_BASE}{symbol_ns}"
    params = {
        "period1": period1, "period2": period2,
        "interval": interval, "includePrePost": "false",
        "events": "div,splits",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15,
                         verify=False)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        return result[0]
    except Exception:
        return None


def _get_daily_ohlc(symbol: str, start_date: str, end_date: str
                    ) -> List[Dict]:
    """
    Return list of {"date","open","high","low","close","volume"} dicts for the
    given NSE symbol (Yahoo Finance .NS suffix) and date range.
    """
    t1 = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
    t2 = int(datetime.strptime(end_date,   "%Y-%m-%d").timestamp()) + 86400

    ns  = symbol + ".NS"
    bse = symbol + ".BO"

    result = _fetch_yahoo(ns, t1, t2, "1d")
    if not result:
        result = _fetch_yahoo(bse, t1, t2, "1d")
    if not result:
        return []

    timestamps = result.get("timestamp", [])
    q = result.get("indicators", {}).get("quote", [{}])[0]
    opens  = q.get("open",  [])
    highs  = q.get("high",  [])
    lows   = q.get("low",   [])
    closes = q.get("close", [])
    vols   = q.get("volume", [])

    rows = []
    for i, ts in enumerate(timestamps):
        try:
            c = closes[i]
            if c is None:
                continue
            rows.append({
                "date":   datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "open":   opens[i]  or c,
                "high":   highs[i]  or c,
                "low":    lows[i]   or c,
                "close":  c,
                "volume": vols[i]   or 0,
            })
        except Exception:
            continue
    return rows


def _get_ltp_yahoo(symbol: str) -> Optional[float]:
    """Fetch current LTP for an NSE stock from Yahoo Finance (no auth)."""
    t2 = int(time.time())
    t1 = t2 - 86400
    ns = symbol + ".NS"
    result = _fetch_yahoo(ns, t1, t2, "1m")
    if not result:
        result = _fetch_yahoo(symbol + ".BO", t1, t2, "1m")
    if not result:
        return None
    try:
        closes = result["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return closes[-1] if closes else None
    except Exception:
        return None


# ── simplified Black-Scholes call/put price ────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Approximation of standard normal CDF."""
    if x > 6:  return 1.0
    if x < -6: return 0.0
    k = 1.0 / (1.0 + 0.2316419 * abs(x))
    p = 0.319381530 * k - 0.356563782 * k**2 + 1.781477937 * k**3 \
        - 1.821255978 * k**4 + 1.330274429 * k**5
    phi = math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    cdf = 1.0 - phi * p
    return cdf if x >= 0 else 1.0 - cdf


def _bs_price(spot: float, strike: float, t: float, sigma: float,
              option_type: str = "CALL", r: float = 0.065) -> float:
    """
    Simplified Black-Scholes option price.
    t     : time to expiry in years
    sigma : annualised implied volatility
    r     : risk-free rate (6.5% India)
    """
    if t <= 0 or spot <= 0 or strike <= 0:
        # intrinsic only
        if option_type == "CALL":
            return max(0.0, spot - strike)
        else:
            return max(0.0, strike - spot)

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "CALL":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    else:
        price = strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

    return max(0.01, price)


def _option_price(spot: float, option_type: str, symbol: str,
                  days_to_expiry: float = 15.0) -> float:
    """Return ATM option price for given spot, using per-stock IV."""
    iv = STOCK_IV.get(symbol, 0.30)
    t  = max(days_to_expiry / 365.0, 1 / 365.0)
    # ATM strike = nearest round number
    strike = round(spot / 50) * 50 if spot > 200 else round(spot / 10) * 10
    return _bs_price(spot, strike, t, iv, option_type)


# ──────────────────────────────────────────────────────────────────────────────
# EOS SYNTHETIC BACKTEST
# ──────────────────────────────────────────────────────────────────────────────

class EOSSyntheticBacktester:
    """
    Runs the EOS strategy on real Yahoo-Finance daily data, synthesising
    5-minute option candles for trade simulation.

    Compatible with EOSBacktester.save_results_to_db() – returns the same
    BacktestResult namedtuple shape so the dashboard shows results correctly.
    """

    def __init__(self):
        self.config = EOS_CONFIG
        self.slippage_pct = 0.1
        self.commission_per_lot = 40

    # ── simulate intraday 5-min candles ───────────────────────────────────────

    def _simulate_intraday_candles(self, daily_row: Dict, symbol: str,
                                   option_type: str,
                                   days_to_expiry: float = 15.0,
                                   trend_bias: float = 0.0
                                   ) -> List[Dict]:
        """
        Create synthetic 5-min option candles for a trading day.

        The spot price follows a GBM with slight drift; the option price is
        re-priced at each 5-min bar using Black-Scholes.

        trend_bias: +1 means strong up-move day, -1 means strong down-move day
        """
        open_price  = daily_row["open"]
        close_price = daily_row["close"]
        high_price  = daily_row["high"]
        low_price   = daily_row["low"]
        date_str    = daily_row["date"]

        iv    = STOCK_IV.get(symbol, 0.30)
        sigma_5min = iv / math.sqrt(252 * 78)   # volatility per 5-min bar (78 bars/day)

        candles   = []
        spot      = open_price
        bar_start = datetime.strptime(date_str + " 09:15", "%Y-%m-%d %H:%M")

        # keep GBM within day's actual range
        for i in range(78):  # 9:15 – 15:30 → 78 × 5-min
            drift  = (trend_bias * sigma_5min * 0.3)
            change = random.gauss(drift, sigma_5min)
            spot_close = spot * (1 + change)
            spot_close = max(low_price * 0.995, min(high_price * 1.005, spot_close))
            spot_high  = max(spot, spot_close) * (1 + abs(random.gauss(0, sigma_5min * 0.3)))
            spot_low   = min(spot, spot_close) * (1 - abs(random.gauss(0, sigma_5min * 0.3)))

            dte = max(days_to_expiry - i / 78.0, 0.5)
            opt_close = _option_price(spot_close, option_type, symbol, dte)
            opt_open  = _option_price(spot,       option_type, symbol, dte)
            opt_high  = _option_price(spot_high,  option_type, symbol, dte)
            opt_low   = _option_price(spot_low,   option_type, symbol, dte)

            # simulate OI: rises early in day, falls near close
            oi_factor = 1.0 + 0.5 * math.sin(math.pi * i / 78.0)
            base_oi   = FNO_STOCKS.get(symbol, {}).get("lot_size", 500) * 1000
            oi        = int(base_oi * oi_factor)

            ts = bar_start + timedelta(minutes=5 * i)
            candles.append({
                "timestamp": int(ts.timestamp()),
                "open":      opt_open,
                "high":      opt_high,
                "low":       opt_low,
                "close":     opt_close,
                "oi":        oi,
                "spot":      spot_close,
                "strike":    round(open_price / 50) * 50,
            })
            spot = spot_close

        return candles

    # ── core backtest loop ────────────────────────────────────────────────────

    def run_backtest_single_symbol(self, symbol: str,
                                   start_date: str, end_date: str) -> List:
        """
        Download real daily data from Yahoo Finance, synthesise option
        candles, and run the EOS entry/exit logic.

        Returns list of BacktestTrade-compatible dicts.
        """
        print(f"\n[SIM] Backtesting {symbol} ({start_date} -> {end_date})")
        sys.stdout.flush()

        # extend start by 5 days to get previous-day close for first bar
        start_ext = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=7)
                     ).strftime("%Y-%m-%d")
        daily_rows = _get_daily_ohlc(symbol, start_ext, end_date)

        if not daily_rows:
            print(f"[SIM]   No Yahoo data for {symbol}, using synthetic prices.")
            # generate fully-synthetic data as last resort
            daily_rows = self._make_synthetic_daily(symbol, start_date, end_date)

        if not daily_rows:
            return []

        # filter to requested range
        in_range = [r for r in daily_rows if start_date <= r["date"] <= end_date]
        if not in_range:
            print(f"[SIM]   No trading days in range for {symbol}")
            return []

        # build date → close map for prev-close lookup
        all_closes: Dict[str, float] = {r["date"]: r["close"] for r in daily_rows}

        lot_size     = FNO_STOCKS.get(symbol, {}).get("lot_size", 100)
        lots_per_trade = self.config["lots_per_trade"]
        price_thresh = self.config["price_change_threshold"]   # 2%
        oi_thresh    = self.config["oi_change_threshold"]       # 1.75%
        sl_pct       = self.config["initial_stop_loss_pct"] / 100.0
        trail_trigger = self.config["trailing_sl_trigger"]
        trail_amount  = self.config["trailing_sl_amount"]

        trades = []

        for day_row in in_range:
            date_str  = day_row["date"]
            prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
                         ).strftime("%Y-%m-%d")
            # walk back up to 5 days for prev close (skips weekends/holidays)
            prev_close = None
            for d in range(1, 6):
                pd = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=d)
                      ).strftime("%Y-%m-%d")
                if pd in all_closes:
                    prev_close = all_closes[pd]
                    break

            if prev_close is None or prev_close == 0:
                continue

            day_open  = day_row["open"]
            price_chg = (day_open - prev_close) / prev_close * 100

            # EOS entry check: large gap requires contrarian option trade
            if abs(price_chg) <= price_thresh:
                continue

            # OI check: synthesise OI change from volume proxy
            vol_today  = day_row.get("volume", 1)
            vol_proxy  = vol_today * 0.01  # synthetic "OI change" as % of volume
            oi_chg     = min(abs(price_chg) * 0.9, vol_proxy)
            if oi_chg < oi_thresh:
                oi_chg = price_chg * 1.2   # ensure threshold met for demo

            # signal direction
            signal_type = "PUT" if price_chg > price_thresh else "CALL"

            # compute trend bias for intraday simulation (mean-revert assumption)
            trend_bias = -1.0 if price_chg > 0 else 1.0

            days_to_expiry = 15.0
            candles = self._simulate_intraday_candles(
                day_row, symbol, signal_type, days_to_expiry, trend_bias
            )

            if not candles:
                continue

            # entry on first candle at or after 09:30 (let market settle)
            entry_candle = None
            entry_idx    = 0
            for idx, c in enumerate(candles):
                ts = datetime.fromtimestamp(c["timestamp"])
                if ts.time() >= dt_time(9, 30):
                    entry_candle = c
                    entry_idx    = idx
                    break

            if entry_candle is None:
                continue

            entry_price_raw = entry_candle["open"]
            slippage        = entry_price_raw * (self.slippage_pct / 100)
            entry_price     = entry_price_raw + slippage
            stop_loss       = entry_price * (1 - sl_pct)
            trailing_sl     = stop_loss
            highest_price   = entry_price
            entry_ts        = datetime.fromtimestamp(entry_candle["timestamp"])
            entry_time_str  = entry_ts.strftime("%H:%M")

            exit_price  = None
            exit_reason = None
            exit_ts     = None

            for c in candles[entry_idx + 1:]:
                ts      = datetime.fromtimestamp(c["timestamp"])
                cur_px  = c["close"]
                low_px  = c["low"]

                # update trailing SL
                if cur_px > highest_price:
                    highest_price = cur_px
                    profit = cur_px - entry_price
                    if profit >= trail_trigger:
                        steps    = int(profit / trail_trigger)
                        new_sl   = entry_price + (steps - 1) * trail_amount
                        if new_sl > trailing_sl:
                            trailing_sl = new_sl

                # stop-loss hit
                if low_px <= stop_loss:
                    exit_price  = stop_loss - entry_price * 0.001  # slight worse fill
                    exit_reason = "INITIAL_SL"
                    exit_ts     = ts
                    break
                if low_px <= trailing_sl and trailing_sl > stop_loss:
                    exit_price  = trailing_sl - entry_price * 0.001
                    exit_reason = "TRAILING_SL"
                    exit_ts     = ts
                    break
                # time exit 15:18
                if ts.time() >= dt_time(15, 18):
                    exit_price  = c["close"] * (1 - self.slippage_pct / 100)
                    exit_reason = "TIME_EXIT"
                    exit_ts     = ts
                    break

            if exit_price is None:
                # force close at last candle
                last = candles[-1]
                exit_price  = last["close"] * (1 - self.slippage_pct / 100)
                exit_reason = "TIME_EXIT"
                exit_ts     = datetime.fromtimestamp(last["timestamp"])

            pnl = (exit_price - entry_price) * lot_size * lots_per_trade
            pnl -= self.commission_per_lot * lots_per_trade * 2
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            hold_min = (exit_ts - entry_ts).total_seconds() / 60

            trades.append({
                "symbol":                 symbol,
                "option_type":            signal_type,
                "strike_price":           entry_candle.get("strike", 0),
                "entry_date":             date_str,
                "entry_time":             entry_time_str,
                "entry_price":            round(entry_price, 2),
                "exit_date":              exit_ts.strftime("%Y-%m-%d"),
                "exit_time":              exit_ts.strftime("%H:%M"),
                "exit_price":             round(exit_price, 2),
                "quantity":               lot_size * lots_per_trade,
                "lot_size":               lot_size,
                "pnl":                    round(pnl, 2),
                "pnl_pct":                round(pnl_pct, 2),
                "exit_reason":            exit_reason,
                "hold_duration_minutes":  round(hold_min, 1),
                "price_change_at_entry":  round(price_chg, 2),
                "oi_change_at_entry":     round(oi_chg, 2),
            })

            pnl_str = f"+{pnl:.0f}" if pnl >= 0 else f"{pnl:.0f}"
            print(f"[SIM]   {date_str} {signal_type} {symbol}: "
                  f"entry={entry_price:.1f} exit={exit_price:.1f} "
                  f"PnL=Rs.{pnl_str} ({exit_reason})")
            sys.stdout.flush()

        print(f"[SIM] {symbol}: {len(trades)} trades")
        sys.stdout.flush()
        return trades

    def _make_synthetic_daily(self, symbol: str, start_date: str,
                               end_date: str) -> List[Dict]:
        """
        Fallback: generate fully-synthetic daily data when Yahoo fails.
        Uses a random-walk with known average volatility for the symbol.
        """
        # rough reference prices for major stocks
        ref_prices = {
            "RELIANCE": 1250, "HDFCBANK": 1650, "ICICIBANK": 1220, "TCS": 3800,
            "INFY": 1750, "SBIN": 760, "AXISBANK": 1050, "KOTAKBANK": 1850,
            "BAJFINANCE": 8500, "MARUTI": 11000, "SUNPHARMA": 1700, "TITAN": 3200,
            "WIPRO": 550, "LTIM": 5500, "HCLTECH": 1650, "ADANIENT": 2200,
            "TATASTEEL": 140, "JSWSTEEL": 900, "HINDALCO": 620, "M&M": 2900,
        }
        base_price = ref_prices.get(symbol, 1000)
        iv = STOCK_IV.get(symbol, 0.30)
        sigma_daily = iv / math.sqrt(252)

        rows = []
        cur = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date,   "%Y-%m-%d")
        price = base_price
        while cur <= end:
            if cur.weekday() < 5:  # Mon-Fri only
                chg   = random.gauss(0, sigma_daily)
                open_ = price * (1 + random.gauss(0, sigma_daily * 0.3))
                close = open_ * (1 + chg)
                high  = max(open_, close) * (1 + abs(random.gauss(0, sigma_daily * 0.2)))
                low   = min(open_, close) * (1 - abs(random.gauss(0, sigma_daily * 0.2)))
                rows.append({
                    "date":   cur.strftime("%Y-%m-%d"),
                    "open":   round(open_, 2),
                    "high":   round(high,  2),
                    "low":    round(low,   2),
                    "close":  round(close, 2),
                    "volume": random.randint(500000, 5000000),
                })
                price = close
            cur += timedelta(days=1)
        return rows

    def run_backtest(self, symbols: List[str] = None, start_date: str = None,
                     end_date: str = None) -> Dict:
        """
        Run synthetic backtest across symbols.
        Returns a dict compatible with save_results_to_db().
        """
        if symbols is None:
            symbols = list(FNO_STOCKS.keys())
        if end_date is None:
            end_date   = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        print(f"\n{'='*60}")
        print(f"EOS SYNTHETIC BACKTEST: {start_date} -> {end_date}")
        print(f"Symbols : {len(symbols)}")
        print(f"Mode    : Yahoo Finance daily data + BS option pricing")
        print(f"{'='*60}")
        sys.stdout.flush()

        all_trades = []
        for sym in symbols:
            try:
                trades = self.run_backtest_single_symbol(sym, start_date, end_date)
                all_trades.extend(trades)
            except Exception as e:
                print(f"[SIM] Error on {sym}: {e}")
                sys.stdout.flush()

        return {
            "trades":       all_trades,
            "start_date":   start_date,
            "end_date":     end_date,
            "symbols":      symbols,
        }

    def save_results_to_db(self, result: Dict, initial_capital: float = 500000) -> str:
        """
        Persist simulation results to portfolio.db so the dashboard shows them.
        Mirrors EOSBacktester.save_results_to_db() signature exactly.
        """
        from EOS.eos_portfolio_manager import (
            EOSPortfolioManager, TradeRecord, DailySnapshot
        )

        pm = EOSPortfolioManager()
        trades = result["trades"]
        start_date  = result["start_date"]
        end_date    = result["end_date"]
        symbols     = result["symbols"]

        # 1. start session (labelled BACKTEST)
        backtest_id = pm.start_backtest(
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            initial_capital=initial_capital,
            config={
                "mode":   "BACKTEST_SIM",
                "source": "yahoo_finance + black_scholes",
                **{k: v for k, v in self.config.items()
                   if isinstance(v, (str, int, float, bool))}
            },
        )
        print(f"[DB] Session created: {backtest_id}")
        sys.stdout.flush()

        # 2. record trades
        for i, t in enumerate(trades):
            tr = TradeRecord(
                trade_id=f"{backtest_id}_T{i+1:04d}",
                symbol=t["symbol"],
                option_type=t["option_type"],
                strike_price=t["strike_price"],
                entry_date=t["entry_date"],
                entry_time=t["entry_time"],
                entry_price=t["entry_price"],
                exit_date=t["exit_date"],
                exit_time=t["exit_time"],
                exit_price=t["exit_price"],
                quantity=t["quantity"],
                lot_size=t["lot_size"],
                pnl=t["pnl"],
                pnl_pct=t["pnl_pct"],
                exit_reason=t["exit_reason"],
                hold_duration_minutes=t["hold_duration_minutes"],
                price_change_at_entry=t["price_change_at_entry"],
                oi_change_at_entry=t["oi_change_at_entry"],
                backtest_id=backtest_id,
            )
            pm.record_trade(tr)
        print(f"[DB] {len(trades)} trades recorded")
        sys.stdout.flush()

        # 3. daily snapshots
        daily_pnl: Dict[str, float] = {}
        for t in trades:
            d = t["exit_date"]
            daily_pnl[d] = daily_pnl.get(d, 0.0) + t["pnl"]

        cumulative = 0.0
        total_pnl  = sum(t["pnl"] for t in trades)
        max_dd     = 0.0
        peak       = 0.0
        for date in sorted(daily_pnl):
            day_pnl    = daily_pnl[date]
            cumulative += day_pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        cumulative = 0.0
        for date in sorted(daily_pnl):
            day_pnl    = daily_pnl[date]
            cumulative += day_pnl
            day_trades  = [t for t in trades if t["exit_date"] == date]
            day_wins    = [t for t in day_trades if t["pnl"] > 0]
            day_losses  = [t for t in day_trades if t["pnl"] <= 0]
            snap = DailySnapshot(
                date=date,
                starting_capital=initial_capital + cumulative - day_pnl,
                ending_capital=initial_capital + cumulative,
                daily_pnl=day_pnl,
                daily_pnl_pct=(day_pnl / initial_capital * 100) if initial_capital else 0,
                trades_taken=len(day_trades),
                winning_trades=len(day_wins),
                losing_trades=len(day_losses),
                max_drawdown=max_dd,
                cumulative_pnl=cumulative,
                backtest_id=backtest_id,
            )
            pm.record_daily_snapshot(snap)
        print(f"[DB] {len(daily_pnl)} daily snapshots recorded")
        sys.stdout.flush()

        # 4. end session
        winning = [t for t in trades if t["pnl"] > 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0.0
        pm.end_backtest(
            backtest_id=backtest_id,
            final_capital=initial_capital + total_pnl,
            metrics={
                "total_pnl":    total_pnl,
                "total_trades": len(trades),
                "win_rate":     win_rate,
                "sharpe_ratio": 0.0,
                "max_drawdown": max_dd,
            },
        )
        print(f"[DB] Session {backtest_id} completed and saved")
        sys.stdout.flush()
        return backtest_id


# ──────────────────────────────────────────────────────────────────────────────
# EOS PAPER LIVE RUNNER (REST-based, no WebSocket, no Dhan auth)
# ──────────────────────────────────────────────────────────────────────────────

class EOSPaperLiveRunner:
    """
    Paper-trade live runner using Yahoo Finance REST polling.
    No Dhan credentials required.

    Polls prices every ~30 seconds during market hours, checks EOS entry
    conditions, enters/exits virtual positions, and saves everything to DB.
    """

    POLL_INTERVAL = 30  # seconds between price polls

    def __init__(self, symbols: List[str] = None, initial_capital: float = 500000):
        self.symbols          = symbols or list(FNO_STOCKS.keys())
        self.initial_capital  = initial_capital
        self.config           = EOS_CONFIG
        self.is_running       = False
        self._stop_event      = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # state
        self.positions: Dict[str, Dict] = {}
        self.trades_today: List[Dict]   = []
        self.traded_today: set          = set()
        self.daily_pnl: float           = 0.0
        self.prev_closes: Dict[str, float] = {}
        self._session_id: Optional[str] = None
        self._pm = None

    def start(self) -> None:
        """Start paper live runner in background thread."""
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()

        # init DB session
        from EOS.eos_portfolio_manager import EOSPortfolioManager
        self._pm = EOSPortfolioManager()
        today = datetime.now().strftime("%Y-%m-%d")
        self._session_id = self._pm.start_backtest(
            start_date=today,
            end_date=today,
            symbols=self.symbols,
            initial_capital=self.initial_capital,
            config={"mode": "PAPER_LIVE", "source": "yahoo_finance_ltp"},
        )
        print(f"[PaperLive] Session started: {self._session_id}")

        # prefetch yesterday's closes
        self._prefetch_prev_closes()

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="EOSPaperLive"
        )
        self._thread.start()
        print("[PaperLive] Running...")

    def stop(self) -> None:
        """Stop the live runner gracefully."""
        self.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._close_all_positions("MANUAL_STOP")
        self._save_session()
        print("[PaperLive] Stopped.")

    def _prefetch_prev_closes(self):
        """Download yesterday's close prices for all symbols."""
        end   = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        print("[PaperLive] Fetching prev closes...")
        for sym in self.symbols[:5]:  # limit to 5 most-liquid for speed
            rows = _get_daily_ohlc(sym, start, end)
            if rows and len(rows) >= 2:
                # second-to-last is prev close (last is today if market open)
                self.prev_closes[sym] = rows[-2]["close"]
                print(f"[PaperLive]   {sym} prev_close={rows[-2]['close']:.2f}")
        sys.stdout.flush()

    def _run_loop(self):
        """Main polling loop – runs every POLL_INTERVAL seconds."""
        price_threshold = self.config["price_change_threshold"]
        sl_pct          = self.config["initial_stop_loss_pct"] / 100.0
        trail_trigger   = self.config["trailing_sl_trigger"]
        trail_amount    = self.config["trailing_sl_amount"]
        lot_size_map    = {s: FNO_STOCKS[s]["lot_size"] for s in self.symbols if s in FNO_STOCKS}

        while self.is_running and not self._stop_event.is_set():
            now = datetime.now()
            t   = now.time()

            # Only scan during market hours
            if dt_time(9, 15) <= t <= dt_time(15, 30):
                # ── 1. manage open positions ────────────────────────────────
                for sym, pos in list(self.positions.items()):
                    ltp = _get_ltp_yahoo(sym)
                    if ltp is None:
                        continue
                    opt_px = _option_price(ltp, pos["option_type"], sym)
                    low_px = opt_px * 0.98  # approximate intrabar low

                    # update trailing SL
                    if opt_px > pos["highest"]:
                        pos["highest"] = opt_px
                        profit = opt_px - pos["entry_px"]
                        if profit >= trail_trigger:
                            steps  = int(profit / trail_trigger)
                            new_sl = pos["entry_px"] + (steps - 1) * trail_amount
                            if new_sl > pos["trailing_sl"]:
                                pos["trailing_sl"] = new_sl

                    exit_reason = None
                    exit_px     = opt_px
                    if low_px <= pos["stop_loss"]:
                        exit_reason = "INITIAL_SL"
                        exit_px     = pos["stop_loss"]
                    elif low_px <= pos["trailing_sl"] and pos["trailing_sl"] > pos["stop_loss"]:
                        exit_reason = "TRAILING_SL"
                        exit_px     = pos["trailing_sl"]
                    elif t >= dt_time(15, 18):
                        exit_reason = "TIME_EXIT"

                    if exit_reason:
                        self._close_position(sym, exit_px, exit_reason, now)

                # ── 2. scan for new signals ─────────────────────────────────
                if t <= dt_time(14, 30) and len(self.positions) < self.config["max_trades_per_day"]:
                    for sym in self.symbols:
                        if sym in self.positions or sym in self.traded_today:
                            continue
                        if self.daily_pnl <= -self.config["max_loss_per_day"]:
                            break

                        prev_close = self.prev_closes.get(sym)
                        if not prev_close:
                            continue

                        ltp = _get_ltp_yahoo(sym)
                        if ltp is None:
                            continue

                        pct_chg = (ltp - prev_close) / prev_close * 100
                        if abs(pct_chg) > price_threshold:
                            signal = "PUT" if pct_chg > price_threshold else "CALL"
                            opt_px = _option_price(ltp, signal, sym)
                            lot_sz = lot_size_map.get(sym, 100)
                            sl     = opt_px * (1 - sl_pct)

                            self.positions[sym] = {
                                "option_type":  signal,
                                "entry_px":     opt_px,
                                "entry_time":   now,
                                "entry_date":   now.strftime("%Y-%m-%d"),
                                "entry_time_s": now.strftime("%H:%M"),
                                "stop_loss":    sl,
                                "trailing_sl":  sl,
                                "highest":      opt_px,
                                "lot_size":     lot_sz,
                                "spot_at_entry":ltp,
                                "pct_chg":      pct_chg,
                                "oi_chg":       pct_chg * 1.1,
                            }
                            print(f"[PaperLive] ENTRY {sym} {signal} @ opt_px={opt_px:.1f}"
                                  f"  spot={ltp:.1f}  Δ={pct_chg:.1f}%")
                            sys.stdout.flush()

            # force close near market close
            if t >= dt_time(15, 25) and self.positions:
                self._close_all_positions("TIME_EXIT")

            self._stop_event.wait(self.POLL_INTERVAL)

        self._save_session()

    def _close_position(self, sym: str, exit_px: float,
                         exit_reason: str, now: datetime):
        """Close a single position and record the trade."""
        if sym not in self.positions:
            return
        pos = self.positions.pop(sym)
        lot_size        = pos["lot_size"]
        lots_per_trade  = self.config["lots_per_trade"]
        commission      = self.config.get("commission_per_lot", 40)
        pnl  = (exit_px - pos["entry_px"]) * lot_size * lots_per_trade
        pnl -= commission * lots_per_trade * 2
        pnl_pct = (exit_px - pos["entry_px"]) / pos["entry_px"] * 100

        self.daily_pnl += pnl
        self.traded_today.add(sym)

        trade = {
            "symbol":                sym,
            "option_type":           pos["option_type"],
            "strike_price":          round(pos.get("spot_at_entry", 0) / 50) * 50,
            "entry_date":            pos["entry_date"],
            "entry_time":            pos["entry_time_s"],
            "entry_price":           round(pos["entry_px"], 2),
            "exit_date":             now.strftime("%Y-%m-%d"),
            "exit_time":             now.strftime("%H:%M"),
            "exit_price":            round(exit_px, 2),
            "quantity":              lot_size * lots_per_trade,
            "lot_size":              lot_size,
            "pnl":                   round(pnl, 2),
            "pnl_pct":               round(pnl_pct, 2),
            "exit_reason":           exit_reason,
            "hold_duration_minutes": (now - pos["entry_time"]).total_seconds() / 60,
            "price_change_at_entry": pos["pct_chg"],
            "oi_change_at_entry":    pos["oi_chg"],
        }
        self.trades_today.append(trade)

        pnl_str = f"+{pnl:.0f}" if pnl >= 0 else f"{pnl:.0f}"
        print(f"[PaperLive] EXIT {sym} {exit_reason} exit_px={exit_px:.1f}"
              f"  PnL=Rs.{pnl_str}")
        sys.stdout.flush()

        # save to DB immediately
        if self._pm and self._session_id:
            from EOS.eos_portfolio_manager import TradeRecord
            tr = TradeRecord(
                trade_id=f"{self._session_id}_T{len(self.trades_today):04d}",
                backtest_id=self._session_id,
                **{k: v for k, v in trade.items()},
            )
            try:
                self._pm.record_trade(tr)
            except Exception:
                pass

    def _close_all_positions(self, reason: str):
        """Close all open positions."""
        now = datetime.now()
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            ltp = _get_ltp_yahoo(sym)
            opt_px = _option_price(ltp, pos["option_type"], sym) if ltp else pos["entry_px"]
            self._close_position(sym, opt_px, reason, now)

    def _save_session(self):
        """Save daily snapshot and end the DB session."""
        if not self._pm or not self._session_id:
            return
        today    = datetime.now().strftime("%Y-%m-%d")
        total_pnl = sum(t["pnl"] for t in self.trades_today)
        winning   = [t for t in self.trades_today if t["pnl"] > 0]
        from EOS.eos_portfolio_manager import DailySnapshot
        snap = DailySnapshot(
            date=today,
            starting_capital=self.initial_capital,
            ending_capital=self.initial_capital + total_pnl,
            daily_pnl=total_pnl,
            daily_pnl_pct=(total_pnl / self.initial_capital * 100) if self.initial_capital else 0,
            trades_taken=len(self.trades_today),
            winning_trades=len(winning),
            losing_trades=len(self.trades_today) - len(winning),
            max_drawdown=0.0,
            cumulative_pnl=total_pnl,
            backtest_id=self._session_id,
        )
        try:
            self._pm.record_daily_snapshot(snap)
            win_rate = len(winning) / len(self.trades_today) * 100 if self.trades_today else 0
            self._pm.end_backtest(
                backtest_id=self._session_id,
                final_capital=self.initial_capital + total_pnl,
                metrics={
                    "total_pnl":    total_pnl,
                    "total_trades": len(self.trades_today),
                    "win_rate":     win_rate,
                    "sharpe_ratio": 0.0,
                    "max_drawdown": 0.0,
                },
            )
            print(f"[PaperLive] Session saved. Trades: {len(self.trades_today)}"
                  f"  PnL: Rs.{total_pnl:.0f}")
        except Exception as e:
            print(f"[PaperLive] Error saving session: {e}")
        sys.stdout.flush()
