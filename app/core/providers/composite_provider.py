from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from app.core.providers.akshare_provider import AkshareProvider
from app.core.providers.baostock_provider import BaostockProvider
from app.core.providers.base import BaseMarketDataProvider
from app.core.providers.mootdx_provider import MootdxProvider
from app.core.providers.tencent_provider import TencentProvider
from app.core.storage.duckdb_store import DuckDBStore


class CompositeProvider(BaseMarketDataProvider):
    """多源聚合 provider：DuckDB + mootdx + 腾讯 + AKShare fallback。"""

    MIN_STOCK_BASIC_ROWS = 1000
    MIN_DAILY_BARS_FOR_CACHE = 200

    def __init__(self):
        self.store = DuckDBStore()
        self.mootdx = MootdxProvider()
        self.baostock = BaostockProvider()
        self.tencent = TencentProvider()
        self.akshare = AkshareProvider()

    def get_stock_list(self) -> list[dict]:
        local_df = self.store.get_stock_basic()
        if local_df is not None and not local_df.empty and len(local_df) >= self.MIN_STOCK_BASIC_ROWS:
            return local_df.fillna("").to_dict("records")

        if self._is_read_only_mode():
            if local_df is not None and not local_df.empty:
                return local_df.fillna("").to_dict("records")
            stocks = self.mootdx.get_stock_list()
            return stocks or self.akshare.get_stock_list()

        stocks = self.mootdx.get_stock_list()
        if stocks:
            self.store.upsert_stock_basic(pd.DataFrame(stocks))
            return stocks

        if local_df is not None and not local_df.empty:
            return local_df.fillna("").to_dict("records")

        stocks = self.akshare.get_stock_list()
        if stocks:
            self.store.upsert_stock_basic(pd.DataFrame(stocks))
        return stocks

    def get_realtime_quote(self, code: str) -> Optional[dict]:
        base_quote = self.mootdx.get_realtime_quote(code) or {"code": code, "source_main": "mootdx"}
        extra_quote = self.tencent.get_realtime_quote_extra(code) or {}
        merged = {**base_quote, **extra_quote}
        merged.setdefault("code", code)
        merged.setdefault("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return merged

    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        df = self.store.get_daily_kline(code=code, start_date=start_date, end_date=end_date, adjust=adjust)
        if self._is_usable_daily_cache(df, start_date=start_date, end_date=end_date):
            return df

        if self._is_read_only_mode():
            return df if df is not None and not df.empty else pd.DataFrame()

        try:
            df = self.mootdx.get_daily_bars(code=code, start_date=start_date, end_date=end_date, adjust=adjust)
        except Exception:
            df = pd.DataFrame()
        if df is not None and not df.empty:
            frame = self._normalize_daily_frame(df=df, code=code, adjust=adjust, source="mootdx")
            self.store.upsert_daily_kline(frame)
            return frame

        fallback_adjust = "qfq" if adjust == "hfq" else adjust
        df = self.akshare.get_daily_bars(code=code, start_date=start_date, end_date=end_date, adjust=fallback_adjust)
        if df is None or df.empty:
            return df
        frame = self._normalize_daily_frame(df=df, code=code, adjust=fallback_adjust, source="akshare")
        self.store.upsert_daily_kline(frame)
        return frame

    def get_minute_bars(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        df = self.store.get_minute_kline(code=code, period=period, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            return df

        df = self.baostock.get_minute_bars(code=code, period=period, start_date=start_date, end_date=end_date)
        source = "baostock"
        if df is None or df.empty:
            df = self.mootdx.get_minute_bars(code=code, period=period, start_date=start_date, end_date=end_date)
            source = "mootdx"
        if df is None or df.empty:
            df = self.akshare.get_minute_bars(code=code, period=period, start_date=start_date, end_date=end_date)
            source = "akshare_hist_min_em"
        if df is None or df.empty:
            return df

        frame = df.copy()
        if "code" not in frame.columns:
            frame["code"] = code
        frame["period"] = str(period)
        frame["source"] = frame.get("source", source)
        self.store.upsert_minute_kline(frame)
        return frame

    def _is_usable_daily_cache(
        self,
        df: Optional[pd.DataFrame],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        if df is None or df.empty:
            return False
        if start_date:
            return len(df) >= self.MIN_DAILY_BARS_FOR_CACHE
        if end_date:
            return len(df) >= self.MIN_DAILY_BARS_FOR_CACHE
        return True

    @staticmethod
    def _is_read_only_mode() -> bool:
        import os
        return os.getenv("ASHARE_DUCKDB_READ_ONLY", "0") == "1"

    @staticmethod
    def _normalize_daily_frame(df: pd.DataFrame, code: str, adjust: str, source: str) -> pd.DataFrame:
        frame = df.copy()
        rename_map = {
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "换手率": "turnover_rate",
            "涨跌幅": "change_pct",
        }
        frame = frame.rename(columns=rename_map)
        if "code" not in frame.columns:
            frame["code"] = code
        if "trade_date" not in frame.columns and "date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["date"]).dt.date
        elif "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        frame["adjust"] = adjust
        frame["source"] = source
        return frame
