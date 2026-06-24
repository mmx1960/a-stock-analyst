from __future__ import annotations

import json
import os
import time
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

        last_error: duckdb.IOException | None = None
        for attempt in range(6):
            try:
                return duckdb.connect(str(self.db_path))
            except duckdb.IOException as exc:
                last_error = exc
                message = str(exc)
                if "Could not set lock" not in message and "Conflicting lock is held" not in message:
                    raise
                if attempt == 5:
                    break
                time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

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
                CREATE TABLE IF NOT EXISTS realtime_quote_snapshot (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    price DOUBLE,
                    change_pct DOUBLE,
                    volume DOUBLE,
                    amount DOUBLE,
                    turnover DOUBLE,
                    turnover_rate DOUBLE,
                    market_cap DOUBLE,
                    circulating_market_cap DOUBLE,
                    source TEXT,
                    raw_json TEXT,
                    trade_dt TIMESTAMP,
                    updated_at TIMESTAMP
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
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kaipanla_market_sentiment (
                    trade_date DATE PRIMARY KEY,
                    up_count INTEGER,
                    down_count INTEGER,
                    flat_count INTEGER,
                    limit_up_count INTEGER,
                    actual_limit_up_count INTEGER,
                    limit_down_count INTEGER,
                    actual_limit_down_count INTEGER,
                    rise_fall_ratio DOUBLE,
                    yesterday_rise_fall_ratio DOUBLE,
                    sh_index DOUBLE,
                    sh_change_pct TEXT,
                    sh_amount DOUBLE,
                    first_board_count INTEGER,
                    second_board_count INTEGER,
                    third_board_count INTEGER,
                    fourth_plus_board_count INTEGER,
                    consecutive_board_rate DOUBLE,
                    sharp_withdrawal_count INTEGER,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kaipanla_limit_up_sectors (
                    trade_date DATE,
                    sector_code TEXT,
                    sector_name TEXT,
                    stock_count INTEGER,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (trade_date, sector_code)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kaipanla_limit_up_stocks (
                    trade_date DATE,
                    sector_code TEXT,
                    sector_name TEXT,
                    code TEXT,
                    name TEXT,
                    limit_up_price DOUBLE,
                    turnover DOUBLE,
                    circulating_market_cap DOUBLE,
                    total_market_cap DOUBLE,
                    consecutive_days INTEGER,
                    consecutive_count INTEGER,
                    concept_tags TEXT,
                    theme TEXT,
                    reason TEXT,
                    seal_amount DOUBLE,
                    main_net_inflow DOUBLE,
                    first_limit_up_time TEXT,
                    is_first_board INTEGER,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (trade_date, sector_code, code)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kaipanla_limit_up_ladder (
                    trade_date DATE,
                    code TEXT,
                    name TEXT,
                    consecutive_days INTEGER,
                    tips TEXT,
                    is_broken BOOLEAN,
                    is_height_mark BOOLEAN,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (trade_date, code, consecutive_days, is_broken, is_height_mark)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kaipanla_sector_strength (
                    trade_date DATE,
                    sector_code TEXT,
                    sector_name TEXT,
                    limit_up_count INTEGER,
                    max_consecutive_days INTEGER,
                    stock_count INTEGER,
                    turnover DOUBLE,
                    main_net_inflow DOUBLE,
                    main_buy DOUBLE,
                    main_sell DOUBLE,
                    seal_amount DOUBLE,
                    strength_score DOUBLE,
                    capital_score DOUBLE,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (trade_date, sector_code)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_sector_membership (
                    code TEXT,
                    name TEXT,
                    sector_code TEXT,
                    sector_name TEXT,
                    sector_type TEXT,
                    source TEXT,
                    is_current BOOLEAN,
                    raw_json TEXT,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (code, sector_code, sector_type, source)
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

    def upsert_realtime_quote_snapshot(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        rename_map = {
            "latest_price": "price",
            "成交额": "amount",
            "成交量": "volume",
            "总市值": "market_cap",
            "流通市值": "circulating_market_cap",
        }
        frame = frame.rename(columns=rename_map)
        for col, default in {
            "name": "",
            "price": None,
            "change_pct": None,
            "volume": None,
            "amount": None,
            "turnover": None,
            "turnover_rate": None,
            "market_cap": None,
            "circulating_market_cap": None,
            "source": "unknown",
            "raw_json": None,
            "trade_dt": datetime.now(),
        }.items():
            if col not in frame.columns:
                frame[col] = default
        frame["code"] = frame["code"].astype(str).str.strip().str.zfill(6)
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        with self._connect() as con:
            con.register("quote_snapshot_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO realtime_quote_snapshot
                SELECT code, name, price, change_pct, volume, amount, turnover,
                       turnover_rate, market_cap, circulating_market_cap, source,
                       raw_json, trade_dt, updated_at
                FROM quote_snapshot_df
                """
            )

    def get_realtime_quote_snapshot(self, code: str) -> pd.DataFrame:
        normalized_code = str(code or "").strip()
        if normalized_code.endswith(".0"):
            normalized_code = normalized_code[:-2]
        if normalized_code.isdigit() and len(normalized_code) <= 6:
            normalized_code = normalized_code.zfill(6)
        with self._connect() as con:
            return con.execute(
                """
                SELECT *
                FROM realtime_quote_snapshot
                WHERE code = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                [normalized_code],
            ).df()

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

    def upsert_kaipanla_market_sentiment(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        if "raw_json" not in frame.columns:
            frame["raw_json"] = None
        with self._connect() as con:
            con.register("kpl_market_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO kaipanla_market_sentiment
                SELECT trade_date, up_count, down_count, flat_count, limit_up_count,
                       actual_limit_up_count, limit_down_count, actual_limit_down_count,
                       rise_fall_ratio, yesterday_rise_fall_ratio, sh_index, sh_change_pct,
                       sh_amount, first_board_count, second_board_count, third_board_count,
                       fourth_plus_board_count, consecutive_board_rate, sharp_withdrawal_count,
                       source, raw_json, updated_at
                FROM kpl_market_df
                """
            )

    def upsert_kaipanla_limit_up(self, sectors_df: pd.DataFrame, stocks_df: pd.DataFrame) -> None:
        now = datetime.now()
        with self._connect() as con:
            if sectors_df is not None and not sectors_df.empty:
                sectors = sectors_df.copy()
                if "updated_at" not in sectors.columns:
                    sectors["updated_at"] = now
                if "raw_json" not in sectors.columns:
                    sectors["raw_json"] = None
                con.register("kpl_sectors_df", sectors)
                con.execute(
                    """
                    INSERT OR REPLACE INTO kaipanla_limit_up_sectors
                    SELECT trade_date, sector_code, sector_name, stock_count, source, raw_json, updated_at
                    FROM kpl_sectors_df
                    """
                )
            if stocks_df is not None and not stocks_df.empty:
                stocks = stocks_df.copy()
                if "updated_at" not in stocks.columns:
                    stocks["updated_at"] = now
                if "raw_json" not in stocks.columns:
                    stocks["raw_json"] = None
                con.register("kpl_stocks_df", stocks)
                con.execute(
                    """
                    INSERT OR REPLACE INTO kaipanla_limit_up_stocks
                    SELECT trade_date, sector_code, sector_name, code, name, limit_up_price,
                           turnover, circulating_market_cap, total_market_cap, consecutive_days,
                           consecutive_count, concept_tags, theme, reason, seal_amount,
                           main_net_inflow, first_limit_up_time, is_first_board, source, raw_json,
                           updated_at
                    FROM kpl_stocks_df
                    """
                )

    def upsert_kaipanla_limit_up_ladder(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        if "raw_json" not in frame.columns:
            frame["raw_json"] = None
        with self._connect() as con:
            con.register("kpl_ladder_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO kaipanla_limit_up_ladder
                SELECT trade_date, code, name, consecutive_days, tips, is_broken,
                       is_height_mark, source, raw_json, updated_at
                FROM kpl_ladder_df
                """
            )

    def upsert_kaipanla_sector_strength(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        if "raw_json" not in frame.columns:
            frame["raw_json"] = None
        with self._connect() as con:
            con.register("kpl_sector_strength_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO kaipanla_sector_strength
                SELECT trade_date, sector_code, sector_name, limit_up_count,
                       max_consecutive_days, stock_count, turnover, main_net_inflow,
                       main_buy, main_sell, seal_amount, strength_score, capital_score,
                       source, raw_json, updated_at
                FROM kpl_sector_strength_df
                """
            )

    def get_kaipanla_sector_strength(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        clauses = []
        params = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(self._normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(self._normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as con:
            return con.execute(
                f"SELECT * FROM kaipanla_sector_strength {where} ORDER BY trade_date DESC, strength_score DESC, capital_score DESC",
                params,
            ).df()

    def upsert_stock_sector_membership(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        frame = df.copy()
        for col, default in {
            "name": "",
            "sector_code": "",
            "sector_name": "",
            "sector_type": "unknown",
            "source": "unknown",
            "is_current": True,
            "raw_json": None,
        }.items():
            if col not in frame.columns:
                frame[col] = default
        frame["code"] = frame["code"].astype(str).str.strip().str.zfill(6)
        frame = frame[(frame["code"] != "") & (frame["sector_name"].astype(str).str.strip() != "")]
        if frame.empty:
            return
        if "updated_at" not in frame.columns:
            frame["updated_at"] = datetime.now()
        with self._connect() as con:
            con.register("stock_sector_df", frame)
            con.execute(
                """
                INSERT OR REPLACE INTO stock_sector_membership
                SELECT code, name, sector_code, sector_name, sector_type, source,
                       is_current, raw_json, updated_at
                FROM stock_sector_df
                """
            )

    def get_stock_sector_membership(self, code: str, *, current_only: bool = True) -> pd.DataFrame:
        normalized_code = str(code or "").strip()
        if normalized_code.endswith(".0"):
            normalized_code = normalized_code[:-2]
        if normalized_code.isdigit() and len(normalized_code) <= 6:
            normalized_code = normalized_code.zfill(6)
        clauses = ["code = ?"]
        params = [normalized_code]
        if current_only:
            clauses.append("is_current = true")
        with self._connect() as con:
            return con.execute(
                f"""
                SELECT *
                FROM stock_sector_membership
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE sector_type
                        WHEN 'tdx_industry_l3' THEN 0
                        WHEN 'tdx_industry_l2' THEN 1
                        WHEN 'tdx_industry_l1' THEN 2
                        WHEN 'kaipanla_sector' THEN 3
                        WHEN 'hotspot' THEN 4
                        WHEN 'concept' THEN 5
                        WHEN 'industry' THEN 6
                        WHEN 'cninfo_industry_l4' THEN 8
                        WHEN 'cninfo_industry_l3' THEN 9
                        WHEN 'cninfo_industry_l2' THEN 10
                        WHEN 'cninfo_industry_l1' THEN 11
                        ELSE 99
                    END,
                    source,
                    sector_name
                """,
                params,
            ).df()

    def get_kaipanla_market_sentiment(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        clauses = []
        params = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(self._normalize_date(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(self._normalize_date(end_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as con:
            return con.execute(f"SELECT * FROM kaipanla_market_sentiment {where} ORDER BY trade_date", params).df()

    def get_kaipanla_limit_up_stocks(self, trade_date: Optional[str] = None, min_consecutive_days: int = 1) -> pd.DataFrame:
        clauses = ["consecutive_days >= ?"]
        params = [int(min_consecutive_days)]
        if trade_date:
            clauses.append("trade_date = ?")
            params.append(self._normalize_date(trade_date))
        sql = f"""
            SELECT *
            FROM kaipanla_limit_up_stocks
            WHERE {' AND '.join(clauses)}
            ORDER BY trade_date DESC, consecutive_days DESC, seal_amount DESC
        """
        with self._connect() as con:
            return con.execute(sql, params).df()

    def get_kaipanla_limit_up_sectors(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        clauses = []
        params = []
        if trade_date:
            clauses.append("trade_date = ?")
            params.append(self._normalize_date(trade_date))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as con:
            return con.execute(f"SELECT * FROM kaipanla_limit_up_sectors {where} ORDER BY trade_date DESC, stock_count DESC", params).df()

    def get_latest_kaipanla_limit_up_trade_date(self) -> Optional[str]:
        with self._connect() as con:
            rows = con.execute("SELECT max(trade_date) AS trade_date FROM kaipanla_limit_up_stocks").fetchall()
        if not rows or rows[0][0] is None:
            return None
        return str(rows[0][0])[:10]

    def get_latest_kaipanla_trade_date(self) -> Optional[str]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT max(trade_date) AS trade_date
                FROM (
                    SELECT trade_date FROM kaipanla_limit_up_stocks
                    UNION ALL
                    SELECT trade_date FROM kaipanla_market_sentiment
                )
                """
            ).fetchall()
        if not rows or rows[0][0] is None:
            return None
        return str(rows[0][0])[:10]

    @staticmethod
    def json_dumps(value) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _normalize_date(value: str) -> str:
        value = str(value)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return value[:10]


