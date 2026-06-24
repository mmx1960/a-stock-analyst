from __future__ import annotations

from typing import Callable, Optional, TypeVar

import os
from contextlib import contextmanager

import akshare as ak
import pandas as pd

from app.core.providers.base import BaseMarketDataProvider

T = TypeVar("T")
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@contextmanager
def _without_env_proxy():
    old = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _call_akshare_without_proxy(func: Callable[..., T], *args, **kwargs) -> T:
    with _without_env_proxy():
        return func(*args, **kwargs)


class AkshareProvider(BaseMarketDataProvider):
    """低频补充 provider。"""

    def get_stock_list(self) -> list[dict]:
        try:
            df = _call_akshare_without_proxy(ak.stock_info_a_code_name)
            if df is None or df.empty:
                return []
            return [
                {
                    "code": str(row.get("code", "")).strip(),
                    "name": str(row.get("name", "")).strip(),
                    "source": "akshare",
                }
                for _, row in df.iterrows()
            ]
        except Exception:
            return []

    def get_realtime_quote(self, code: str) -> Optional[dict]:
        return None

    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        try:
            return _call_akshare_without_proxy(
                ak.stock_zh_a_hist,
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        except Exception:
            return pd.DataFrame()

    def get_minute_bars(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        if str(period) not in {"1", "5", "15", "30", "60"}:
            return pd.DataFrame()
        start = _normalize_minute_datetime(start_date, default="1979-09-01 09:30:00")
        end = _normalize_minute_datetime(end_date, default="2222-01-01 15:00:00", is_end=True)
        df = pd.DataFrame()
        source = "akshare_hist_min_em"
        try:
            df = _call_akshare_without_proxy(
                ak.stock_zh_a_hist_min_em,
                symbol=str(code).zfill(6),
                start_date=start,
                end_date=end,
                period=str(period),
                adjust="",
            )
        except Exception:
            df = pd.DataFrame()
        if df is None or df.empty:
            try:
                df = _call_akshare_without_proxy(
                    ak.stock_zh_a_minute,
                    symbol=_with_exchange_prefix(code),
                    period=str(period),
                    adjust="",
                )
                source = "akshare_sina_minute"
                if df is not None and not df.empty:
                    df = _filter_minute_range(df, start=start, end=end)
            except Exception:
                df = pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        try:
            frame = df.copy()
            rename_map = {
                "时间": "trade_dt",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }
            frame = frame.rename(columns=rename_map)
            if "trade_dt" not in frame.columns and "datetime" in frame.columns:
                frame = frame.rename(columns={"datetime": "trade_dt"})
            if "trade_dt" not in frame.columns and "day" in frame.columns:
                frame = frame.rename(columns={"day": "trade_dt"})
            if "trade_dt" in frame.columns:
                frame["trade_dt"] = pd.to_datetime(frame["trade_dt"])
            frame["source"] = source
            return frame
        except Exception:
            return pd.DataFrame()


def _normalize_minute_datetime(value: Optional[str], *, default: str, is_end: bool = False) -> str:
    if not value:
        return default
    text = str(value).strip()
    if " " in text:
        return text
    normalized = text.replace("-", "")
    if len(normalized) == 8 and normalized.isdigit():
        date = f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"
    else:
        date = text
    return f"{date} {'15:00:00' if is_end else '09:30:00'}"


def _with_exchange_prefix(code: str) -> str:
    normalized = str(code).zfill(6)
    return f"sh{normalized}" if normalized.startswith(("5", "6", "9")) else f"sz{normalized}"


def _filter_minute_range(df: pd.DataFrame, *, start: str, end: str) -> pd.DataFrame:
    frame = df.copy()
    dt_col = "day" if "day" in frame.columns else "trade_dt" if "trade_dt" in frame.columns else "datetime" if "datetime" in frame.columns else "时间" if "时间" in frame.columns else ""
    if not dt_col:
        return frame
    dt = pd.to_datetime(frame[dt_col])
    mask = (dt >= pd.to_datetime(start)) & (dt <= pd.to_datetime(end))
    return frame.loc[mask].copy()
