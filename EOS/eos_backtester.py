"""
EOS Backtester - Modular Backtesting Framework
Backtests the EOS strategy using historical data from Dhan API.
Designed to be modular for future strategy implementations.
"""

import json
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import statistics

from .config import EOS_CONFIG, FNO_STOCKS
from .data_fetcher import EOSDataFetcher
from .eos_strategy_engine import SignalType, ExitReason
from .eos_portfolio_manager import EOSPortfolioManager, TradeRecord, DailySnapshot


@dataclass
class BacktestTrade:
    """Represents a single trade in backtesting."""
    symbol: str
    option_type: str          # "CALL" or "PUT"
    strike_price: float
    entry_date: str
    entry_time: str
    entry_price: float
    exit_date: str
    exit_time: str
    exit_price: float
    quantity: int
    lot_size: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    hold_duration_minutes: float
    price_change_at_entry: float
    oi_change_at_entry: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    start_date: str
    end_date: str
    symbols_tested: List[str]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_win: float
    avg_loss: float
    avg_hold_time_minutes: float
    profit_factor: float
    trades: List[BacktestTrade] = field(default_factory=list)
    daily_pnl: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        result = asdict(self)
        result["trades"] = [t.to_dict() for t in self.trades]
        return result

    def to_json(self, filepath: str = None) -> str:
        json_str = json.dumps(self.to_dict(), indent=2, default=str)
        if filepath:
            with open(filepath, 'w') as f:
                f.write(json_str)
        return json_str


class EOSBacktester:
    """
    Modular backtester for EOS strategy.
    Can be extended for other strategies by subclassing.

    Key Features:
    - Uses expired options historical data from Dhan API
    - Simulates realistic trade execution with slippage
    - Tracks all EOS rules (SL, trailing SL, SMA exit, time exit)
    - Calculates comprehensive performance metrics
    """

    def __init__(self, data_fetcher: EOSDataFetcher = None):
        self.data_fetcher = data_fetcher or EOSDataFetcher()
        self.config = EOS_CONFIG
        self.trades: List[BacktestTrade] = []
        self.daily_pnl: Dict[str, float] = {}

        # Slippage and commission settings
        self.slippage_pct = 0.1      # 0.1% slippage
        self.commission_per_lot = 40  # ₹40 per lot (approx)

    def load_historical_data(self, symbol: str, start_date: str, end_date: str,
                              expiry_code: int = 1) -> Dict:
        """
        Load historical data for backtesting a single symbol.

        Args:
            symbol: Stock symbol (e.g., "RELIANCE")
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            expiry_code: Expiry to use (1=last month, 2=2nd last, etc.)

        Returns:
            Dict with daily_data, options_data (contains both CE and PE)
        """
        stock_info = FNO_STOCKS.get(symbol)
        if not stock_info:
            return {"status": "error", "error": f"Symbol {symbol} not found"}

        equity_id = stock_info["equity_id"]

        # Load daily historical data for previous close prices
        daily_data = self.data_fetcher.get_daily_historical(
            str(equity_id), "NSE_EQ", "EQUITY", start_date, end_date
        )

        # Load expired options data - need separate calls for CALL and PUT
        call_data = self.data_fetcher.get_expired_options_data(
            security_id=equity_id,
            instrument="OPTSTK",
            expiry_flag="MONTH",
            expiry_code=expiry_code,
            strike="ATM",
            option_type="CALL",
            interval="5",
            from_date=start_date,
            to_date=end_date
        )

        put_data = self.data_fetcher.get_expired_options_data(
            security_id=equity_id,
            instrument="OPTSTK",
            expiry_flag="MONTH",
            expiry_code=expiry_code,
            strike="ATM",
            option_type="PUT",
            interval="5",
            from_date=start_date,
            to_date=end_date
        )

        return {
            "status": "success",
            "symbol": symbol,
            "equity_id": equity_id,
            "lot_size": stock_info["lot_size"],
            "daily_data": daily_data,
            "call_data": call_data,
            "put_data": put_data
        }

    def parse_candle_data(self, options_data: Dict, option_type: str = "CALL") -> List[Dict]:
        """
        Parse options data into list of candle dictionaries.

        Args:
            options_data: Response from get_expired_options_data
            option_type: "CALL" or "PUT" to select ce or pe data

        Returns:
            List of candle dicts with timestamp, open, high, low, close, oi, spot
        """
        if options_data.get("status") != "success":
            return []

        raw_data = options_data.get("data", {})
        if not raw_data:
            return []

        # Select CE or PE data based on option_type
        option_key = "ce" if option_type.upper() == "CALL" else "pe"
        option_data = raw_data.get(option_key, {})

        if not option_data:
            return []

        # Extract arrays from response (API uses 'timestamp' not 'start_Time')
        timestamps = option_data.get("timestamp", [])
        opens = option_data.get("open", [])
        highs = option_data.get("high", [])
        lows = option_data.get("low", [])
        closes = option_data.get("close", [])
        ois = option_data.get("oi", [])
        spots = option_data.get("spot", [])
        strikes = option_data.get("strike", [])

        candles = []
        for i in range(len(timestamps)):
            candle = {
                "timestamp": timestamps[i] if i < len(timestamps) else None,
                "open": opens[i] if i < len(opens) else None,
                "high": highs[i] if i < len(highs) else None,
                "low": lows[i] if i < len(lows) else None,
                "close": closes[i] if i < len(closes) else None,
                "oi": ois[i] if i < len(ois) else None,
                "spot": spots[i] if i < len(spots) else None,
                "strike": strikes[i] if i < len(strikes) else None
            }
            candles.append(candle)

        return candles

    def get_daily_close_prices(self, daily_data: Dict) -> Dict[str, float]:
        """
        Extract daily close prices indexed by date.

        Args:
            daily_data: Response from get_daily_historical

        Returns:
            Dict of date -> close price
        """
        if daily_data.get("status") != "success":
            return {}

        # Historical API returns data directly in response, not nested under 'data'
        timestamps = daily_data.get("timestamp", [])
        closes = daily_data.get("close", [])

        if not timestamps or not closes:
            return {}

        daily_closes = {}
        for i, ts in enumerate(timestamps):
            if ts and i < len(closes):
                # Parse epoch timestamp and extract date
                try:
                    dt = datetime.fromtimestamp(ts)
                    date_str = dt.strftime("%Y-%m-%d")
                    daily_closes[date_str] = closes[i]
                except:
                    pass

        return daily_closes

    def simulate_entry(self, candle: Dict, option_type: str) -> Tuple[float, float]:
        """
        Simulate entry execution with slippage.

        Args:
            candle: Entry candle data
            option_type: "CALL" or "PUT"

        Returns:
            Tuple of (entry_price, stop_loss_price)
        """
        # Use open price of next candle + slippage for realistic entry
        base_price = candle.get("open", candle.get("close", 0))
        slippage = base_price * (self.slippage_pct / 100)
        entry_price = base_price + slippage  # Worse price due to slippage

        # Calculate initial stop loss (30% below entry)
        sl_pct = self.config["initial_stop_loss_pct"] / 100
        stop_loss = entry_price * (1 - sl_pct)

        return entry_price, stop_loss

    def simulate_exit(self, candle: Dict, exit_reason: ExitReason) -> float:
        """
        Simulate exit execution with slippage.

        Args:
            candle: Exit candle data
            exit_reason: Reason for exit

        Returns:
            Exit price after slippage
        """
        if exit_reason in [ExitReason.INITIAL_STOP_LOSS, ExitReason.TRAILING_STOP_LOSS]:
            # Stop loss hit - use low of candle
            base_price = candle.get("low", candle.get("close", 0))
        else:
            # Normal exit - use close
            base_price = candle.get("close", 0)

        # Apply negative slippage (worse exit)
        slippage = base_price * (self.slippage_pct / 100)
        return base_price - slippage

    def check_entry_conditions_backtest(self, spot_price: float, prev_close: float,
                                         current_oi: int, prev_oi: int) -> Tuple[bool, str]:
        """
        Check if entry conditions are met during backtest.

        Returns:
            Tuple of (should_enter, signal_direction: "PUT" or "CALL" or "")
        """
        # Calculate price change %
        if prev_close == 0:
            return False, ""

        price_change_pct = ((spot_price - prev_close) / prev_close) * 100

        # Calculate OI change %
        if prev_oi == 0:
            return False, ""

        oi_change_pct = ((current_oi - prev_oi) / prev_oi) * 100

        # Check thresholds
        price_threshold = self.config["price_change_threshold"]
        oi_threshold = self.config["oi_change_threshold"]

        if abs(price_change_pct) > price_threshold and abs(oi_change_pct) > oi_threshold:
            # Contrarian: if price went up strongly, buy PUT
            if price_change_pct > price_threshold:
                return True, "PUT"
            elif price_change_pct < -price_threshold:
                return True, "CALL"

        return False, ""

    def run_backtest_single_symbol(self, symbol: str, start_date: str, end_date: str,
                                    expiry_code: int = 1) -> List[BacktestTrade]:
        """
        Run backtest for a single symbol.

        Args:
            symbol: Stock symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            expiry_code: Expiry to use

        Returns:
            List of BacktestTrade objects
        """
        print(f"\n{'='*50}")
        print(f"Backtesting {symbol}: {start_date} to {end_date}")
        print(f"{'='*50}")

        # Load data
        data = self.load_historical_data(symbol, start_date, end_date, expiry_code)
        if data.get("status") != "success":
            print(f"Failed to load data for {symbol}")
            return []

        # Parse candles - separate call and put data
        call_candles = self.parse_candle_data(data.get("call_data", {}), "CALL")
        put_candles = self.parse_candle_data(data.get("put_data", {}), "PUT")
        daily_closes = self.get_daily_close_prices(data.get("daily_data", {}))

        if not call_candles and not put_candles:
            print(f"No option candle data for {symbol}")
            return []

        print(f"Loaded {len(call_candles)} CALL candles, {len(put_candles)} PUT candles")
        print(f"Loaded {len(daily_closes)} daily close prices")

        lot_size = data.get("lot_size", 1)
        trades = []

        # Use the longer of the two for iteration
        candles = call_candles if len(call_candles) >= len(put_candles) else put_candles

        # Track state
        in_position = False
        position_type = None
        entry_price = 0
        entry_time = None
        entry_date = None
        stop_loss = 0
        trailing_sl = 0
        highest_price = 0
        price_change_at_entry = 0
        oi_change_at_entry = 0
        prev_day_oi = None
        current_date = None
        daily_oi_start = {}

        for i, candle in enumerate(candles):
            if not candle.get("timestamp"):
                continue

            try:
                # Timestamp is epoch integer, convert to datetime
                epoch_ts = candle["timestamp"]
                ts = datetime.fromtimestamp(epoch_ts)
            except:
                continue

            date_str = ts.strftime("%Y-%m-%d")
            time_str = ts.strftime("%H:%M")

            # Track daily OI start
            if date_str != current_date:
                current_date = date_str
                if current_date not in daily_oi_start:
                    daily_oi_start[current_date] = candle.get("oi", 0)

            spot = candle.get("spot", 0)
            current_oi = candle.get("oi", 0)

            # Get previous day close
            prev_date = (ts - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_close = daily_closes.get(prev_date, spot * 0.98)  # Fallback

            # Get previous day OI (use start of current day as proxy)
            prev_oi = daily_oi_start.get(prev_date, current_oi)

            if not in_position:
                # Check entry conditions
                should_enter, signal_type = self.check_entry_conditions_backtest(
                    spot, prev_close, current_oi, prev_oi
                )

                if should_enter and ts.time() >= dt_time(9, 15) and ts.time() <= dt_time(14, 30):
                    # Select correct option data
                    if signal_type == "PUT" and i < len(put_candles):
                        opt_candle = put_candles[i]
                    elif signal_type == "CALL" and i < len(call_candles):
                        opt_candle = call_candles[i]
                    else:
                        continue

                    entry_price, stop_loss = self.simulate_entry(opt_candle, signal_type)
                    trailing_sl = stop_loss
                    highest_price = entry_price
                    entry_time = time_str
                    entry_date = date_str
                    position_type = signal_type
                    in_position = True

                    # Track entry conditions
                    price_change_at_entry = ((spot - prev_close) / prev_close) * 100
                    oi_change_at_entry = ((current_oi - prev_oi) / prev_oi) * 100 if prev_oi else 0

                    print(f"  ENTRY: {date_str} {time_str} - {signal_type} @ ₹{entry_price:.2f}")
                    print(f"         Price Δ: {price_change_at_entry:.2f}%, OI Δ: {oi_change_at_entry:.2f}%")

            else:
                # In position - check exits
                if position_type == "PUT" and i < len(put_candles):
                    opt_candle = put_candles[i]
                elif position_type == "CALL" and i < len(call_candles):
                    opt_candle = call_candles[i]
                else:
                    continue

                current_price = opt_candle.get("close", 0)
                low_price = opt_candle.get("low", current_price)

                # Update trailing SL
                if current_price > highest_price:
                    highest_price = current_price
                    profit = current_price - entry_price
                    if profit >= self.config["trailing_sl_trigger"]:
                        trail_steps = int(profit / self.config["trailing_sl_trigger"])
                        new_sl = entry_price + (trail_steps - 1) * self.config["trailing_sl_amount"]
                        if new_sl > trailing_sl:
                            trailing_sl = new_sl

                exit_reason = None
                exit_price = 0

                # Check stop losses
                if low_price <= stop_loss:
                    exit_reason = ExitReason.INITIAL_STOP_LOSS
                    exit_price = stop_loss
                elif low_price <= trailing_sl and trailing_sl > stop_loss:
                    exit_reason = ExitReason.TRAILING_STOP_LOSS
                    exit_price = trailing_sl
                # Time exit at 3:18 PM
                elif ts.time() >= dt_time(15, 18):
                    exit_reason = ExitReason.TIME_EXIT
                    exit_price = self.simulate_exit(opt_candle, exit_reason)

                if exit_reason:
                    pnl = (exit_price - entry_price) * lot_size * self.config["lots_per_trade"]
                    pnl -= self.commission_per_lot * self.config["lots_per_trade"] * 2  # Entry + exit
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100

                    entry_dt = datetime.strptime(f"{entry_date} {entry_time}", "%Y-%m-%d %H:%M")
                    hold_duration = (ts - entry_dt).total_seconds() / 60

                    trade = BacktestTrade(
                        symbol=symbol,
                        option_type=position_type,
                        strike_price=opt_candle.get("strike", 0),
                        entry_date=entry_date,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        exit_date=date_str,
                        exit_time=time_str,
                        exit_price=exit_price,
                        quantity=lot_size * self.config["lots_per_trade"],
                        lot_size=lot_size,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason.value,
                        hold_duration_minutes=hold_duration,
                        price_change_at_entry=price_change_at_entry,
                        oi_change_at_entry=oi_change_at_entry
                    )
                    trades.append(trade)

                    print(f"  EXIT:  {date_str} {time_str} - {exit_reason.value} @ ₹{exit_price:.2f}")
                    print(f"         PnL: ₹{pnl:.2f} ({pnl_pct:.2f}%)")

                    in_position = False
                    position_type = None

        print(f"\n{symbol}: {len(trades)} trades completed")
        return trades

    def run_backtest(self, symbols: List[str] = None, start_date: str = None,
                      end_date: str = None, expiry_code: int = 1) -> BacktestResult:
        """
        Run backtest across multiple symbols.

        Args:
            symbols: List of symbols to test (default: all FNO_STOCKS)
            start_date: Start date (default: 30 days ago)
            end_date: End date (default: today)
            expiry_code: Expiry to use

        Returns:
            BacktestResult with all trades and metrics
        """
        if symbols is None:
            symbols = list(FNO_STOCKS.keys())

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        print(f"\n{'='*60}")
        print(f"EOS BACKTEST: {start_date} to {end_date}")
        print(f"Symbols: {len(symbols)}")
        print(f"{'='*60}")

        all_trades = []

        for symbol in symbols:
            try:
                trades = self.run_backtest_single_symbol(symbol, start_date, end_date, expiry_code)
                all_trades.extend(trades)
            except Exception as e:
                print(f"Error backtesting {symbol}: {e}")

        # Calculate metrics
        result = self.calculate_metrics(all_trades, symbols, start_date, end_date)

        return result

    def calculate_metrics(self, trades: List[BacktestTrade], symbols: List[str],
                          start_date: str, end_date: str) -> BacktestResult:
        """
        Calculate performance metrics from trades.

        Args:
            trades: List of BacktestTrade objects
            symbols: Symbols tested
            start_date: Backtest start date
            end_date: Backtest end date

        Returns:
            BacktestResult with all metrics
        """
        if not trades:
            return BacktestResult(
                start_date=start_date,
                end_date=end_date,
                symbols_tested=symbols,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                avg_hold_time_minutes=0.0,
                profit_factor=0.0,
                trades=[],
                daily_pnl={}
            )

        # Basic stats
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in trades)

        win_rate = len(winning) / len(trades) * 100 if trades else 0
        avg_win = statistics.mean([t.pnl for t in winning]) if winning else 0
        avg_loss = statistics.mean([t.pnl for t in losing]) if losing else 0
        avg_hold = statistics.mean([t.hold_duration_minutes for t in trades]) if trades else 0

        # Profit factor
        gross_profit = sum(t.pnl for t in winning) if winning else 0
        gross_loss = abs(sum(t.pnl for t in losing)) if losing else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Daily PnL and drawdown
        daily_pnl = {}
        for t in trades:
            date = t.exit_date
            daily_pnl[date] = daily_pnl.get(date, 0) + t.pnl

        # Calculate drawdown
        cumulative = 0
        peak = 0
        max_drawdown = 0

        for date in sorted(daily_pnl.keys()):
            cumulative += daily_pnl[date]
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        max_dd_pct = (max_drawdown / peak * 100) if peak > 0 else 0

        # Sharpe Ratio (simplified - using daily returns)
        daily_returns = list(daily_pnl.values())
        if len(daily_returns) > 1:
            avg_return = statistics.mean(daily_returns)
            std_return = statistics.stdev(daily_returns)
            sharpe = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
        else:
            sharpe = 0

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            symbols_tested=symbols,
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_hold_time_minutes=avg_hold,
            profit_factor=profit_factor,
            trades=trades,
            daily_pnl=daily_pnl
        )

    def print_summary(self, result: BacktestResult):
        """Print formatted backtest summary."""
        print(f"\n{'='*60}")
        print("BACKTEST SUMMARY")
        print(f"{'='*60}")
        print(f"Period: {result.start_date} to {result.end_date}")
        print(f"Symbols Tested: {len(result.symbols_tested)}")
        print(f"\n--- TRADE STATISTICS ---")
        print(f"Total Trades: {result.total_trades}")
        print(f"Winning Trades: {result.winning_trades}")
        print(f"Losing Trades: {result.losing_trades}")
        print(f"Win Rate: {result.win_rate:.1f}%")
        print(f"Avg Hold Time: {result.avg_hold_time_minutes:.1f} minutes")
        print(f"\n--- P&L STATISTICS ---")
        print(f"Total P&L: ₹{result.total_pnl:,.2f}")
        print(f"Avg Win: ₹{result.avg_win:,.2f}")
        print(f"Avg Loss: ₹{result.avg_loss:,.2f}")
        print(f"Profit Factor: {result.profit_factor:.2f}")
        print(f"\n--- RISK METRICS ---")
        print(f"Max Drawdown: ₹{result.max_drawdown:,.2f}")
        print(f"Max Drawdown %: {result.max_drawdown_pct:.1f}%")
        print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"{'='*60}")

        # Exit reason breakdown
        if result.trades:
            print(f"\n--- EXIT REASON BREAKDOWN ---")
            exit_counts = {}
            for t in result.trades:
                exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
            for reason, count in sorted(exit_counts.items()):
                pct = count / len(result.trades) * 100
                print(f"{reason}: {count} ({pct:.1f}%)")


    def save_results_to_db(self, result: BacktestResult, initial_capital: float = 100000):
        """
        Save backtest results to portfolio database so dashboard can display them.

        Args:
            result: BacktestResult from run_backtest()
            initial_capital: Starting capital for the backtest
        """
        pm = EOSPortfolioManager()

        # 1. Start backtest session
        backtest_id = pm.start_backtest(
            start_date=result.start_date,
            end_date=result.end_date,
            symbols=result.symbols_tested,
            initial_capital=initial_capital,
            config={"mode": "BACKTEST", **{k: v for k, v in self.config.items() if isinstance(v, (str, int, float, bool))}}
        )
        print(f"[DB] Session created: {backtest_id}")

        # 2. Record each trade
        for i, t in enumerate(result.trades):
            trade_id = f"{backtest_id}_T{i+1:04d}"
            trade_record = TradeRecord(
                trade_id=trade_id,
                symbol=t.symbol,
                option_type=t.option_type,
                strike_price=t.strike_price,
                entry_date=t.entry_date,
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                exit_date=t.exit_date,
                exit_time=t.exit_time,
                exit_price=t.exit_price,
                quantity=t.quantity,
                lot_size=t.lot_size,
                pnl=t.pnl,
                pnl_pct=t.pnl_pct,
                exit_reason=t.exit_reason,
                hold_duration_minutes=t.hold_duration_minutes,
                price_change_at_entry=t.price_change_at_entry,
                oi_change_at_entry=t.oi_change_at_entry,
                backtest_id=backtest_id
            )
            pm.record_trade(trade_record)
        print(f"[DB] {len(result.trades)} trades recorded")

        # 3. Record daily snapshots from daily_pnl
        cumulative_pnl = 0.0
        for date in sorted(result.daily_pnl.keys()):
            day_pnl = result.daily_pnl[date]
            cumulative_pnl += day_pnl

            # Count trades for this day
            day_trades = [t for t in result.trades if t.exit_date == date]
            day_winners = [t for t in day_trades if t.pnl > 0]
            day_losers = [t for t in day_trades if t.pnl <= 0]

            snapshot = DailySnapshot(
                date=date,
                starting_capital=initial_capital + cumulative_pnl - day_pnl,
                ending_capital=initial_capital + cumulative_pnl,
                daily_pnl=day_pnl,
                daily_pnl_pct=(day_pnl / initial_capital * 100) if initial_capital > 0 else 0,
                trades_taken=len(day_trades),
                winning_trades=len(day_winners),
                losing_trades=len(day_losers),
                max_drawdown=result.max_drawdown,
                cumulative_pnl=cumulative_pnl,
                backtest_id=backtest_id
            )
            pm.record_daily_snapshot(snapshot)
        print(f"[DB] {len(result.daily_pnl)} daily snapshots recorded")

        # 4. End backtest with final metrics
        final_capital = initial_capital + result.total_pnl
        metrics = {
            'total_pnl': result.total_pnl,
            'total_trades': result.total_trades,
            'win_rate': result.win_rate,
            'sharpe_ratio': result.sharpe_ratio,
            'max_drawdown': result.max_drawdown,
        }
        pm.end_backtest(
            backtest_id=backtest_id,
            final_capital=final_capital,
            metrics=metrics
        )
        print(f"[DB] Session {backtest_id} completed and saved")
        return backtest_id