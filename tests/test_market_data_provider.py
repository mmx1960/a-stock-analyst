from __future__ import annotations

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider


class FakeStore:
    def get_stock_basic(self, limit=None):
        frame = pd.DataFrame([{"code": "000001", "name": "平安银行"}])
        return frame.head(limit) if limit else frame

    def get_daily_kline(self, **kwargs):
        return pd.DataFrame([{**kwargs, "trade_date": "2026-06-19", "close": 10.0}])

    def get_minute_kline(self, **kwargs):
        return pd.DataFrame([{**kwargs, "trade_dt": "2026-06-19 10:00:00", "close": 10.0}])

    def get_realtime_quote_snapshot(self, code):
        if str(code).zfill(6) == "000001":
            return pd.DataFrame([{"code": "000001", "name": "000001", "price": 11.5, "amount": 200_000_000, "source": "snapshot_test"}])
        return pd.DataFrame()

    def get_stock_sector_membership(self, **kwargs):
        return pd.DataFrame([{**kwargs, "sector_name": "银行"}])

    def get_kaipanla_sector_strength(self, **kwargs):
        return pd.DataFrame([{**kwargs, "sector_name": "银行", "strength_score": 70}])


def test_duckdb_market_data_provider_delegates_to_store() -> None:
    provider = DuckDBMarketDataProvider(store=FakeStore())

    assert provider.get_stock_list(limit=1).iloc[0]["code"] == "000001"
    assert provider.get_daily_bars("000001", start_date="2026-01-01").iloc[0]["code"] == "000001"
    assert provider.get_minute_bars("000001", period="30").iloc[0]["period"] == "30"
    quote = provider.get_realtime_quote("000001")
    assert quote["name"] == "平安银行"
    assert quote["price"] == 11.5
    assert quote["source"] == "snapshot_test"
    assert provider.get_realtime_quote("000999")["source"] == "duckdb_empty_quote"
    assert provider.get_sector_membership("000001").iloc[0]["sector_name"] == "银行"
    assert provider.get_sector_strength("2026-06-01", "2026-06-19").iloc[0]["strength_score"] == 70
