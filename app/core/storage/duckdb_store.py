from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

from app.core.config import DUCKDB_PATH


class DuckDBStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DUCKDB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap()

    def _connect(self):
        read_only = os.getenv("ASHARE_DUCKDB_READ_ONLY", "0") == "1"
        if read_only:
            return duckdb.connect(str(self.db_path), read_only=True)
        return duckdb.connect(str(self.db_path))

    def _bootstrap(self):
        if os.getenv("ASHARE_DUCKDB_READ_ONLY", "0") == "1":
            return
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_basic (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    market TEXT,
                    exchange TEXT,
                    list_date DATE,
                    status TEXT,
                    updated_at TIMESTAMP,
                    source TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_kline (
                    code TEXT,
                    trade_date DATE,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume DOUBLE,
                    amount DOUBLE,
                    turnover_rate DOUBLE,
                    change_pct DOUBLE,
                    adjust TEXT,
                    source TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (code, trade_date, adjust)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS minute_kline (
                    code TEXT,
                    period TEXT,
                    trade_dt TIMESTAMP,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume DOUBLE,
                    amount DOUBLE,
                    source TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (code, period, trade_dt)
                )
                """
            )

    def upsert_stock_basic(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        with self._connect() as con:
            con.register("stock_basic_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO stock_basic
                SELECT code, name, market, exchange, list_date, status, updated_at, source
                FROM stock_basic_df
                """
            )

    def upsert_daily_kline(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        for col, default in {
            "turnover_rate": None,
            "change_pct": None,
            "adjust": "hfq",
            "source": "unknown",
        }.items():
            if col not in frame.columns:
                frame[col] = default
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        with self._connect() as con:
            con.register("daily_kline_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO daily_kline
                SELECT code, trade_date, open, high, low, close, volume, amount,
                       turnover_rate, change_pct, adjust, source, updated_at
                FROM daily_kline_df
                """
            )

    def upsert_minute_kline(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        rename_map = {
            "datetime": "trade_dt",
            "vol": "volume",
            "成交量": "volume",
            "成交额": "amount",
        }
        frame = frame.rename(columns=rename_map)
        for col, default in {
            "period": "5",
            "amount": None,
            "source": "unknown",
        }.items():
            if col not in frame.columns:
                frame[col] = default
        if "trade_dt" not in frame.columns:
            raise ValueError("minute kline missing trade_dt/datetime column")
        frame["trade_dt"] = pd.to_datetime(frame["trade_dt"])
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        with self._connect() as con:
            con.register("minute_kline_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO minute_kline
                SELECT code, period, trade_dt, open, high, low, close, volume, amount, source, updated_at
                FROM minute_kline_df
                """
            )

    def get_daily_kline(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        clauses = ["code = ?", "adjust = ?"]
        params = [code, adjust]
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(self._normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(self._normalize_date(end_date))
        sql = f"""
            SELECT *
            FROM daily_kline
            WHERE {' AND '.join(clauses)}
            ORDER BY trade_date
        """
        with self._connect() as con:
            return con.execute(sql, params).df()

    def has_daily_kline(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> bool:
        df = self.get_daily_kline(code=code, start_date=start_date, end_date=end_date, adjust=adjust)
        return df is not None and not df.empty

    def get_stock_basic(self, limit: Optional[int] = None) -> pd.DataFrame:
        sql = "SELECT * FROM stock_basic ORDER BY code"
        if limit and limit > 0:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as con:
            return con.execute(sql).df()

    def get_minute_kline(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses = ["code = ?", "period = ?"]
        params = [code, str(period)]
        if start_date:
            clauses.append("trade_dt >= ?")
            params.append(pd.to_datetime(start_date))
        if end_date:
            clauses.append("trade_dt <= ?")
            params.append(pd.to_datetime(end_date))
        sql = f"""
            SELECT *
            FROM minute_kline
            WHERE {' AND '.join(clauses)}
            ORDER BY trade_dt
        """
        with self._connect() as con:
            return con.execute(sql, params).df()

    def has_minute_kline(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        df = self.get_minute_kline(code=code, period=period, start_date=start_date, end_date=end_date)
        return df is not None and not df.empty

    @staticmethod
    def _normalize_date(value: str) -> str:
        value = str(value)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return value[:10]
