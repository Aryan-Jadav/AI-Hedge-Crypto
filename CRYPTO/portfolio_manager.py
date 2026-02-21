"""
CRYPTO Portfolio Manager
Mirrors EOS/eos_portfolio_manager.py structure exactly.

CRITICAL: Opens the SAME portfolio.db as EOS (shared database).
Creates NEW crypto_* prefixed tables without touching any existing EOS tables.
This allows the unified dashboard to show both EOS and CRYPTO data.

Database: EOS/portfolio.db (shared)
New tables: crypto_sessions, crypto_trades, crypto_daily_snapshots,
            crypto_positions, crypto_validation_logs
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")

# Path to shared EOS portfolio database
_DEFAULT_DB_PATH = str(
    Path(__file__).parent.parent / "EOS" / "portfolio.db"
)


# =============================================================================
# DATACLASSES (mirrors EOS TradeRecord and DailySnapshot)
# =============================================================================

@dataclass
class CryptoTradeRecord:
    """
    Completed trade record. Mirrors EOS TradeRecord.
    All monetary values in USDT.
    """
    trade_id: str
    session_id: str
    symbol: str
    side: str               # "LONG" or "SHORT"
    entry_date: str
    entry_time: str         # IST HH:MM:SS
    entry_price: float
    exit_date: str
    exit_time: str
    exit_price: float
    quantity: float         # base asset units
    min_qty: float
    pnl_usdt: float
    pnl_pct: float
    exit_reason: str
    hold_duration_minutes: float
    price_change_at_entry: float
    funding_rate_at_entry: float
    volume_spike_at_entry: bool


@dataclass
class CryptoDailySnapshot:
    """
    Daily session snapshot. Mirrors EOS DailySnapshot.
    All monetary values in USDT.
    """
    date: str
    starting_capital_usdt: float
    ending_capital_usdt: float
    daily_pnl_usdt: float
    daily_pnl_pct: float
    trades_taken: int
    winning_trades: int
    losing_trades: int
    max_drawdown_usdt: float
    cumulative_pnl_usdt: float
    session_id: str


# =============================================================================
# PORTFOLIO MANAGER
# =============================================================================

class CryptoPortfolioManager:
    """
    Crypto Portfolio Manager. Mirrors EOSPortfolioManager exactly.

    Opens shared portfolio.db from EOS directory.
    Creates crypto_* tables only; never touches EOS tables.
    """

    def __init__(self, db_path: str = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.current_session_id: Optional[str] = None
        self.conn: Optional[sqlite3.Connection] = None
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-safe database connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_database(self) -> None:
        """
        Create all crypto_* tables if they don't exist.
        Safe to call multiple times (uses IF NOT EXISTS).
        Never modifies existing EOS tables.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # crypto_sessions: mirrors backtests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_sessions (
                session_id            TEXT PRIMARY KEY,
                start_date            TEXT NOT NULL,
                end_date              TEXT NOT NULL,
                symbols               TEXT NOT NULL,
                initial_capital_usdt  REAL DEFAULT 10000.0,
                final_capital_usdt    REAL,
                total_pnl_usdt        REAL,
                total_trades          INTEGER DEFAULT 0,
                win_rate              REAL,
                sharpe_ratio          REAL,
                max_drawdown_usdt     REAL,
                created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
                config_json           TEXT,
                mode                  TEXT DEFAULT 'PAPER'
            )
        """)

        # crypto_trades: mirrors trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_trades (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id              TEXT UNIQUE NOT NULL,
                session_id            TEXT NOT NULL,
                symbol                TEXT NOT NULL,
                side                  TEXT NOT NULL,
                entry_date            TEXT NOT NULL,
                entry_time            TEXT NOT NULL,
                entry_price           REAL NOT NULL,
                exit_date             TEXT,
                exit_time             TEXT,
                exit_price            REAL,
                quantity              REAL NOT NULL,
                min_qty               REAL NOT NULL,
                pnl_usdt              REAL,
                pnl_pct               REAL,
                exit_reason           TEXT,
                hold_duration_minutes REAL,
                price_change_at_entry REAL,
                funding_rate_at_entry REAL,
                volume_spike_at_entry INTEGER DEFAULT 0,
                status                TEXT DEFAULT 'OPEN',
                created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES crypto_sessions(session_id)
            )
        """)

        # crypto_daily_snapshots: mirrors daily_snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_daily_snapshots (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id              TEXT NOT NULL,
                date                    TEXT NOT NULL,
                starting_capital_usdt   REAL,
                ending_capital_usdt     REAL,
                daily_pnl_usdt          REAL,
                daily_pnl_pct           REAL,
                trades_taken            INTEGER DEFAULT 0,
                winning_trades          INTEGER DEFAULT 0,
                losing_trades           INTEGER DEFAULT 0,
                max_drawdown_usdt       REAL DEFAULT 0.0,
                cumulative_pnl_usdt     REAL DEFAULT 0.0,
                FOREIGN KEY (session_id) REFERENCES crypto_sessions(session_id),
                UNIQUE(session_id, date)
            )
        """)

        # crypto_positions: mirrors positions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_positions (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id                TEXT NOT NULL,
                symbol                    TEXT NOT NULL,
                side                      TEXT NOT NULL,
                entry_date                TEXT NOT NULL,
                entry_time                TEXT NOT NULL,
                entry_price               REAL NOT NULL,
                quantity                  REAL NOT NULL,
                min_qty                   REAL NOT NULL,
                current_price             REAL,
                unrealized_pnl_usdt       REAL DEFAULT 0.0,
                stop_loss                 REAL,
                trailing_stop             REAL,
                highest_favorable_price   REAL,
                status                    TEXT DEFAULT 'OPEN',
                FOREIGN KEY (session_id) REFERENCES crypto_sessions(session_id)
            )
        """)

        # crypto_validation_logs: mirrors validation_logs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_validation_logs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT,
                timestamp           TEXT NOT NULL,
                symbol              TEXT NOT NULL,
                signal_type         TEXT,
                result              TEXT NOT NULL,
                confidence          REAL,
                reason              TEXT,
                tier_used           TEXT,
                latency_ms          INTEGER DEFAULT 0,
                tokens_used         INTEGER DEFAULT 0,
                price_change_pct    REAL,
                funding_rate        REAL,
                volume_spike        INTEGER DEFAULT 0,
                entry_price         REAL,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES crypto_sessions(session_id)
            )
        """)

        conn.commit()
        conn.close()
        print(f"[CryptoDB] Database initialized: {self.db_path}")

    # =========================================================================
    # SESSION MANAGEMENT (mirrors start_backtest / end_backtest)
    # =========================================================================

    def start_session(
        self,
        start_date: str,
        end_date: str,
        symbols: List[str],
        initial_capital_usdt: float = 10000.0,
        config: Dict = None,
        mode: str = "PAPER",
    ) -> str:
        """
        Start a new crypto trading session.
        Mirrors EOSPortfolioManager.start_backtest().
        Returns session_id.
        """
        now = datetime.now(IST)
        session_id = f"CRYPTO_{now.strftime('%Y%m%d_%H%M%S')}"

        conn = self._get_connection()
        conn.execute("""
            INSERT INTO crypto_sessions
            (session_id, start_date, end_date, symbols, initial_capital_usdt, mode, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            start_date,
            end_date,
            json.dumps(symbols),
            initial_capital_usdt,
            mode,
            json.dumps(config or {}),
        ))
        conn.commit()
        conn.close()

        self.current_session_id = session_id
        print(f"[CryptoDB] Session started: {session_id} | mode={mode} | capital=${initial_capital_usdt:.2f}")
        return session_id

    def end_session(
        self,
        session_id: str = None,
        final_capital_usdt: float = None,
        metrics: Dict = None,
    ) -> None:
        """
        End and finalize a crypto session.
        Mirrors EOSPortfolioManager.end_backtest().
        """
        sid = session_id or self.current_session_id
        if not sid:
            return

        metrics = metrics or {}
        now = datetime.now(IST).strftime("%Y-%m-%d")

        conn = self._get_connection()
        conn.execute("""
            UPDATE crypto_sessions
            SET end_date=?, final_capital_usdt=?, total_pnl_usdt=?,
                total_trades=?, win_rate=?, sharpe_ratio=?, max_drawdown_usdt=?
            WHERE session_id=?
        """, (
            now,
            final_capital_usdt,
            metrics.get("total_pnl_usdt", 0.0),
            metrics.get("total_trades", 0),
            metrics.get("win_rate"),
            metrics.get("sharpe_ratio"),
            metrics.get("max_drawdown_usdt"),
            sid,
        ))
        conn.commit()
        conn.close()
        print(f"[CryptoDB] Session ended: {sid} | final=${final_capital_usdt:.2f}" if final_capital_usdt else f"[CryptoDB] Session ended: {sid}")

    # =========================================================================
    # TRADE MANAGEMENT (mirrors record_trade / get_trades)
    # =========================================================================

    def record_trade(self, trade: CryptoTradeRecord) -> int:
        """
        Record a completed trade. Mirrors EOSPortfolioManager.record_trade().
        Returns row ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO crypto_trades
            (trade_id, session_id, symbol, side, entry_date, entry_time, entry_price,
             exit_date, exit_time, exit_price, quantity, min_qty,
             pnl_usdt, pnl_pct, exit_reason, hold_duration_minutes,
             price_change_at_entry, funding_rate_at_entry, volume_spike_at_entry, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.trade_id, trade.session_id, trade.symbol, trade.side,
            trade.entry_date, trade.entry_time, trade.entry_price,
            trade.exit_date, trade.exit_time, trade.exit_price,
            trade.quantity, trade.min_qty, trade.pnl_usdt, trade.pnl_pct,
            trade.exit_reason, trade.hold_duration_minutes,
            trade.price_change_at_entry, trade.funding_rate_at_entry,
            1 if trade.volume_spike_at_entry else 0, "CLOSED",
        ))
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def get_trades(
        self,
        session_id: str = None,
        symbol: str = None,
        start_date: str = None,
        end_date: str = None,
        limit: int = 200,
    ) -> List[Dict]:
        """Fetch trades with optional filters. Mirrors EOSPortfolioManager.get_trades()."""
        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM crypto_trades WHERE 1=1"
        params: List = []

        if session_id:
            query += " AND session_id=?"
            params.append(session_id)
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        if start_date:
            query += " AND entry_date>=?"
            params.append(start_date)
        if end_date:
            query += " AND entry_date<=?"
            params.append(end_date)

        query += " ORDER BY entry_date DESC, entry_time DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================

    def open_position(
        self,
        session_id: str,
        symbol: str,
        side: str,
        entry_date: str,
        entry_time: str,
        entry_price: float,
        quantity: float,
        min_qty: float,
        stop_loss: float,
    ) -> int:
        """Open a position. Mirrors EOSPortfolioManager.open_position()."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO crypto_positions
            (session_id, symbol, side, entry_date, entry_time, entry_price,
             quantity, min_qty, current_price, stop_loss, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            session_id, symbol, side, entry_date, entry_time, entry_price,
            quantity, min_qty, entry_price, stop_loss, "OPEN",
        ))
        position_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return position_id

    def update_position(
        self,
        position_id: int,
        current_price: float = None,
        trailing_stop: float = None,
        highest_favorable_price: float = None,
    ) -> None:
        """Update position fields. Mirrors EOSPortfolioManager.update_position()."""
        conn = self._get_connection()
        updates = []
        params: List = []

        if current_price is not None:
            updates.append("current_price=?")
            params.append(current_price)
        if trailing_stop is not None:
            updates.append("trailing_stop=?")
            params.append(trailing_stop)
        if highest_favorable_price is not None:
            updates.append("highest_favorable_price=?")
            params.append(highest_favorable_price)

        if updates:
            params.append(position_id)
            conn.execute(
                f"UPDATE crypto_positions SET {', '.join(updates)} WHERE id=?",
                params,
            )
            conn.commit()
        conn.close()

    def close_position(self, position_id: int) -> None:
        """Mark a position as CLOSED."""
        conn = self._get_connection()
        conn.execute(
            "UPDATE crypto_positions SET status='CLOSED' WHERE id=?",
            (position_id,),
        )
        conn.commit()
        conn.close()

    def get_open_positions(self, session_id: str = None) -> List[Dict]:
        """Get open positions. Mirrors EOSPortfolioManager.get_open_positions()."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if session_id:
            cursor.execute(
                "SELECT * FROM crypto_positions WHERE status='OPEN' AND session_id=?",
                (session_id,),
            )
        else:
            cursor.execute("SELECT * FROM crypto_positions WHERE status='OPEN'")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    # =========================================================================
    # SNAPSHOT MANAGEMENT
    # =========================================================================

    def record_daily_snapshot(self, snapshot: CryptoDailySnapshot) -> None:
        """Record daily snapshot. Mirrors EOSPortfolioManager.record_daily_snapshot()."""
        conn = self._get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO crypto_daily_snapshots
            (session_id, date, starting_capital_usdt, ending_capital_usdt,
             daily_pnl_usdt, daily_pnl_pct, trades_taken, winning_trades,
             losing_trades, max_drawdown_usdt, cumulative_pnl_usdt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            snapshot.session_id, snapshot.date,
            snapshot.starting_capital_usdt, snapshot.ending_capital_usdt,
            snapshot.daily_pnl_usdt, snapshot.daily_pnl_pct,
            snapshot.trades_taken, snapshot.winning_trades,
            snapshot.losing_trades, snapshot.max_drawdown_usdt,
            snapshot.cumulative_pnl_usdt,
        ))
        conn.commit()
        conn.close()

    def get_daily_snapshots(self, session_id: str) -> List[Dict]:
        """Get daily snapshots for a session."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM crypto_daily_snapshots WHERE session_id=? ORDER BY date",
            (session_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    # =========================================================================
    # VALIDATION LOGGING
    # =========================================================================

    def record_validation(
        self,
        session_id: str,
        symbol: str,
        signal_type: str,
        result: str,
        confidence: float,
        reason: str,
        tier_used: str,
        latency_ms: int = 0,
        tokens_used: int = 0,
        price_change_pct: float = 0,
        funding_rate: float = 0,
        volume_spike: bool = False,
        entry_price: float = 0,
    ) -> int:
        """Record AI validation log. Mirrors EOSPortfolioManager.record_validation()."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO crypto_validation_logs
            (session_id, timestamp, symbol, signal_type, result, confidence,
             reason, tier_used, latency_ms, tokens_used,
             price_change_pct, funding_rate, volume_spike, entry_price)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            session_id,
            datetime.now(IST).isoformat(),
            symbol, signal_type, result,
            confidence, reason, tier_used,
            latency_ms, tokens_used,
            price_change_pct, funding_rate,
            1 if volume_spike else 0,
            entry_price,
        ))
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id

    # =========================================================================
    # ANALYTICS (mirrors EOSPortfolioManager analytics methods)
    # =========================================================================

    def get_equity_curve(self, session_id: str = None) -> List[Dict]:
        """
        Get equity curve data. Mirrors EOSPortfolioManager.get_equity_curve().
        Returns list of {date, ending_capital_usdt}.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        if session_id:
            cursor.execute("""
                SELECT date, ending_capital_usdt
                FROM crypto_daily_snapshots
                WHERE session_id=?
                ORDER BY date
            """, (session_id,))
        else:
            cursor.execute("""
                SELECT date, AVG(ending_capital_usdt) as ending_capital_usdt
                FROM crypto_daily_snapshots
                GROUP BY date
                ORDER BY date
            """)
        rows = [{"date": row[0], "ending_capital_usdt": row[1]} for row in cursor.fetchall()]
        conn.close()
        return rows

    def get_performance_by_symbol(self, session_id: str) -> Dict[str, Dict]:
        """Symbol-level performance breakdown."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(pnl_usdt) as avg_pnl,
                   MIN(pnl_usdt) as worst_trade,
                   MAX(pnl_usdt) as best_trade
            FROM crypto_trades
            WHERE session_id=? AND status='CLOSED'
            GROUP BY symbol
        """, (session_id,))
        result = {}
        for row in cursor.fetchall():
            row = dict(row)
            result[row["symbol"]] = {
                "total_trades": row["total_trades"],
                "wins":         row["wins"],
                "losses":       row["total_trades"] - row["wins"],
                "win_rate":     round(row["wins"] / row["total_trades"] * 100, 1) if row["total_trades"] else 0,
                "total_pnl":    round(row["total_pnl"] or 0, 2),
                "avg_pnl":      round(row["avg_pnl"] or 0, 2),
                "best_trade":   round(row["best_trade"] or 0, 2),
                "worst_trade":  round(row["worst_trade"] or 0, 2),
            }
        conn.close()
        return result

    def get_session_summary(self, session_id: str) -> Dict:
        """Full session summary. Mirrors EOSPortfolioManager.get_backtest_summary()."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM crypto_sessions WHERE session_id=?", (session_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {}
        session = dict(row)

        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(hold_duration_minutes) as avg_hold
            FROM crypto_trades
            WHERE session_id=? AND status='CLOSED'
        """, (session_id,))
        trade_stats = dict(cursor.fetchone() or {})

        conn.close()

        total = trade_stats.get("total") or 0
        wins = trade_stats.get("wins") or 0

        return {
            **session,
            "total_trades":   total,
            "winning_trades": wins,
            "losing_trades":  total - wins,
            "win_rate":       round(wins / total * 100, 1) if total else 0,
            "total_pnl":      round(trade_stats.get("total_pnl") or 0, 2),
            "avg_hold_min":   round(trade_stats.get("avg_hold") or 0, 1),
        }

    def list_sessions(self) -> List[Dict]:
        """List all crypto sessions. Mirrors EOSPortfolioManager listing."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM crypto_sessions ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def close(self) -> None:
        """Graceful shutdown."""
        print("[CryptoDB] Portfolio manager closed.")
