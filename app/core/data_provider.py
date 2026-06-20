"""
统一数据层 - 多源 A 股数据接口门面
主链路：DuckDB + mootdx + 腾讯财经
低频补充：AKShare
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from functools import wraps
from typing import Optional

import akshare as ak
import pandas as pd

from app.core.config import CACHE_CONFIG, MIN_REQUEST_INTERVAL
from app.core.providers.composite_provider import CompositeProvider

logger = logging.getLogger(__name__)


class LRUCache:
    """带 TTL 和容量限制的 LRU 缓存，防止 OOM。"""

    def __init__(self, maxsize: int = 200):
        self._data: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str):
        if key in self._data:
            entry = self._data[key]
            if time.time() - entry["ts"] < entry["ttl"]:
                self._data.move_to_end(key)
                return entry["data"]
            del self._data[key]
        return None

    def set(self, key: str, data, ttl: int):
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = {"data": data, "ts": time.time(), "ttl": ttl}
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self):
        self._data.clear()


cache = LRUCache(maxsize=100)


def _is_read_only_mode() -> bool:
    return os.getenv("ASHARE_DUCKDB_READ_ONLY", "0") == "1"


def _throttle():
    if not hasattr(_throttle, "_last"):
        _throttle._last = 0
    elapsed = time.time() - _throttle._last
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _throttle._last = time.time()


def _safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _handle_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"数据获取失败 [{func.__name__}]: {e}")
            return None
    return wrapper


class DataProvider:
    """A 股统一数据门面，对外保持原接口，内部切到多源 provider。"""

    def __init__(self):
        self._cache = cache
        self.composite = CompositeProvider()

    def convert_code(self, code: str) -> str:
        return str(code).split(".")[0].replace("sh", "").replace("sz", "").replace("bj", "")

    def get_market_prefix(self, code: str) -> str:
        code = self.convert_code(code)
        if code.startswith("6"):
            return "sh"
        if code.startswith(("0", "3")):
            return "sz"
        if code.startswith(("4", "8")):
            return "bj"
        return "sh"

    @_handle_errors
    def get_stock_list(self) -> Optional[list]:
        cache_key = "stock_list"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        _throttle()
        stocks = self.composite.get_stock_list()
        if stocks:
            self._cache.set(cache_key, stocks, CACHE_CONFIG["stock_list"])
        return stocks

    @_handle_errors
    def get_realtime_quote(self, code: str) -> Optional[dict]:
        code = self.convert_code(code)
        cache_key = f"quote_{code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        _throttle()
        quote = self.composite.get_realtime_quote(code)
        if quote:
            quote.setdefault("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._cache.set(cache_key, quote, CACHE_CONFIG["realtime_quote"])
        return quote

    @_handle_errors
    def get_kline_daily(
        self,
        code: str,
        period: str = "daily",
        start_date: str = "",
        end_date: str = "",
    ) -> Optional[pd.DataFrame]:
        code = self.convert_code(code)
        cache_key = f"kline_{code}_{period}_{start_date}_{end_date}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        _throttle()
        df = self.composite.get_daily_bars(code=code, start_date=start_date or None, end_date=end_date or None, adjust="hfq")
        if df is not None and not df.empty:
            if not _is_read_only_mode():
                self._cache.set(cache_key, df, CACHE_CONFIG["kline_daily"])
            return df
        return None

    @_handle_errors
    def get_kline_minute(self, code: str, period: str = "1") -> Optional[pd.DataFrame]:
        code = self.convert_code(code)
        cache_key = f"kline_min_{code}_{period}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        _throttle()
        df = self.composite.get_minute_bars(code=code, period=period)
        if df is not None and not df.empty:
            self._cache.set(cache_key, df, CACHE_CONFIG["kline_minute"])
            return df
        return None

    @_handle_errors
    def get_market_overview(self) -> Optional[dict]:
        _throttle()
        df = ak.stock_zh_index_spot_em()
        if df is None or df.empty:
            return None
        target_codes = {
            "000001": "上证指数",
            "399001": "深证成指",
            "399006": "创业板指",
            "000688": "科创50",
            "000300": "沪深300",
            "000905": "中证500",
        }
        indices = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code in target_codes:
                indices[code] = {
                    "name": target_codes[code],
                    "price": _safe_float(row.get("最新价", 0)),
                    "change_pct": _safe_float(row.get("涨跌幅", 0)),
                    "turnover": _safe_float(row.get("成交额", 0)),
                }
        return {"indices": indices, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    @_handle_errors
    def get_fundamental(self, code: str) -> Optional[dict]:
        code = self.convert_code(code)
        cache_key = f"fund_{code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        _throttle()
        result = self.composite.get_realtime_quote(code) or {"code": code}
        try:
            info = ak.stock_individual_info_em(symbol=code)
            if info is not None and not info.empty:
                for _, row in info.iterrows():
                    key = str(row.get("item", ""))
                    val = str(row.get("value", ""))
                    if "行业" in key:
                        result["industry"] = val
                    elif "地区" in key:
                        result["region"] = val
                    elif "上市" in key:
                        result["list_date"] = val
        except Exception:
            pass
        self._cache.set(cache_key, result, CACHE_CONFIG["fundamental"])
        return result

    @_handle_errors
    def get_capital_flow(self, code: str) -> Optional[dict]:
        code = self.convert_code(code)
        market = "sh" if code.startswith("6") else "sz"
        _throttle()
        try:
            df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is None or df.empty:
                return None
            latest = df.iloc[-1]
            return {
                "code": code,
                "main_net_inflow": _safe_float(latest.get("主力净流入-净额", 0)),
                "super_large_net": _safe_float(latest.get("超大单净流入-净额", 0)),
                "large_net": _safe_float(latest.get("大单净流入-净额", 0)),
                "medium_net": _safe_float(latest.get("中单净流入-净额", 0)),
                "small_net": _safe_float(latest.get("小单净流入-净额", 0)),
                "history": df.tail(30).to_dict("records"),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception:
            return None

    @_handle_errors
    def get_northbound_flow(self) -> Optional[dict]:
        _throttle()
        try:
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df is None or df.empty:
                return None
            latest = df.iloc[0]
            return {
                "net_flow": _safe_float(latest.get("当日净买额", 0)),
                "buy": _safe_float(latest.get("当日买入额", 0)),
                "sell": _safe_float(latest.get("当日卖出额", 0)),
                "history": df.head(30).to_dict("records"),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception:
            return None

    @_handle_errors
    def get_sector_rank(self, indicator: str = "今日涨跌排名") -> Optional[list]:
        _throttle()
        try:
            df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                return None
            sectors = []
            for _, row in df.iterrows():
                sectors.append(
                    {
                        "name": str(row.get("板块名称", "")),
                        "code": str(row.get("板块代码", "")),
                        "change_pct": _safe_float(row.get("涨跌幅", 0)),
                        "turnover": _safe_float(row.get("成交额", 0)),
                    }
                )
            sectors.sort(key=lambda x: x["change_pct"], reverse=("涨" in indicator))
            return sectors
        except Exception:
            return None


# 全局单例
data_provider = DataProvider()
