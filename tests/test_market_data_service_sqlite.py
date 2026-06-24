from __future__ import annotations

import pandas as pd

from app.core.services.market_data_service import MarketDataService
from app.core.storage.sqlite_store import SQLiteStore


class FakeMarketSource:
    name = "fake"

    def __init__(self) -> None:
        self.kline_calls = 0

    def fetch_stock_list(self):
        return [{"code": "000001", "name": "Ping An", "source": self.name}]

    def fetch_realtime_quotes(self, codes):
        return [{"code": code, "price": 10.5, "source_main": self.name} for code in codes]

    def fetch_kline(self, code, period, start_date=None, end_date=None, adjust="hfq"):
        self.kline_calls += 1
        return pd.DataFrame(
            [
                {
                    "code": code,
                    "period": period,
                    "trade_time": "2026-06-19",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                    "amount": 1000,
                    "adjust": adjust,
                    "source": self.name,
                }
            ]
        )


class FakeSectorSource:
    name = "tdx"

    def fetch_sector_strength(self, trade_date=None):
        return pd.DataFrame()

    def fetch_stock_sector_membership(self, trade_date=None, **_):
        return pd.DataFrame(
            [
                {
                    "code": "000001",
                    "name": "Ping An",
                    "sector_code": "I65",
                    "sector_name": "软件开发",
                    "sector_type": "tdx_industry_l3",
                    "source": "tdx",
                }
            ]
        )


def test_market_data_service_reads_cache_before_source(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")
    source = FakeMarketSource()
    service = MarketDataService(store=store, market_sources=[source])

    first = service.get_kline("000001", period="d", start_date="2026-06-01", end_date="2026-06-30")
    second = service.get_kline("000001", period="d", start_date="2026-06-01", end_date="2026-06-30")

    assert first.iloc[0]["close"] == 10.5
    assert second.iloc[0]["close"] == 10.5
    assert source.kline_calls == 1


def test_market_data_service_refreshes_quote_and_stock_list(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")
    service = MarketDataService(store=store, market_sources=[FakeMarketSource()])

    stocks = service.get_stock_list()
    quote = service.get_realtime_quote("000001", refresh=True)

    assert stocks.iloc[0]["code"] == "000001"
    assert quote["price"] == 10.5
    assert store.get_realtime_quote_snapshot("000001").iloc[0]["price"] == 10.5


def test_market_data_service_syncs_sector_membership_from_tdx_source(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")
    service = MarketDataService(store=store, market_sources=[FakeMarketSource()], sector_sources=[FakeSectorSource()])

    summary = service.sync_sector_membership(source="tdx")

    assert summary["rows_written"] == 1
    assert store.get_stock_sector_membership("000001").iloc[0]["sector_type"] == "tdx_industry_l3"
