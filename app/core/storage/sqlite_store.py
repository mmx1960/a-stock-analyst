from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from app.core.config import SQLITE_PATH


class SQLiteStore:
    """SQLite-backed local market data catalog."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or SQLITE_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _bootstrap(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS stock_basic (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    market TEXT,
                    exchange TEXT,
                    list_date TEXT,
                    status TEXT,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kline_bars (
                    code TEXT NOT NULL,
                    period TEXT NOT NULL,
                    trade_time TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    turnover_rate REAL,
                    change_pct REAL,
                    adjust TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (code, period, trade_time, adjust)
                );
                CREATE INDEX IF NOT EXISTS idx_kline_bars_lookup
                    ON kline_bars (code, period, adjust, trade_time);

                CREATE TABLE IF NOT EXISTS realtime_quote_snapshot (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    price REAL,
                    change_pct REAL,
                    change_amt REAL,
                    volume REAL,
                    amount REAL,
                    turnover REAL,
                    turnover_rate REAL,
                    pe REAL,
                    pb REAL,
                    market_cap REAL,
                    circulating_cap REAL,
                    volume_ratio REAL,
                    amplitude REAL,
                    source_main TEXT,
                    source_extra TEXT,
                    raw_json TEXT,
                    quote_time TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sector_strength (
                    trade_date TEXT NOT NULL,
                    sector_code TEXT NOT NULL,
                    sector_name TEXT,
                    sector_type TEXT,
                    limit_up_count INTEGER,
                    max_consecutive_days INTEGER,
                    stock_count INTEGER,
                    turnover REAL,
                    main_net_inflow REAL,
                    main_buy REAL,
                    main_sell REAL,
                    seal_amount REAL,
                    strength_score REAL,
                    capital_score REAL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, sector_code, source)
                );

                CREATE TABLE IF NOT EXISTS stock_sector_membership (
                    code TEXT NOT NULL,
                    name TEXT,
                    sector_code TEXT NOT NULL,
                    sector_name TEXT,
                    sector_type TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    is_current INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (code, sector_code, sector_type, source)
                );

                CREATE TABLE IF NOT EXISTS hotspot_news (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT,
                    published_at TEXT,
                    code TEXT,
                    sector_code TEXT,
                    sector_name TEXT,
                    url TEXT,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    raw_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hotspot_news_lookup
                    ON hotspot_news (published_at, code, sector_code);

                CREATE TABLE IF NOT EXISTS limit_up_events (
                    trade_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    sector_code TEXT,
                    sector_name TEXT,
                    limit_up_price REAL,
                    turnover REAL,
                    circulating_market_cap REAL,
                    total_market_cap REAL,
                    consecutive_days INTEGER,
                    consecutive_count INTEGER,
                    concept_tags TEXT,
                    theme TEXT,
                    reason TEXT,
                    seal_amount REAL,
                    main_net_inflow REAL,
                    first_limit_up_time TEXT,
                    is_first_board INTEGER,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, code, source)
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name TEXT NOT NULL,
                    data_type TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    rows_written INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    params_json TEXT
                );

                CREATE TABLE IF NOT EXISTS data_freshness (
                    data_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    latest_time TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (data_type, scope)
                );
                """
            )

    def upsert_stock_basic(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df)
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            code = self._normalize_code(row.get("code"))
            if not code:
                continue
            rows.append(
                (
                    code,
                    self._clean_text(row.get("name")),
                    self._clean_text(row.get("market")),
                    self._clean_text(row.get("exchange")),
                    self._date_text(row.get("list_date")),
                    self._clean_text(row.get("status")) or "active",
                    self._clean_text(row.get("source")) or "unknown",
                    self._raw_json(row),
                    self._now(),
                )
            )
        if not rows:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO stock_basic
                    (code, name, market, exchange, list_date, status, source, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    exchange=excluded.exchange,
                    list_date=excluded.list_date,
                    status=excluded.status,
                    source=excluded.source,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_stock_basic(self, limit: Optional[int] = None) -> pd.DataFrame:
        sql = "SELECT * FROM stock_basic ORDER BY code"
        params: list[Any] = []
        if limit and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        return self._read_sql(sql, params)

    def upsert_kline_bars(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df).rename(
            columns={
                "date": "trade_time",
                "datetime": "trade_time",
                "trade_date": "trade_time",
                "trade_dt": "trade_time",
                "成交量": "volume",
                "成交额": "amount",
                "换手率": "turnover_rate",
                "涨跌幅": "change_pct",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
            }
        )
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            code = self._normalize_code(row.get("code"))
            trade_time = self._datetime_text(row.get("trade_time"))
            if not code or not trade_time:
                continue
            rows.append(
                (
                    code,
                    self._normalize_period(row.get("period")),
                    trade_time,
                    self._float_or_none(row.get("open")),
                    self._float_or_none(row.get("high")),
                    self._float_or_none(row.get("low")),
                    self._float_or_none(row.get("close")),
                    self._float_or_none(row.get("volume")),
                    self._float_or_none(row.get("amount")),
                    self._float_or_none(row.get("turnover_rate")),
                    self._float_or_none(row.get("change_pct")),
                    self._clean_text(row.get("adjust")) or "",
                    self._clean_text(row.get("source")) or "unknown",
                    self._raw_json(row),
                    self._now(),
                )
            )
        if not rows:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO kline_bars
                    (code, period, trade_time, open, high, low, close, volume, amount,
                     turnover_rate, change_pct, adjust, source, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, period, trade_time, adjust) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    amount=excluded.amount,
                    turnover_rate=excluded.turnover_rate,
                    change_pct=excluded.change_pct,
                    source=excluded.source,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_kline_bars(
        self,
        code: str,
        *,
        period: str = "d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        clauses = ["code = ?", "period = ?", "adjust = ?"]
        params: list[Any] = [self._normalize_code(code), self._normalize_period(period), adjust or ""]
        if start_date:
            clauses.append("trade_time >= ?")
            params.append(self._datetime_text(start_date))
        if end_date:
            clauses.append("trade_time <= ?")
            params.append(self._end_datetime_text(end_date, period))
        return self._read_sql(
            f"SELECT * FROM kline_bars WHERE {' AND '.join(clauses)} ORDER BY trade_time",
            params,
        )

    def upsert_realtime_quote_snapshot(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df).rename(
            columns={
                "latest_price": "price",
                "成交量": "volume",
                "成交额": "amount",
                "总市值": "market_cap",
                "流通市值": "circulating_cap",
                "circulating_market_cap": "circulating_cap",
                "source": "source_main",
            }
        )
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            code = self._normalize_code(row.get("code"))
            if not code:
                continue
            quote_time = self._datetime_text(row.get("quote_time") or row.get("trade_dt") or row.get("updated_at")) or self._now()
            rows.append(
                (
                    code,
                    self._clean_text(row.get("name")),
                    self._float_or_none(row.get("price")),
                    self._float_or_none(row.get("change_pct") or row.get("涨跌幅")),
                    self._float_or_none(row.get("change_amt")),
                    self._float_or_none(row.get("volume")),
                    self._float_or_none(row.get("amount")),
                    self._float_or_none(row.get("turnover") or row.get("amount")),
                    self._float_or_none(row.get("turnover_rate") or row.get("换手率")),
                    self._float_or_none(row.get("pe")),
                    self._float_or_none(row.get("pb")),
                    self._float_or_none(row.get("market_cap")),
                    self._float_or_none(row.get("circulating_cap")),
                    self._float_or_none(row.get("volume_ratio")),
                    self._float_or_none(row.get("amplitude")),
                    self._clean_text(row.get("source_main")) or "unknown",
                    self._clean_text(row.get("source_extra")),
                    self._raw_json(row),
                    quote_time,
                    self._now(),
                )
            )
        if not rows:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO realtime_quote_snapshot
                    (code, name, price, change_pct, change_amt, volume, amount, turnover,
                     turnover_rate, pe, pb, market_cap, circulating_cap, volume_ratio,
                     amplitude, source_main, source_extra, raw_json, quote_time, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,
                    price=excluded.price,
                    change_pct=excluded.change_pct,
                    change_amt=excluded.change_amt,
                    volume=excluded.volume,
                    amount=excluded.amount,
                    turnover=excluded.turnover,
                    turnover_rate=excluded.turnover_rate,
                    pe=excluded.pe,
                    pb=excluded.pb,
                    market_cap=excluded.market_cap,
                    circulating_cap=excluded.circulating_cap,
                    volume_ratio=excluded.volume_ratio,
                    amplitude=excluded.amplitude,
                    source_main=excluded.source_main,
                    source_extra=excluded.source_extra,
                    raw_json=excluded.raw_json,
                    quote_time=excluded.quote_time,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_realtime_quote_snapshot(self, code: str) -> pd.DataFrame:
        return self._read_sql(
            "SELECT * FROM realtime_quote_snapshot WHERE code = ? ORDER BY updated_at DESC LIMIT 1",
            [self._normalize_code(code)],
        )

    def upsert_sector_strength(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df)
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            trade_date = self._date_text(row.get("trade_date"))
            sector_code = self._clean_text(row.get("sector_code") or row.get("sector_name"))
            if not trade_date or not sector_code:
                continue
            rows.append(
                (
                    trade_date,
                    sector_code,
                    self._clean_text(row.get("sector_name")),
                    self._clean_text(row.get("sector_type")),
                    self._int_or_none(row.get("limit_up_count")),
                    self._int_or_none(row.get("max_consecutive_days")),
                    self._int_or_none(row.get("stock_count")),
                    self._float_or_none(row.get("turnover")),
                    self._float_or_none(row.get("main_net_inflow")),
                    self._float_or_none(row.get("main_buy")),
                    self._float_or_none(row.get("main_sell")),
                    self._float_or_none(row.get("seal_amount")),
                    self._float_or_none(row.get("strength_score")),
                    self._float_or_none(row.get("capital_score")),
                    self._clean_text(row.get("source")) or "unknown",
                    self._raw_json(row),
                    self._now(),
                )
            )
        if not rows:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO sector_strength
                    (trade_date, sector_code, sector_name, sector_type, limit_up_count,
                     max_consecutive_days, stock_count, turnover, main_net_inflow,
                     main_buy, main_sell, seal_amount, strength_score, capital_score,
                     source, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, sector_code, source) DO UPDATE SET
                    sector_name=excluded.sector_name,
                    sector_type=excluded.sector_type,
                    limit_up_count=excluded.limit_up_count,
                    max_consecutive_days=excluded.max_consecutive_days,
                    stock_count=excluded.stock_count,
                    turnover=excluded.turnover,
                    main_net_inflow=excluded.main_net_inflow,
                    main_buy=excluded.main_buy,
                    main_sell=excluded.main_sell,
                    seal_amount=excluded.seal_amount,
                    strength_score=excluded.strength_score,
                    capital_score=excluded.capital_score,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_sector_strength(self, start_date: Optional[str] = None, end_date: Optional[str] = None, top_n: Optional[int] = None) -> pd.DataFrame:
        clauses = []
        params: list[Any] = []
        if start_date:
            clauses.append("trade_date >= ?")
            params.append(self._date_text(start_date))
        if end_date:
            clauses.append("trade_date <= ?")
            params.append(self._date_text(end_date))
        sql = "SELECT * FROM sector_strength"
        if clauses:
            sql += f" WHERE {' AND '.join(clauses)}"
        sql += " ORDER BY trade_date DESC, strength_score DESC, capital_score DESC"
        if top_n and top_n > 0:
            sql += " LIMIT ?"
            params.append(int(top_n))
        return self._read_sql(sql, params)

    def upsert_stock_sector_membership(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df)
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            code = self._normalize_code(row.get("code"))
            sector_code = self._clean_text(row.get("sector_code") or row.get("sector_name"))
            if not code or not sector_code:
                continue
            rows.append(
                (
                    code,
                    self._clean_text(row.get("name")),
                    sector_code,
                    self._clean_text(row.get("sector_name")) or sector_code,
                    self._clean_text(row.get("sector_type")) or "unknown",
                    self._clean_text(row.get("source")) or "unknown",
                    1 if bool(row.get("is_current", True)) else 0,
                    self._raw_json(row),
                    self._now(),
                )
            )
        if not rows:
            return
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO stock_sector_membership
                    (code, name, sector_code, sector_name, sector_type, source,
                     is_current, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, sector_code, sector_type, source) DO UPDATE SET
                    name=excluded.name,
                    sector_name=excluded.sector_name,
                    is_current=excluded.is_current,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_stock_sector_membership(self, code: str, *, current_only: bool = True) -> pd.DataFrame:
        clauses = ["code = ?"]
        params: list[Any] = [self._normalize_code(code)]
        if current_only:
            clauses.append("is_current = 1")
        return self._read_sql(
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
        )

    def upsert_hotspot_news(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df)
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            title = self._clean_text(row.get("title"))
            if not title:
                continue
            news_id = self._clean_text(row.get("id")) or f"{self._clean_text(row.get('source'))}:{self._datetime_text(row.get('published_at'))}:{title}"
            rows.append(
                (
                    news_id,
                    title,
                    self._clean_text(row.get("summary")),
                    self._datetime_text(row.get("published_at")),
                    self._normalize_code(row.get("code")) if row.get("code") else "",
                    self._clean_text(row.get("sector_code")),
                    self._clean_text(row.get("sector_name")),
                    self._clean_text(row.get("url")),
                    self._clean_text(row.get("source")) or "unknown",
                    self._raw_json(row),
                    self._now(),
                )
            )
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO hotspot_news
                    (id, title, summary, published_at, code, sector_code, sector_name,
                     url, source, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    summary=excluded.summary,
                    published_at=excluded.published_at,
                    code=excluded.code,
                    sector_code=excluded.sector_code,
                    sector_name=excluded.sector_name,
                    url=excluded.url,
                    source=excluded.source,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_hotspot_news(self, *, date: Optional[str] = None, code: Optional[str] = None, sector: Optional[str] = None) -> pd.DataFrame:
        clauses = []
        params: list[Any] = []
        if date:
            clauses.append("substr(published_at, 1, 10) = ?")
            params.append(self._date_text(date))
        if code:
            clauses.append("code = ?")
            params.append(self._normalize_code(code))
        if sector:
            clauses.append("(sector_code = ? OR sector_name = ?)")
            params.extend([sector, sector])
        sql = "SELECT * FROM hotspot_news"
        if clauses:
            sql += f" WHERE {' AND '.join(clauses)}"
        sql += " ORDER BY published_at DESC"
        return self._read_sql(sql, params)

    def upsert_limit_up_events(self, df: pd.DataFrame) -> None:
        frame = self._prepare_frame(df)
        if frame.empty:
            return
        rows = []
        for _, row in frame.iterrows():
            trade_date = self._date_text(row.get("trade_date"))
            code = self._normalize_code(row.get("code"))
            if not trade_date or not code:
                continue
            rows.append(
                (
                    trade_date,
                    code,
                    self._clean_text(row.get("name")),
                    self._clean_text(row.get("sector_code")),
                    self._clean_text(row.get("sector_name")),
                    self._float_or_none(row.get("limit_up_price")),
                    self._float_or_none(row.get("turnover")),
                    self._float_or_none(row.get("circulating_market_cap")),
                    self._float_or_none(row.get("total_market_cap")),
                    self._int_or_none(row.get("consecutive_days")),
                    self._int_or_none(row.get("consecutive_count")),
                    self._clean_text(row.get("concept_tags")),
                    self._clean_text(row.get("theme")),
                    self._clean_text(row.get("reason")),
                    self._float_or_none(row.get("seal_amount")),
                    self._float_or_none(row.get("main_net_inflow")),
                    self._clean_text(row.get("first_limit_up_time")),
                    self._int_or_none(row.get("is_first_board")),
                    self._clean_text(row.get("source")) or "unknown",
                    self._raw_json(row),
                    self._now(),
                )
            )
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO limit_up_events
                    (trade_date, code, name, sector_code, sector_name, limit_up_price,
                     turnover, circulating_market_cap, total_market_cap, consecutive_days,
                     consecutive_count, concept_tags, theme, reason, seal_amount,
                     main_net_inflow, first_limit_up_time, is_first_board, source,
                     raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, code, source) DO UPDATE SET
                    name=excluded.name,
                    sector_code=excluded.sector_code,
                    sector_name=excluded.sector_name,
                    limit_up_price=excluded.limit_up_price,
                    turnover=excluded.turnover,
                    circulating_market_cap=excluded.circulating_market_cap,
                    total_market_cap=excluded.total_market_cap,
                    consecutive_days=excluded.consecutive_days,
                    consecutive_count=excluded.consecutive_count,
                    concept_tags=excluded.concept_tags,
                    theme=excluded.theme,
                    reason=excluded.reason,
                    seal_amount=excluded.seal_amount,
                    main_net_inflow=excluded.main_net_inflow,
                    first_limit_up_time=excluded.first_limit_up_time,
                    is_first_board=excluded.is_first_board,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def get_limit_up_events(self, *, date: Optional[str] = None, code: Optional[str] = None) -> pd.DataFrame:
        clauses = []
        params: list[Any] = []
        if date:
            clauses.append("trade_date = ?")
            params.append(self._date_text(date))
        if code:
            clauses.append("code = ?")
            params.append(self._normalize_code(code))
        sql = "SELECT * FROM limit_up_events"
        if clauses:
            sql += f" WHERE {' AND '.join(clauses)}"
        sql += " ORDER BY trade_date DESC, consecutive_days DESC, seal_amount DESC"
        return self._read_sql(sql, params)

    def start_sync_run(self, job_name: str, *, data_type: str = "", params: Optional[dict[str, Any]] = None) -> int:
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO sync_runs (job_name, data_type, started_at, status, params_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [job_name, data_type, self._now(), "running", json.dumps(params or {}, ensure_ascii=False, default=str)],
            )
            return int(cur.lastrowid)

    def finish_sync_run(self, run_id: int, *, status: str, rows_written: int = 0, error: str = "") -> None:
        with self._connect() as con:
            con.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, status = ?, rows_written = ?, error = ?
                WHERE id = ?
                """,
                [self._now(), status, int(rows_written), error, int(run_id)],
            )

    def get_sync_runs(self, limit: int = 20) -> pd.DataFrame:
        return self._read_sql("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", [int(limit)])

    def update_data_freshness(
        self,
        data_type: str,
        scope: str,
        *,
        latest_time: Optional[str],
        row_count: int = 0,
        source: str = "",
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO data_freshness
                    (data_type, scope, latest_time, row_count, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_type, scope) DO UPDATE SET
                    latest_time=excluded.latest_time,
                    row_count=excluded.row_count,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                [data_type, scope, latest_time, int(row_count), source, self._now()],
            )

    def get_data_freshness(self, data_type: Optional[str] = None) -> pd.DataFrame:
        params: list[Any] = []
        sql = "SELECT * FROM data_freshness"
        if data_type:
            sql += " WHERE data_type = ?"
            params.append(data_type)
        sql += " ORDER BY data_type, scope"
        return self._read_sql(sql, params)

    def _read_sql(self, sql: str, params: Optional[list[Any]] = None) -> pd.DataFrame:
        with self._connect() as con:
            return pd.read_sql_query(sql, con, params=params or [])

    @staticmethod
    def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()

    @staticmethod
    def _normalize_code(value: Any) -> str:
        text = str(value or "").strip()
        if text.endswith(".0"):
            text = text[:-2]
        text = text.replace("sh", "").replace("sz", "").replace("bj", "")
        return text.zfill(6) if text.isdigit() and len(text) <= 6 else text

    @staticmethod
    def _normalize_period(value: Any) -> str:
        text = str(value or "d").strip().lower()
        return {"daily": "d", "day": "d", "weekly": "w", "monthly": "m", "1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "60m"}.get(text, text)

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except TypeError:
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "nat"} else text

    @classmethod
    def _datetime_text(cls, value: Any) -> str:
        text = cls._clean_text(value)
        if not text:
            return ""
        try:
            return pd.to_datetime(text).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return text[:19]

    @classmethod
    def _end_datetime_text(cls, value: Any, period: str) -> str:
        text = cls._datetime_text(value)
        if text.endswith("00:00:00") and cls._normalize_period(period) in {"d", "w", "m"}:
            return text[:10] + " 23:59:59"
        return text

    @classmethod
    def _date_text(cls, value: Any) -> str:
        text = cls._clean_text(value)
        if not text:
            return ""
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            return pd.to_datetime(text).strftime("%Y-%m-%d")
        except Exception:
            return text[:10]

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            if value is None or pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        try:
            if value is None or pd.isna(value):
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _raw_json(row: pd.Series) -> str:
        return json.dumps(row.to_dict(), ensure_ascii=False, default=str)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
