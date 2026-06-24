from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class MarketSource(Protocol):
    name: str

    def fetch_stock_list(self) -> list[dict[str, Any]]:
        ...

    def fetch_realtime_quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        ...

    def fetch_kline(
        self,
        code: str,
        period: str,
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        ...


class SectorSource(Protocol):
    name: str

    def fetch_sector_strength(self, trade_date: str | None = None) -> pd.DataFrame:
        ...

    def fetch_stock_sector_membership(self, trade_date: str | None = None) -> pd.DataFrame:
        ...


class HotspotSource(Protocol):
    name: str

    def fetch_limit_up(self, trade_date: str | None = None) -> pd.DataFrame:
        ...

    def fetch_hot_news(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> pd.DataFrame:
        ...
