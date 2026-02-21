"""
CRYPTO Simulation Engine
========================
Runs the CRYPTO-EOS strategy on REAL live data from Bybit public REST API.
Works without any API credentials.

Modes
-----
  backtest_sim : downloads historical klines (Bybit public) for the past
                 N days and runs the signal logic on 5-min bars.
  paper_live   : polls live tickers every 30 s, enters virtual positions,
                 exits on SL / trailing-SL / session-end.

No API key required – all Bybit public endpoints are used.
"""

import sys
import threading
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional

import pytz
import requests

# ── project root on sys.path ──────────────────────────────────────────────────
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from CRYPTO.config import CRYPTO_CONFIG, CRYPTO_PAIRS
from CRYPTO.data_fetcher import CryptoDataFetcher

IST = pytz.timezone("Asia/Kolkata")
requests.packages.urllib3.disable_warnings()

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _fetch_klines(symbol: str, interval: str = "5", limit: int = 500,
                  start_ms: int = None) -> List[Dict]:
    """
    Fetch Bybit public klines. Returns list of:
      {ts_ms, open, high, low, close, volume}
    Newest-first from Bybit -> we reverse to oldest-first.
    """
    df = CryptoDataFetcher()
    result = df.get_kline(symbol=symbol, interval=interval, limit=limit,
                          start=start_ms)
    if result.get("error"):
        return []
    data = result.get("data", {})
    raw = data.get("list", [])
    # Bybit returns: [startTime, open, high, low, close, volume, turnover]
    candles = []
    for row in raw:
        try:
            candles.append({
                "ts_ms":  int(row[0]),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })
        except Exception:
            continue
    candles.sort(key=lambda c: c["ts_ms"])   # oldest first
    return candles


def _fetch_ticker(symbol: str) -> Optional[Dict]:
    """Fetch single ticker from Bybit. Returns dict with lastPrice etc."""
    df = CryptoDataFetcher()
    r  = df.get_ticker(symbol)
    if r.get("error") or not r.get("data"):
        return None
    lst = r["data"].get("list", [])
    return lst[0] if lst else None


# ──────────────────────────────────────────────────────────────────────────────
# CRYPTO BACKTEST SIMULATOR
# ──────────────────────────────────────────────────────────────────────────────

class CryptoSyntheticBacktester:
    """
    Downloads real historical 5-min klines from Bybit (public) and runs
    the CRYPTO-EOS contrarian strategy.

    Entry : abs(price_chg from prev_close) > 4%  ->  contrarian direction
    Exit  : initial SL 3%, trailing SL 1%, or time exit at 13:50 IST
    """

    def __init__(self):
        self.config = CRYPTO_CONFIG

    def run_backtest_single_symbol(self, symbol: str,
                                   start_date: str, end_date: str) -> List[Dict]:
        """
        Download klines and simulate trades for one symbol.
        start_date / end_date: "YYYY-MM-DD"
        """
        print(f"\n[CryptoSim] Backtesting {symbol} ({start_date} -> {end_date})")
        sys.stdout.flush()

        t_start = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        t_end   = int((datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                       ).timestamp() * 1000)

        # fetch up to 1000 bars at a time (Bybit limit)
        all_candles: List[Dict] = []
        cursor = t_start
        while cursor < t_end:
            batch = _fetch_klines(symbol, "5", 1000, start_ms=cursor)
            if not batch:
                break
            all_candles.extend(batch)
            cursor = batch[-1]["ts_ms"] + 300_000   # next 5-min window
            if len(batch) < 1000:
                break
            time.sleep(0.2)

        # de-duplicate and sort
        seen = set()
        candles = []
        for c in sorted(all_candles, key=lambda x: x["ts_ms"]):
            if c["ts_ms"] not in seen:
                seen.add(c["ts_ms"])
                candles.append(c)

        if not candles:
            print(f"[CryptoSim]   No kline data for {symbol}")
            return []

        print(f"[CryptoSim]   {len(candles)} 5-min candles loaded")
        sys.stdout.flush()

        # compute rolling 20-period avg volume
        avg_vol_window = self.config.get("avg_volume_periods", 20)
        price_thresh   = self.config["price_change_threshold"]  # 4%
        sl_pct         = self.config["initial_stop_loss_pct"] / 100.0   # 3%
        trail_trigger  = self.config["trailing_sl_trigger_pct"] / 100.0  # 1%
        trail_amount   = self.config["trailing_sl_amount_pct"] / 100.0   # 1%
        min_qty        = CRYPTO_PAIRS.get(symbol, {}).get("min_qty", 0.001)

        # build daily close map for prev-close calculation
        daily_closes: Dict[str, float] = {}
        for c in candles:
            dt_ist   = datetime.fromtimestamp(c["ts_ms"] / 1000, tz=IST)
            date_str = dt_ist.strftime("%Y-%m-%d")
            daily_closes[date_str] = c["close"]   # last bar of day wins

        trades: List[Dict] = []
        position: Optional[Dict] = None
        traded_today: set = set()
        current_date: Optional[str] = None

        # volume window
        recent_vols: List[float] = []

        for idx, c in enumerate(candles):
            dt_ist   = datetime.fromtimestamp(c["ts_ms"] / 1000, tz=IST)
            date_str = dt_ist.strftime("%Y-%m-%d")
            time_ist = dt_ist.time()

            # reset daily state at session start
            if date_str != current_date:
                current_date = date_str
                traded_today = set()

            # only trade during Asia session 05:30 – 13:50
            in_session = dt_time(5, 30) <= time_ist <= dt_time(13, 50)

            # maintain rolling avg vol
            recent_vols.append(c["volume"])
            if len(recent_vols) > avg_vol_window:
                recent_vols.pop(0)
            avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1

            # ── manage open position ──────────────────────────────────────
            if position is not None:
                cur_px = c["close"]
                side   = position["side"]

                # price favorable for LONG is up, for SHORT is down
                if side == "LONG":
                    fav_px = cur_px
                    low_px = c["low"]
                else:
                    fav_px = -cur_px   # we want price to fall
                    low_px = c["high"]  # worst-case for short

                # trailing SL update (percentage-based)
                if side == "LONG":
                    if cur_px > position["highest"]:
                        position["highest"] = cur_px
                        profit_pct = (cur_px - position["entry_px"]) / position["entry_px"]
                        if profit_pct >= trail_trigger:
                            steps  = int(profit_pct / trail_trigger)
                            new_sl = position["entry_px"] * (1 + (steps - 1) * trail_amount)
                            if new_sl > position["trailing_sl"]:
                                position["trailing_sl"] = new_sl
                else:  # SHORT
                    if cur_px < position["lowest"]:
                        position["lowest"] = cur_px
                        profit_pct = (position["entry_px"] - cur_px) / position["entry_px"]
                        if profit_pct >= trail_trigger:
                            steps  = int(profit_pct / trail_trigger)
                            new_sl = position["entry_px"] * (1 - (steps - 1) * trail_amount)
                            if new_sl < position["trailing_sl"]:
                                position["trailing_sl"] = new_sl

                exit_reason = None
                exit_px     = cur_px

                if side == "LONG":
                    if c["low"] <= position["stop_loss"]:
                        exit_reason = "INITIAL_SL"
                        exit_px     = position["stop_loss"]
                    elif c["low"] <= position["trailing_sl"] and \
                            position["trailing_sl"] > position["stop_loss"]:
                        exit_reason = "TRAILING_SL"
                        exit_px     = position["trailing_sl"]
                else:  # SHORT
                    if c["high"] >= position["stop_loss"]:
                        exit_reason = "INITIAL_SL"
                        exit_px     = position["stop_loss"]
                    elif c["high"] >= position["trailing_sl"] and \
                            position["trailing_sl"] < position["stop_loss"]:
                        exit_reason = "TRAILING_SL"
                        exit_px     = position["trailing_sl"]

                if time_ist >= dt_time(13, 50):
                    exit_reason = "TIME_EXIT"
                    exit_px     = cur_px

                if exit_reason:
                    entry_px = position["entry_px"]
                    if side == "LONG":
                        pnl_usdt = (exit_px - entry_px) * min_qty
                    else:
                        pnl_usdt = (entry_px - exit_px) * min_qty

                    pnl_pct  = (pnl_usdt / (entry_px * min_qty)) * 100
                    hold_min = (c["ts_ms"] - position["entry_ts_ms"]) / 60000

                    trades.append({
                        "symbol":                 symbol,
                        "side":                   side,
                        "entry_date":             position["entry_date"],
                        "entry_time":             position["entry_time"],
                        "entry_price":            round(entry_px, 4),
                        "exit_date":              date_str,
                        "exit_time":              time_ist.strftime("%H:%M"),
                        "exit_price":             round(exit_px, 4),
                        "quantity":               min_qty,
                        "min_qty":                min_qty,
                        "pnl_usdt":               round(pnl_usdt, 4),
                        "pnl_pct":                round(pnl_pct, 2),
                        "exit_reason":            exit_reason,
                        "hold_duration_minutes":  round(hold_min, 1),
                        "price_change_at_entry":  position["price_chg"],
                        "funding_rate_at_entry":  0.0,
                        "volume_spike_at_entry":  position["vol_spike"],
                    })

                    pnl_str = f"+{pnl_usdt:.2f}" if pnl_usdt >= 0 else f"{pnl_usdt:.2f}"
                    print(f"[CryptoSim]   {date_str} {side} {symbol}: "
                          f"entry={entry_px:.2f} exit={exit_px:.2f} "
                          f"PnL=${pnl_str} ({exit_reason})")
                    sys.stdout.flush()
                    position = None
                    continue

            # ── look for entry signals ─────────────────────────────────────
            if (position is None
                    and in_session
                    and symbol not in traded_today):

                cur_px = c["close"]

                # get prev-day close (walk back up to 3 days)
                prev_close = None
                for d in range(1, 4):
                    pd = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=d)
                          ).strftime("%Y-%m-%d")
                    if pd in daily_closes:
                        prev_close = daily_closes[pd]
                        break

                if prev_close and prev_close != 0:
                    price_chg = (cur_px - prev_close) / prev_close * 100
                    vol_spike = c["volume"] > avg_vol * self.config.get("volume_spike_multiplier", 1.5)

                    if abs(price_chg) > price_thresh:
                        # contrarian: big up -> SHORT, big down -> LONG
                        side = "SHORT" if price_chg > 0 else "LONG"

                        if side == "LONG":
                            entry_px  = cur_px
                            sl        = entry_px * (1 - sl_pct)
                            trail_sl  = sl
                        else:
                            entry_px  = cur_px
                            sl        = entry_px * (1 + sl_pct)
                            trail_sl  = sl

                        position = {
                            "side":        side,
                            "entry_px":    entry_px,
                            "entry_ts_ms": c["ts_ms"],
                            "entry_date":  date_str,
                            "entry_time":  time_ist.strftime("%H:%M"),
                            "stop_loss":   sl,
                            "trailing_sl": trail_sl,
                            "highest":     entry_px,
                            "lowest":      entry_px,
                            "price_chg":   round(price_chg, 2),
                            "vol_spike":   vol_spike,
                        }
                        traded_today.add(symbol)
                        print(f"[CryptoSim]   ENTRY {symbol} {side} "
                              f"@ {entry_px:.2f}  Δ={price_chg:.1f}%")
                        sys.stdout.flush()

        # force close if position still open
        if position and candles:
            last = candles[-1]
            entry_px = position["entry_px"]
            exit_px  = last["close"]
            side     = position["side"]
            if side == "LONG":
                pnl_usdt = (exit_px - entry_px) * min_qty
            else:
                pnl_usdt = (entry_px - exit_px) * min_qty
            pnl_pct  = (pnl_usdt / (entry_px * min_qty)) * 100
            hold_min = (last["ts_ms"] - position["entry_ts_ms"]) / 60000
            dt_last  = datetime.fromtimestamp(last["ts_ms"] / 1000, tz=IST)
            trades.append({
                "symbol":                symbol,
                "side":                  side,
                "entry_date":            position["entry_date"],
                "entry_time":            position["entry_time"],
                "entry_price":           round(entry_px, 4),
                "exit_date":             dt_last.strftime("%Y-%m-%d"),
                "exit_time":             dt_last.strftime("%H:%M"),
                "exit_price":            round(exit_px, 4),
                "quantity":              min_qty,
                "min_qty":               min_qty,
                "pnl_usdt":              round(pnl_usdt, 4),
                "pnl_pct":               round(pnl_pct, 2),
                "exit_reason":           "TIME_EXIT",
                "hold_duration_minutes": round(hold_min, 1),
                "price_change_at_entry": position["price_chg"],
                "funding_rate_at_entry": 0.0,
                "volume_spike_at_entry": position["vol_spike"],
            })

        print(f"[CryptoSim] {symbol}: {len(trades)} trades")
        sys.stdout.flush()
        return trades

    def run_backtest(self, symbols: List[str] = None, start_date: str = None,
                     end_date: str = None) -> Dict:
        """Run across all symbols. Returns result dict."""
        if symbols is None:
            symbols = list(CRYPTO_PAIRS.keys())
        if end_date is None:
            end_date = _now_ist().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (_now_ist() - timedelta(days=30)).strftime("%Y-%m-%d")

        print(f"\n{'='*60}")
        print(f"CRYPTO SYNTHETIC BACKTEST: {start_date} -> {end_date}")
        print(f"Symbols: {symbols}")
        print(f"Source : Bybit public klines (no auth)")
        print(f"{'='*60}")
        sys.stdout.flush()

        all_trades: List[Dict] = []
        for sym in symbols:
            try:
                t = self.run_backtest_single_symbol(sym, start_date, end_date)
                all_trades.extend(t)
            except Exception as e:
                print(f"[CryptoSim] Error on {sym}: {e}")
                sys.stdout.flush()

        return {
            "trades":     all_trades,
            "start_date": start_date,
            "end_date":   end_date,
            "symbols":    symbols,
        }

    def save_results_to_db(self, result: Dict,
                            initial_capital_usdt: float = 10000.0) -> str:
        """Persist crypto sim results to portfolio.db."""
        from CRYPTO.portfolio_manager import (
            CryptoPortfolioManager, CryptoTradeRecord, CryptoDailySnapshot
        )

        pm     = CryptoPortfolioManager()
        trades = result["trades"]

        session_id = pm.start_session(
            start_date=result["start_date"],
            end_date=result["end_date"],
            symbols=result["symbols"],
            initial_capital_usdt=initial_capital_usdt,
            config={
                "mode": "BACKTEST_SIM",
                "source": "bybit_public_klines",
                **{k: v for k, v in self.config.items()
                   if isinstance(v, (str, int, float, bool))}
            },
            mode="PAPER",
        )
        print(f"[CryptoDB] Session created: {session_id}")
        sys.stdout.flush()

        for i, t in enumerate(trades):
            tr = CryptoTradeRecord(
                trade_id=f"{session_id}_T{i+1:04d}",
                session_id=session_id,
                symbol=t["symbol"],
                side=t["side"],
                entry_date=t["entry_date"],
                entry_time=t["entry_time"],
                entry_price=t["entry_price"],
                exit_date=t["exit_date"],
                exit_time=t["exit_time"],
                exit_price=t["exit_price"],
                quantity=t["quantity"],
                min_qty=t["min_qty"],
                pnl_usdt=t["pnl_usdt"],
                pnl_pct=t["pnl_pct"],
                exit_reason=t["exit_reason"],
                hold_duration_minutes=t["hold_duration_minutes"],
                price_change_at_entry=t["price_change_at_entry"],
                funding_rate_at_entry=t["funding_rate_at_entry"],
                volume_spike_at_entry=t["volume_spike_at_entry"],
            )
            pm.record_trade(tr)
        print(f"[CryptoDB] {len(trades)} trades recorded")
        sys.stdout.flush()

        # daily snapshots
        daily_pnl: Dict[str, float] = {}
        for t in trades:
            d = t["exit_date"]
            daily_pnl[d] = daily_pnl.get(d, 0.0) + t["pnl_usdt"]

        cumulative = 0.0
        total_pnl  = sum(t["pnl_usdt"] for t in trades)
        for date in sorted(daily_pnl):
            day_pnl    = daily_pnl[date]
            cumulative += day_pnl
            day_trades = [t for t in trades if t["exit_date"] == date]
            day_wins   = [t for t in day_trades if t["pnl_usdt"] > 0]
            snap = CryptoDailySnapshot(
                date=date,
                starting_capital_usdt=initial_capital_usdt + cumulative - day_pnl,
                ending_capital_usdt=initial_capital_usdt + cumulative,
                daily_pnl_usdt=day_pnl,
                daily_pnl_pct=(day_pnl / initial_capital_usdt * 100) if initial_capital_usdt else 0,
                trades_taken=len(day_trades),
                winning_trades=len(day_wins),
                losing_trades=len(day_trades) - len(day_wins),
                max_drawdown_usdt=0.0,
                cumulative_pnl_usdt=cumulative,
                session_id=session_id,
            )
            pm.record_daily_snapshot(snap)

        winning  = [t for t in trades if t["pnl_usdt"] > 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0.0
        pm.end_session(
            session_id=session_id,
            final_capital_usdt=initial_capital_usdt + total_pnl,
            metrics={
                "total_pnl_usdt": total_pnl,
                "total_trades":   len(trades),
                "win_rate":       win_rate,
                "sharpe_ratio":   0.0,
                "max_drawdown":   0.0,
            },
        )
        print(f"[CryptoDB] {len(daily_pnl)} snapshots saved. Session complete.")
        sys.stdout.flush()
        return session_id


# ──────────────────────────────────────────────────────────────────────────────
# CRYPTO PAPER LIVE RUNNER (REST polling, no WebSocket, no auth)
# ──────────────────────────────────────────────────────────────────────────────

class CryptoPaperLiveRunner:
    """
    Paper-trade crypto live runner.
    Polls Bybit public tickers every 30 s, applies CRYPTO-EOS signal logic,
    and records virtual trades to the DB.

    Works 24/7 – does NOT restrict to Asia session so results appear immediately.
    Session window is used only for auto-stop; signals fire any time.
    """

    POLL_INTERVAL = 30  # seconds

    def __init__(self, symbols: List[str] = None,
                 initial_capital_usdt: float = 10000.0,
                 paper_trade: bool = True):
        self.symbols               = symbols or list(CRYPTO_PAIRS.keys())
        self.initial_capital_usdt  = initial_capital_usdt
        self.config                = CRYPTO_CONFIG
        self.paper_trade           = paper_trade
        self.is_running            = False
        self._stop_event           = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # state
        self.positions: Dict[str, Dict]  = {}
        self.trades_today: List[Dict]    = []
        self.traded_today: set           = set()
        self.daily_pnl_usdt: float       = 0.0
        self.prev_prices: Dict[str, float] = {}
        self._session_id: Optional[str] = None
        self._pm = None

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._stop_event.clear()

        from CRYPTO.portfolio_manager import CryptoPortfolioManager
        self._pm = CryptoPortfolioManager()
        today = _now_ist().strftime("%Y-%m-%d")
        mode  = "PAPER" if self.paper_trade else "LIVE"
        self._session_id = self._pm.start_session(
            start_date=today,
            end_date=today,
            symbols=self.symbols,
            initial_capital_usdt=self.initial_capital_usdt,
            config=self.config,
            mode=mode,
        )
        print(f"[CryptoPaper] Session {self._session_id} started")

        # fetch prev prices
        self._prefetch_prev_prices()

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="CryptoPaperLive"
        )
        self._thread.start()
        print("[CryptoPaper] Running...")

    def stop(self) -> None:
        self.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        self._close_all_positions("MANUAL_STOP")
        self._save_session()
        print("[CryptoPaper] Stopped.")

    def _prefetch_prev_prices(self):
        """Get the last confirmed close for each pair."""
        for sym in self.symbols:
            tk = _fetch_ticker(sym)
            if tk:
                try:
                    self.prev_prices[sym] = float(tk.get("prevPrice24h") or
                                                   tk.get("lastPrice", 0))
                    print(f"[CryptoPaper]   {sym} prev={self.prev_prices[sym]:.4f}")
                except Exception:
                    pass
        sys.stdout.flush()

    def _run_loop(self):
        sl_pct       = self.config["initial_stop_loss_pct"] / 100.0
        trail_trig   = self.config["trailing_sl_trigger_pct"] / 100.0
        trail_amt    = self.config["trailing_sl_amount_pct"] / 100.0
        price_thresh = self.config["price_change_threshold"]
        max_trades   = self.config["max_trades_per_day"]
        max_loss     = self.config["max_loss_per_day_usdt"]

        while self.is_running and not self._stop_event.is_set():
            try:
                now = _now_ist()
                t   = now.time()

                # ── manage open positions ─────────────────────────────────
                for sym in list(self.positions.keys()):
                    tk = _fetch_ticker(sym)
                    if not tk:
                        continue
                    cur_px = float(tk.get("lastPrice", 0))
                    if cur_px == 0:
                        continue
                    pos  = self.positions[sym]
                    side = pos["side"]

                    # update trailing SL
                    if side == "LONG":
                        if cur_px > pos["highest"]:
                            pos["highest"] = cur_px
                            pct = (cur_px - pos["entry_px"]) / pos["entry_px"]
                            if pct >= trail_trig:
                                steps  = int(pct / trail_trig)
                                new_sl = pos["entry_px"] * (1 + (steps - 1) * trail_amt)
                                if new_sl > pos["trailing_sl"]:
                                    pos["trailing_sl"] = new_sl
                    else:
                        if cur_px < pos["lowest"]:
                            pos["lowest"] = cur_px
                            pct = (pos["entry_px"] - cur_px) / pos["entry_px"]
                            if pct >= trail_trig:
                                steps  = int(pct / trail_trig)
                                new_sl = pos["entry_px"] * (1 - (steps - 1) * trail_amt)
                                if new_sl < pos["trailing_sl"]:
                                    pos["trailing_sl"] = new_sl

                    exit_reason = None
                    exit_px     = cur_px

                    if side == "LONG":
                        if cur_px <= pos["stop_loss"]:
                            exit_reason = "INITIAL_SL";   exit_px = pos["stop_loss"]
                        elif cur_px <= pos["trailing_sl"] and pos["trailing_sl"] > pos["stop_loss"]:
                            exit_reason = "TRAILING_SL";  exit_px = pos["trailing_sl"]
                    else:
                        if cur_px >= pos["stop_loss"]:
                            exit_reason = "INITIAL_SL";   exit_px = pos["stop_loss"]
                        elif cur_px >= pos["trailing_sl"] and pos["trailing_sl"] < pos["stop_loss"]:
                            exit_reason = "TRAILING_SL";  exit_px = pos["trailing_sl"]

                    # session-end force close
                    if t >= dt_time(13, 50):
                        exit_reason = "TIME_EXIT"

                    if exit_reason:
                        self._close_position(sym, exit_px, exit_reason, now)

                # ── scan for new entries ──────────────────────────────────
                if (len(self.positions) < max_trades
                        and self.daily_pnl_usdt > -max_loss):
                    for sym in self.symbols:
                        if sym in self.positions or sym in self.traded_today:
                            continue
                        prev = self.prev_prices.get(sym)
                        if not prev:
                            continue
                        tk = _fetch_ticker(sym)
                        if not tk:
                            continue
                        cur_px = float(tk.get("lastPrice", 0))
                        if cur_px == 0:
                            continue
                        pct_chg = (cur_px - prev) / prev * 100

                        if abs(pct_chg) > price_thresh:
                            side = "SHORT" if pct_chg > 0 else "LONG"
                            min_qty = CRYPTO_PAIRS.get(sym, {}).get("min_qty", 0.001)
                            if side == "LONG":
                                sl       = cur_px * (1 - sl_pct)
                                trail_sl = sl
                            else:
                                sl       = cur_px * (1 + sl_pct)
                                trail_sl = sl

                            self.positions[sym] = {
                                "side":       side,
                                "entry_px":   cur_px,
                                "entry_time": now,
                                "entry_date": now.strftime("%Y-%m-%d"),
                                "entry_time_s": now.strftime("%H:%M"),
                                "stop_loss":  sl,
                                "trailing_sl": trail_sl,
                                "highest":    cur_px,
                                "lowest":     cur_px,
                                "min_qty":    min_qty,
                                "price_chg":  round(pct_chg, 2),
                            }
                            print(f"[CryptoPaper] ENTRY {sym} {side} @ {cur_px:.4f}"
                                  f"  Δ={pct_chg:.1f}%")
                            sys.stdout.flush()

            except Exception as e:
                print(f"[CryptoPaper] Loop error: {e}")
                sys.stdout.flush()

            self._stop_event.wait(self.POLL_INTERVAL)

        self._save_session()

    def _close_position(self, sym: str, exit_px: float,
                         reason: str, now: datetime):
        if sym not in self.positions:
            return
        pos     = self.positions.pop(sym)
        side    = pos["side"]
        min_qty = pos["min_qty"]
        entry   = pos["entry_px"]

        if side == "LONG":
            pnl = (exit_px - entry) * min_qty
        else:
            pnl = (entry - exit_px) * min_qty
        pnl_pct  = (pnl / (entry * min_qty)) * 100
        hold_min = (now - pos["entry_time"]).total_seconds() / 60

        self.daily_pnl_usdt += pnl
        self.traded_today.add(sym)

        trade = {
            "symbol":                sym,
            "side":                  side,
            "entry_date":            pos["entry_date"],
            "entry_time":            pos["entry_time_s"],
            "entry_price":           round(entry, 4),
            "exit_date":             now.strftime("%Y-%m-%d"),
            "exit_time":             now.strftime("%H:%M"),
            "exit_price":            round(exit_px, 4),
            "quantity":              min_qty,
            "min_qty":               min_qty,   # required by CryptoTradeRecord
            "pnl_usdt":              round(pnl, 4),
            "pnl_pct":               round(pnl_pct, 2),
            "exit_reason":           reason,
            "hold_duration_minutes": round(hold_min, 1),
            "price_change_at_entry": pos["price_chg"],
            "funding_rate_at_entry": 0.0,
            "volume_spike_at_entry": False,
        }
        self.trades_today.append(trade)
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        print(f"[CryptoPaper] EXIT {sym} {reason} @ {exit_px:.4f}  PnL=${pnl_str}")
        sys.stdout.flush()

        if self._pm and self._session_id:
            from CRYPTO.portfolio_manager import CryptoTradeRecord
            try:
                tr = CryptoTradeRecord(
                    trade_id=f"{self._session_id}_T{len(self.trades_today):04d}",
                    session_id=self._session_id,
                    **{k: v for k, v in trade.items()},
                )
                self._pm.record_trade(tr)
                print(f"[CryptoPaper] Trade saved to DB: {sym} {side} PnL=${pnl_str}")
            except Exception as e:
                print(f"[CryptoPaper] ERROR saving trade to DB: {e}")

    def _close_all_positions(self, reason: str):
        now = _now_ist()
        for sym in list(self.positions.keys()):
            tk = _fetch_ticker(sym)
            px = float(tk.get("lastPrice", self.positions[sym]["entry_px"])) if tk else \
                 self.positions[sym]["entry_px"]
            self._close_position(sym, px, reason, now)

    def _save_session(self):
        if not self._pm or not self._session_id:
            return
        today     = _now_ist().strftime("%Y-%m-%d")
        total_pnl = sum(t["pnl_usdt"] for t in self.trades_today)
        winning   = [t for t in self.trades_today if t["pnl_usdt"] > 0]

        from CRYPTO.portfolio_manager import CryptoDailySnapshot
        snap = CryptoDailySnapshot(
            date=today,
            starting_capital_usdt=self.initial_capital_usdt,
            ending_capital_usdt=self.initial_capital_usdt + total_pnl,
            daily_pnl_usdt=total_pnl,
            daily_pnl_pct=(total_pnl / self.initial_capital_usdt * 100)
                           if self.initial_capital_usdt else 0,
            trades_taken=len(self.trades_today),
            winning_trades=len(winning),
            losing_trades=len(self.trades_today) - len(winning),
            max_drawdown_usdt=0.0,
            cumulative_pnl_usdt=total_pnl,
            session_id=self._session_id,
        )
        try:
            self._pm.record_daily_snapshot(snap)
            win_rate = len(winning) / len(self.trades_today) * 100 if self.trades_today else 0
            self._pm.end_session(
                session_id=self._session_id,
                final_capital_usdt=self.initial_capital_usdt + total_pnl,
                metrics={
                    "total_pnl_usdt": total_pnl,
                    "total_trades":   len(self.trades_today),
                    "win_rate":       win_rate,
                    "sharpe_ratio":   0.0,
                    "max_drawdown":   0.0,
                },
            )
            print(f"[CryptoPaper] Session saved. Trades: {len(self.trades_today)}"
                  f"  PnL: ${total_pnl:.2f}")
        except Exception as e:
            print(f"[CryptoPaper] Save error: {e}")
        sys.stdout.flush()
