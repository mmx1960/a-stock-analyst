from __future__ import annotations

from typing import Optional

import pandas as pd

from app.core.providers.base import BaseMarketDataProvider

try:
    import baostock as bs
except Exception:  # pragma: no cover
    bs = None


class BaostockProvider(BaseMarketDataProvider):
    """Baostock historical K-line provider.

    Baostock is slower than local DuckDB but has stable public historical minute
    K-line coverage for A shares. It is used as a backfill source, not for
    realtime quotes.
    """

    PERIOD_MAP = {"5": "5", "15": "15", "30": "30", "60": "60", "daily": "d"}

    def __init__(self, *, auto_logout: bool = True):
        self.auto_logout = auto_logout
        self._logged_in = False

    def get_stock_list(self) -> list[dict]:
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
        return pd.DataFrame()

    def get_minute_bars(
        self,
        code: str,
        period: str = "30",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        if bs is None or str(period) not in self.PERIOD_MAP or str(period) == "daily":
            return pd.DataFrame()
        start = _normalize_date(start_date, default="1990-01-01")
        end = _normalize_date(end_date, default="2050-01-01")
        baocode = _to_baostock_code(code)
        lg = self._ensure_login()
        try:
            if getattr(lg, "error_code", "0") != "0":
                return pd.DataFrame()
            rs = bs.query_history_k_data_plus(
                baocode,
                "date,time,code,open,high,low,close,volume,amount",
                start_date=start,
                end_date=end,
                frequency=str(period),
                adjustflag="3",
            )
            rows: list[list[str]] = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame()
            frame = pd.DataFrame(rows, columns=rs.fields)
            frame["trade_dt"] = pd.to_datetime(frame["time"].astype(str).str.slice(0, 14), format="%Y%m%d%H%M%S", errors="coerce")
            frame["code"] = str(code).zfill(6)
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            frame["source"] = "baostock"
            return frame[["trade_dt", "open", "high", "low", "close", "volume", "amount", "source", "code"]].dropna(subset=["trade_dt"])
        finally:
            if self.auto_logout:
                self.close()

    def _ensure_login(self):
        if bs is None:
            raise RuntimeError("baostock unavailable")
        if self._logged_in:
            class _LoginResult:
                error_code = "0"
                error_msg = "success"
            return _LoginResult()
        lg = bs.login()
        self._logged_in = getattr(lg, "error_code", "0") == "0"
        return lg

    def close(self) -> None:
        if bs is None or not self._logged_in:
            return
        try:
            bs.logout()
        finally:
            self._logged_in = False


def _to_baostock_code(code: str) -> str:
    normalized = str(code).strip()
    if normalized.startswith(("sh.", "sz.", "bj.")):
        return normalized
    normalized = normalized[-6:].zfill(6)
    if normalized.startswith(("5", "6", "9")):
        return f"sh.{normalized}"
    return f"sz.{normalized}"


def _normalize_date(value: Optional[str], *, default: str) -> str:
    if not value:
        return default
    text = str(value).strip()
    if len(text) >= 10 and "-" in text:
        return text[:10]
    compact = text.replace("-", "")[:8]
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return text[:10]
