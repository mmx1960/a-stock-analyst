from __future__ import annotations

from typing import Any

import pandas as pd


class DirectMarketSource:
    """MarketSource adapter that calls concrete providers without DuckDB read-through."""

    name = "direct"

    def __init__(self):
        self.mootdx = None
        self.tencent = None
        self.akshare = None

    def _mootdx(self):
        if self.mootdx is None:
            from app.core.providers.mootdx_provider import MootdxProvider

            self.mootdx = MootdxProvider()
        return self.mootdx

    def _tencent(self):
        if self.tencent is None:
            from app.core.providers.tencent_provider import TencentProvider

            self.tencent = TencentProvider()
        return self.tencent

    def _akshare(self):
        if self.akshare is None:
            from app.core.providers.akshare_provider import AkshareProvider

            self.akshare = AkshareProvider()
        return self.akshare

    def fetch_stock_list(self) -> list[dict[str, Any]]:
        try:
            rows = self._mootdx().get_stock_list()
            if rows:
                return rows
        except Exception:
            pass
        try:
            return self._akshare().get_stock_list() or []
        except Exception:
            return []

    def fetch_realtime_quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code in codes:
            quote: dict[str, Any] = {"code": code}
            try:
                base = self._mootdx().get_realtime_quote(code) or {}
                quote.update(base)
            except Exception:
                pass
            try:
                extra = self._tencent().get_realtime_quote_extra(code) or {}
                quote.update(extra)
            except Exception:
                pass
            if len(quote) > 1:
                quote.setdefault("source_main", "direct")
                rows.append(quote)
        return rows

    def fetch_kline(
        self,
        code: str,
        period: str,
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        normalized_period = self._normalize_period(period)
        if normalized_period in {"d", "w", "m"}:
            for provider_factory, source_name in ((self._mootdx, "mootdx"), (self._akshare, "akshare")):
                try:
                    frame = provider_factory().get_daily_bars(code=code, start_date=start_date, end_date=end_date, adjust=adjust)
                except Exception:
                    frame = pd.DataFrame()
                if frame is not None and not frame.empty:
                    frame = frame.copy()
                    frame["source"] = frame.get("source", source_name)
                    return frame
            return pd.DataFrame()
        minute_period = normalized_period[:-1] if normalized_period.endswith("m") else normalized_period
        for provider_factory, source_name in ((self._mootdx, "mootdx"), (self._akshare, "akshare")):
            try:
                frame = provider_factory().get_minute_bars(code=code, period=minute_period, start_date=start_date, end_date=end_date)
            except Exception:
                frame = pd.DataFrame()
            if frame is not None and not frame.empty:
                frame = frame.copy()
                frame["source"] = frame.get("source", source_name)
                return frame
        return pd.DataFrame()

    @staticmethod
    def _normalize_period(value: str) -> str:
        text = str(value or "d").strip().lower()
        return {
            "daily": "d",
            "day": "d",
            "weekly": "w",
            "monthly": "m",
            "1": "1m",
            "5": "5m",
            "15": "15m",
            "30": "30m",
            "60": "60m",
        }.get(text, text)
