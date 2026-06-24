from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.providers.composite_provider import CompositeProvider


class CompositeMarketSource:
    """MarketSource adapter over the existing multi-source provider stack."""

    name = "composite"

    def __init__(self, provider: CompositeProvider | None = None):
        self.provider = provider or CompositeProvider()

    def fetch_stock_list(self) -> list[dict[str, Any]]:
        return self.provider.get_stock_list() or []

    def fetch_realtime_quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code in codes:
            quote = self.provider.get_realtime_quote(code)
            if quote:
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
            frame = self.provider.get_daily_bars(
                code=code,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            return frame if frame is not None else pd.DataFrame()
        minute_period = normalized_period[:-1] if normalized_period.endswith("m") else normalized_period
        frame = self.provider.get_minute_bars(
            code=code,
            period=minute_period,
            start_date=start_date,
            end_date=end_date,
        )
        return frame if frame is not None else pd.DataFrame()

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
