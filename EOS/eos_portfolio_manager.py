"""
EOS Portfolio Manager - Database & Trade Management System

Manages all trades, positions, and performance metrics during backtests.
Supports backtests from 2 days to 1 year with persistent SQLite storage.

Features:
- Complete trade table with every single trade
- Daily P&L breakdown
- Symbol-wise performance
- Export to CSV/JSON
- Formatted reports at end of backtest
"""

import sqlite3
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import os


@dataclass
class TradeRecord:
    """Single trade record for database storage."""
    trade_id: str
    symbol: str
    option_type: str  # CALL or PUT
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
    backtest_id: str


@dataclass
class DailySnapshot:
    """Daily portfolio snapshot."""
    date: str
    starting_capital: float
    ending_capital: float
    daily_pnl: float
    daily_pnl_pct: float
    trades_taken: int
    winning_trades: int
    losing_trades: int
    max_drawdown: float
    cumulative_pnl: float
    backtest_id: str


class EOSPortfolioManager:
    """
    Portfolio Manager - Central database for EOS trading system.
    
    Features:
    - SQLite database for persistent storage
    - Trade history tracking
    - Position management
    - Daily/weekly/monthly performance
    - Equity curve tracking
    - Risk metrics calculation
    - Export to JSON/CSV
    """
    
    def __init__(self, db_path: str = None):
        """Initialize Portfolio Manager with database connection."""
        if db_path is None:
            # Default to EOS folder
            eos_dir = Path(__file__).parent
            db_path = str(eos_dir / "portfolio.db")
        
        self.db_path = db_path
        self.conn = None
        self.current_backtest_id = None
        self._init_database()
    
    def _init_database(self):
        """Initialize SQLite database with required tables."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        # Backtests table - tracks each backtest run
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtests (
                backtest_id TEXT PRIMARY KEY,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                symbols TEXT NOT NULL,
                initial_capital REAL DEFAULT 100000,
                final_capital REAL,
                total_pnl REAL,
                total_trades INTEGER,
                win_rate REAL,
                sharpe_ratio REAL,
                max_drawdown REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                config_json TEXT
            )
        """)
        
        # Trades table - all individual trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                backtest_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike_price REAL,
                entry_date TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_date TEXT,
                exit_time TEXT,
                exit_price REAL,
                quantity INTEGER NOT NULL,
                lot_size INTEGER NOT NULL,
                pnl REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                hold_duration_minutes REAL,
                price_change_at_entry REAL,
                oi_change_at_entry REAL,
                status TEXT DEFAULT 'OPEN',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id)
            )
        """)
        
        # Daily snapshots table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id TEXT NOT NULL,
                date TEXT NOT NULL,
                starting_capital REAL,
                ending_capital REAL,
                daily_pnl REAL,
                daily_pnl_pct REAL,
                trades_taken INTEGER,
                winning_trades INTEGER,
                losing_trades INTEGER,
                max_drawdown REAL,
                cumulative_pnl REAL,
                FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id),
                UNIQUE(backtest_id, date)
            )
        """)
        
        # Positions table - open positions tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                option_type TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                lot_size INTEGER NOT NULL,
                current_price REAL,
                unrealized_pnl REAL,
                stop_loss REAL,
                trailing_stop REAL,
                highest_price REAL,
                status TEXT DEFAULT 'OPEN',
                FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id)
            )
        """)
        
        self.conn.commit()

    # ===== BACKTEST MANAGEMENT =====

    def start_backtest(self, start_date: str, end_date: str, symbols: List[str],
                       initial_capital: float = 100000, config: Dict = None) -> str:
        """
        Start a new backtest session.

        Args:
            start_date: Backtest start date (YYYY-MM-DD)
            end_date: Backtest end date (YYYY-MM-DD)
            symbols: List of symbols being tested
            initial_capital: Starting capital
            config: Strategy configuration dict

        Returns:
            backtest_id: Unique identifier for this backtest
        """
        backtest_id = f"BT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.current_backtest_id = backtest_id

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO backtests (backtest_id, start_date, end_date, symbols,
                                   initial_capital, config_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            backtest_id,
            start_date,
            end_date,
            json.dumps(symbols),
            initial_capital,
            json.dumps(config) if config else None
        ))
        self.conn.commit()

        return backtest_id

    def end_backtest(self, backtest_id: str = None, final_capital: float = None,
                     metrics: Dict = None):
        """
        End a backtest session and save final metrics.

        Args:
            backtest_id: Backtest to end (uses current if None)
            final_capital: Final portfolio value
            metrics: Dict with total_pnl, total_trades, win_rate, sharpe_ratio, max_drawdown
        """
        backtest_id = backtest_id or self.current_backtest_id
        if not backtest_id:
            return

        cursor = self.conn.cursor()

        if metrics:
            cursor.execute("""
                UPDATE backtests
                SET final_capital = ?,
                    total_pnl = ?,
                    total_trades = ?,
                    win_rate = ?,
                    sharpe_ratio = ?,
                    max_drawdown = ?
                WHERE backtest_id = ?
            """, (
                final_capital,
                metrics.get('total_pnl', 0),
                metrics.get('total_trades', 0),
                metrics.get('win_rate', 0),
                metrics.get('sharpe_ratio', 0),
                metrics.get('max_drawdown', 0),
                backtest_id
            ))

        self.conn.commit()
        self.current_backtest_id = None

    # ===== TRADE MANAGEMENT =====

    def record_trade(self, trade: TradeRecord) -> int:
        """
        Record a completed trade in the database.

        Args:
            trade: TradeRecord object with all trade details

        Returns:
            trade_id: Database ID of the inserted trade
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO trades (trade_id, backtest_id, symbol, option_type, strike_price,
                               entry_date, entry_time, entry_price, exit_date, exit_time,
                               exit_price, quantity, lot_size, pnl, pnl_pct, exit_reason,
                               hold_duration_minutes, price_change_at_entry, oi_change_at_entry,
                               status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED')
        """, (
            trade.trade_id, trade.backtest_id, trade.symbol, trade.option_type,
            trade.strike_price, trade.entry_date, trade.entry_time, trade.entry_price,
            trade.exit_date, trade.exit_time, trade.exit_price, trade.quantity,
            trade.lot_size, trade.pnl, trade.pnl_pct, trade.exit_reason,
            trade.hold_duration_minutes, trade.price_change_at_entry, trade.oi_change_at_entry
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_trades(self, backtest_id: str = None, symbol: str = None,
                   start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        Retrieve trades with optional filters.

        Args:
            backtest_id: Filter by backtest
            symbol: Filter by symbol
            start_date: Filter by entry date >= start_date
            end_date: Filter by entry date <= end_date

        Returns:
            List of trade dictionaries
        """
        cursor = self.conn.cursor()

        query = "SELECT * FROM trades WHERE 1=1"
        params = []

        if backtest_id:
            query += " AND backtest_id = ?"
            params.append(backtest_id)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_date:
            query += " AND entry_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND entry_date <= ?"
            params.append(end_date)

        query += " ORDER BY entry_date, entry_time"

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # ===== DAILY SNAPSHOT MANAGEMENT =====

    def record_daily_snapshot(self, snapshot: DailySnapshot):
        """Record end-of-day portfolio snapshot."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_snapshots
            (backtest_id, date, starting_capital, ending_capital, daily_pnl,
             daily_pnl_pct, trades_taken, winning_trades, losing_trades,
             max_drawdown, cumulative_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.backtest_id, snapshot.date, snapshot.starting_capital,
            snapshot.ending_capital, snapshot.daily_pnl, snapshot.daily_pnl_pct,
            snapshot.trades_taken, snapshot.winning_trades, snapshot.losing_trades,
            snapshot.max_drawdown, snapshot.cumulative_pnl
        ))
        self.conn.commit()

    def get_daily_snapshots(self, backtest_id: str) -> List[Dict]:
        """Get all daily snapshots for a backtest."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM daily_snapshots
            WHERE backtest_id = ?
            ORDER BY date
        """, (backtest_id,))
        return [dict(row) for row in cursor.fetchall()]

    # ===== POSITION MANAGEMENT =====

    def open_position(self, backtest_id: str, symbol: str, option_type: str,
                      entry_date: str, entry_time: str, entry_price: float,
                      quantity: int, lot_size: int, stop_loss: float) -> int:
        """
        Open a new position.

        Returns:
            position_id: Database ID of the position
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO positions (backtest_id, symbol, option_type, entry_date,
                                   entry_time, entry_price, quantity, lot_size,
                                   current_price, stop_loss, trailing_stop, highest_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            backtest_id, symbol, option_type, entry_date, entry_time,
            entry_price, quantity, lot_size, entry_price, stop_loss,
            stop_loss, entry_price
        ))
        self.conn.commit()
        return cursor.lastrowid

    def update_position(self, position_id: int, current_price: float = None,
                        trailing_stop: float = None, highest_price: float = None):
        """Update position with current market data."""
        cursor = self.conn.cursor()

        updates = []
        params = []

        if current_price is not None:
            updates.append("current_price = ?")
            params.append(current_price)
            # Calculate unrealized P&L
            cursor.execute("SELECT entry_price, quantity, lot_size FROM positions WHERE id = ?",
                          (position_id,))
            row = cursor.fetchone()
            if row:
                unrealized = (current_price - row['entry_price']) * row['quantity'] * row['lot_size']
                updates.append("unrealized_pnl = ?")
                params.append(unrealized)

        if trailing_stop is not None:
            updates.append("trailing_stop = ?")
            params.append(trailing_stop)

        if highest_price is not None:
            updates.append("highest_price = ?")
            params.append(highest_price)

        if updates:
            params.append(position_id)
            cursor.execute(f"UPDATE positions SET {', '.join(updates)} WHERE id = ?", params)
            self.conn.commit()

    def close_position(self, position_id: int):
        """Mark position as closed."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE positions SET status = 'CLOSED' WHERE id = ?", (position_id,))
        self.conn.commit()

    def get_open_positions(self, backtest_id: str = None) -> List[Dict]:
        """Get all open positions."""
        cursor = self.conn.cursor()
        query = "SELECT * FROM positions WHERE status = 'OPEN'"
        params = []

        if backtest_id:
            query += " AND backtest_id = ?"
            params.append(backtest_id)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # ===== ANALYTICS & METRICS =====

    def get_equity_curve(self, backtest_id: str) -> List[Tuple[str, float]]:
        """
        Get equity curve data for plotting.

        Returns:
            List of (date, capital) tuples
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT date, ending_capital
            FROM daily_snapshots
            WHERE backtest_id = ?
            ORDER BY date
        """, (backtest_id,))
        return [(row['date'], row['ending_capital']) for row in cursor.fetchall()]

    def get_performance_by_symbol(self, backtest_id: str) -> Dict[str, Dict]:
        """Get performance breakdown by symbol."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT symbol,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl,
                   MAX(pnl) as best_trade,
                   MIN(pnl) as worst_trade
            FROM trades
            WHERE backtest_id = ?
            GROUP BY symbol
        """, (backtest_id,))

        return {row['symbol']: dict(row) for row in cursor.fetchall()}

    def get_performance_by_day_of_week(self, backtest_id: str) -> Dict[str, Dict]:
        """Get performance breakdown by day of week."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT strftime('%w', entry_date) as day_num,
                   CASE strftime('%w', entry_date)
                       WHEN '0' THEN 'Sunday'
                       WHEN '1' THEN 'Monday'
                       WHEN '2' THEN 'Tuesday'
                       WHEN '3' THEN 'Wednesday'
                       WHEN '4' THEN 'Thursday'
                       WHEN '5' THEN 'Friday'
                       WHEN '6' THEN 'Saturday'
                   END as day_name,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                   SUM(pnl) as total_pnl
            FROM trades
            WHERE backtest_id = ?
            GROUP BY day_num
            ORDER BY day_num
        """, (backtest_id,))

        return {row['day_name']: dict(row) for row in cursor.fetchall()}

    def get_performance_by_exit_reason(self, backtest_id: str) -> Dict[str, Dict]:
        """Get performance breakdown by exit reason."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT exit_reason,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl
            FROM trades
            WHERE backtest_id = ?
            GROUP BY exit_reason
        """, (backtest_id,))

        return {row['exit_reason']: dict(row) for row in cursor.fetchall()}

    def get_monthly_performance(self, backtest_id: str) -> Dict[str, Dict]:
        """Get performance breakdown by month."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT strftime('%Y-%m', entry_date) as month,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl
            FROM trades
            WHERE backtest_id = ?
            GROUP BY month
            ORDER BY month
        """, (backtest_id,))

        return {row['month']: dict(row) for row in cursor.fetchall()}

    def calculate_drawdown(self, backtest_id: str) -> Tuple[float, str, str]:
        """
        Calculate maximum drawdown for a backtest.

        Returns:
            Tuple of (max_drawdown_amount, peak_date, trough_date)
        """
        snapshots = self.get_daily_snapshots(backtest_id)
        if not snapshots:
            return 0.0, "", ""

        peak = snapshots[0]['ending_capital']
        peak_date = snapshots[0]['date']
        max_drawdown = 0.0
        trough_date = ""

        for snap in snapshots:
            capital = snap['ending_capital']
            if capital > peak:
                peak = capital
                peak_date = snap['date']

            drawdown = peak - capital
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                trough_date = snap['date']

        return max_drawdown, peak_date, trough_date

    # ===== EXPORT FUNCTIONS =====

    def export_trades_to_csv(self, backtest_id: str, filepath: str):
        """Export trades to CSV file."""
        import csv

        trades = self.get_trades(backtest_id=backtest_id)
        if not trades:
            return

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)

    def export_to_json(self, backtest_id: str, filepath: str):
        """Export complete backtest data to JSON."""
        cursor = self.conn.cursor()

        # Get backtest info
        cursor.execute("SELECT * FROM backtests WHERE backtest_id = ?", (backtest_id,))
        backtest = dict(cursor.fetchone()) if cursor.fetchone() else {}

        data = {
            "backtest": backtest,
            "trades": self.get_trades(backtest_id=backtest_id),
            "daily_snapshots": self.get_daily_snapshots(backtest_id),
            "performance_by_symbol": self.get_performance_by_symbol(backtest_id),
            "performance_by_day": self.get_performance_by_day_of_week(backtest_id),
            "performance_by_exit": self.get_performance_by_exit_reason(backtest_id),
            "monthly_performance": self.get_monthly_performance(backtest_id)
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def get_backtest_summary(self, backtest_id: str) -> Dict:
        """Get comprehensive summary of a backtest."""
        cursor = self.conn.cursor()

        # Basic info
        cursor.execute("SELECT * FROM backtests WHERE backtest_id = ?", (backtest_id,))
        row = cursor.fetchone()
        backtest = dict(row) if row else {}

        # Trade stats
        trades = self.get_trades(backtest_id=backtest_id)
        winning = [t for t in trades if t['pnl'] > 0]
        losing = [t for t in trades if t['pnl'] <= 0]

        # Drawdown
        max_dd, peak_date, trough_date = self.calculate_drawdown(backtest_id)

        return {
            "backtest_id": backtest_id,
            "period": f"{backtest.get('start_date')} to {backtest.get('end_date')}",
            "symbols": json.loads(backtest.get('symbols', '[]')),
            "initial_capital": backtest.get('initial_capital', 100000),
            "final_capital": backtest.get('final_capital'),
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(trades) * 100 if trades else 0,
            "total_pnl": sum(t['pnl'] for t in trades),
            "avg_win": sum(t['pnl'] for t in winning) / len(winning) if winning else 0,
            "avg_loss": sum(t['pnl'] for t in losing) / len(losing) if losing else 0,
            "max_drawdown": max_dd,
            "drawdown_period": f"{peak_date} to {trough_date}",
            "performance_by_symbol": self.get_performance_by_symbol(backtest_id),
            "performance_by_exit": self.get_performance_by_exit_reason(backtest_id)
        }

    # ===== UTILITY FUNCTIONS =====

    def list_backtests(self) -> List[Dict]:
        """List all backtests in the database."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT backtest_id, start_date, end_date, symbols,
                   total_trades, total_pnl, win_rate, created_at
            FROM backtests
            ORDER BY created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def delete_backtest(self, backtest_id: str):
        """Delete a backtest and all associated data."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM trades WHERE backtest_id = ?", (backtest_id,))
        cursor.execute("DELETE FROM daily_snapshots WHERE backtest_id = ?", (backtest_id,))
        cursor.execute("DELETE FROM positions WHERE backtest_id = ?", (backtest_id,))
        cursor.execute("DELETE FROM backtests WHERE backtest_id = ?", (backtest_id,))
        self.conn.commit()

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ===== REPORTING & DISPLAY =====

    def print_trade_table(self, backtest_id: str, max_trades: int = None):
        """
        Print a formatted table of ALL trades from a backtest.

        Args:
            backtest_id: Backtest to display
            max_trades: Limit number of trades shown (None = all)
        """
        trades = self.get_trades(backtest_id=backtest_id)

        if not trades:
            print("No trades found for this backtest.")
            return

        # Header
        print("\n" + "=" * 140)
        print("COMPLETE TRADE TABLE")
        print("=" * 140)
        print(f"{'#':<4} {'Date':<12} {'Time':<6} {'Symbol':<12} {'Type':<6} {'Entry':<10} "
              f"{'Exit':<10} {'P&L':>12} {'P&L%':>8} {'Exit Reason':<15} {'Hold(min)':<10}")
        print("-" * 140)

        # Trades
        total_pnl = 0
        trades_to_show = trades[:max_trades] if max_trades else trades

        for i, t in enumerate(trades_to_show, 1):
            pnl = t['pnl'] or 0
            total_pnl += pnl
            pnl_str = f"₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
            pnl_pct = t['pnl_pct'] or 0

            print(f"{i:<4} {t['entry_date']:<12} {t['entry_time']:<6} {t['symbol']:<12} "
                  f"{t['option_type']:<6} ₹{t['entry_price']:<9.2f} ₹{t['exit_price'] or 0:<9.2f} "
                  f"{pnl_str:>12} {pnl_pct:>7.2f}% {t['exit_reason'] or 'OPEN':<15} "
                  f"{t['hold_duration_minutes'] or 0:<10.0f}")

        if max_trades and len(trades) > max_trades:
            print(f"... and {len(trades) - max_trades} more trades")

        # Footer
        print("-" * 140)
        total_str = f"₹{total_pnl:,.2f}" if total_pnl >= 0 else f"-₹{abs(total_pnl):,.2f}"
        print(f"{'TOTAL':<4} {'':<12} {'':<6} {'':<12} {'':<6} {'':<10} {'':<10} {total_str:>12}")
        print("=" * 140)

    def print_daily_breakdown(self, backtest_id: str):
        """Print daily P&L breakdown."""
        trades = self.get_trades(backtest_id=backtest_id)

        if not trades:
            print("No trades found.")
            return

        # Group by date
        daily = {}
        for t in trades:
            date = t['entry_date']
            if date not in daily:
                daily[date] = {'trades': 0, 'wins': 0, 'pnl': 0}
            daily[date]['trades'] += 1
            daily[date]['pnl'] += t['pnl'] or 0
            if (t['pnl'] or 0) > 0:
                daily[date]['wins'] += 1

        print("\n" + "=" * 80)
        print("DAILY P&L BREAKDOWN")
        print("=" * 80)
        print(f"{'Date':<15} {'Trades':<10} {'Wins':<10} {'Win%':<10} {'Daily P&L':>15}")
        print("-" * 80)

        cumulative = 0
        for date in sorted(daily.keys()):
            d = daily[date]
            win_pct = (d['wins'] / d['trades'] * 100) if d['trades'] > 0 else 0
            cumulative += d['pnl']
            pnl_str = f"₹{d['pnl']:,.2f}" if d['pnl'] >= 0 else f"-₹{abs(d['pnl']):,.2f}"
            print(f"{date:<15} {d['trades']:<10} {d['wins']:<10} {win_pct:<9.1f}% {pnl_str:>15}")

        print("-" * 80)
        cum_str = f"₹{cumulative:,.2f}" if cumulative >= 0 else f"-₹{abs(cumulative):,.2f}"
        print(f"{'CUMULATIVE':<15} {sum(d['trades'] for d in daily.values()):<10} "
              f"{sum(d['wins'] for d in daily.values()):<10} {'':<10} {cum_str:>15}")
        print("=" * 80)

    def print_symbol_breakdown(self, backtest_id: str):
        """Print symbol-wise performance breakdown."""
        perf = self.get_performance_by_symbol(backtest_id)

        if not perf:
            print("No trades found.")
            return

        print("\n" + "=" * 100)
        print("SYMBOL-WISE PERFORMANCE")
        print("=" * 100)
        print(f"{'Symbol':<15} {'Trades':<10} {'Wins':<10} {'Win%':<10} {'Total P&L':>15} "
              f"{'Avg P&L':>12} {'Best':>12} {'Worst':>12}")
        print("-" * 100)

        for symbol, p in sorted(perf.items()):
            win_pct = (p['winning_trades'] / p['total_trades'] * 100) if p['total_trades'] > 0 else 0
            total_str = f"₹{p['total_pnl']:,.2f}" if p['total_pnl'] >= 0 else f"-₹{abs(p['total_pnl']):,.2f}"
            avg_str = f"₹{p['avg_pnl']:,.2f}" if p['avg_pnl'] >= 0 else f"-₹{abs(p['avg_pnl']):,.2f}"
            best_str = f"₹{p['best_trade']:,.2f}" if p['best_trade'] >= 0 else f"-₹{abs(p['best_trade']):,.2f}"
            worst_str = f"₹{p['worst_trade']:,.2f}" if p['worst_trade'] >= 0 else f"-₹{abs(p['worst_trade']):,.2f}"

            print(f"{symbol:<15} {p['total_trades']:<10} {p['winning_trades']:<10} {win_pct:<9.1f}% "
                  f"{total_str:>15} {avg_str:>12} {best_str:>12} {worst_str:>12}")

        print("=" * 100)

    def print_exit_reason_breakdown(self, backtest_id: str):
        """Print exit reason breakdown."""
        perf = self.get_performance_by_exit_reason(backtest_id)

        if not perf:
            print("No trades found.")
            return

        print("\n" + "=" * 80)
        print("EXIT REASON BREAKDOWN")
        print("=" * 80)
        print(f"{'Exit Reason':<20} {'Trades':<10} {'Wins':<10} {'Win%':<10} {'Total P&L':>15}")
        print("-" * 80)

        for reason, p in sorted(perf.items()):
            win_pct = (p['winning_trades'] / p['total_trades'] * 100) if p['total_trades'] > 0 else 0
            pnl_str = f"₹{p['total_pnl']:,.2f}" if p['total_pnl'] >= 0 else f"-₹{abs(p['total_pnl']):,.2f}"
            print(f"{reason:<20} {p['total_trades']:<10} {p['winning_trades']:<10} "
                  f"{win_pct:<9.1f}% {pnl_str:>15}")

        print("=" * 80)

    def print_full_report(self, backtest_id: str):
        """
        Print complete backtest report with all breakdowns.

        This is the main method to call at the end of every backtest.
        """
        summary = self.get_backtest_summary(backtest_id)

        # Header
        print("\n")
        print("█" * 80)
        print("█" + " " * 30 + "EOS BACKTEST REPORT" + " " * 29 + "█")
        print("█" * 80)

        # Overview
        print(f"\nBacktest ID: {backtest_id}")
        print(f"Period: {summary['period']}")
        print(f"Symbols: {', '.join(summary['symbols'])}")

        # Key Metrics
        print("\n" + "=" * 80)
        print("KEY METRICS")
        print("=" * 80)

        total_pnl = summary['total_pnl']
        pnl_str = f"₹{total_pnl:,.2f}" if total_pnl >= 0 else f"-₹{abs(total_pnl):,.2f}"

        print(f"Initial Capital:    ₹{summary['initial_capital']:,.2f}")
        print(f"Final Capital:      ₹{summary['final_capital'] or summary['initial_capital']:,.2f}")
        print(f"Total P&L:          {pnl_str}")
        print(f"Return:             {(total_pnl / summary['initial_capital'] * 100):.2f}%")
        print(f"\nTotal Trades:       {summary['total_trades']}")
        print(f"Winning Trades:     {summary['winning_trades']}")
        print(f"Losing Trades:      {summary['losing_trades']}")
        print(f"Win Rate:           {summary['win_rate']:.1f}%")

        avg_win = summary['avg_win']
        avg_loss = summary['avg_loss']
        print(f"\nAvg Win:            ₹{avg_win:,.2f}")
        print(f"Avg Loss:           ₹{avg_loss:,.2f}")
        print(f"Risk/Reward:        {abs(avg_win/avg_loss):.2f}:1" if avg_loss != 0 else "Risk/Reward: N/A")
        print(f"Max Drawdown:       ₹{summary['max_drawdown']:,.2f}")

        # All breakdowns
        self.print_trade_table(backtest_id)
        self.print_daily_breakdown(backtest_id)
        self.print_symbol_breakdown(backtest_id)
        self.print_exit_reason_breakdown(backtest_id)

        # Footer
        print("\n" + "█" * 80)
        print("█" + " " * 28 + "END OF BACKTEST REPORT" + " " * 28 + "█")
        print("█" * 80 + "\n")

