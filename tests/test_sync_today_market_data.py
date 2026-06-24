from __future__ import annotations

import pandas as pd

from scripts import sync_today_market_data as sync_today


class FakeStore:
    def __init__(self):
        self.written = None

    def get_stock_basic(self):
        return pd.DataFrame([{"code": "1"}, {"code": "000002"}, {"code": "300001"}])

    def upsert_realtime_quote_snapshot(self, df: pd.DataFrame) -> None:
        self.written = df.copy()


class FakeProvider:
    def get_realtime_quote(self, code: str):
        if code == "000002":
            return None
        return {
            "code": code,
            "name": f"股票{code}",
            "latest_price": "12.34",
            "涨跌幅": "1.5",
            "成交额": "123000000",
            "source": "fake_provider",
        }


def test_resolve_codes_prefers_explicit_codes_and_applies_offset_limit() -> None:
    store = FakeStore()

    assert sync_today.resolve_codes(store=store, codes=["1", "000002", "300001"], from_db_stock_list=True, offset=1, limit=1) == ["000002"]


def test_resolve_codes_from_db_stock_list() -> None:
    store = FakeStore()

    assert sync_today.resolve_codes(store=store, codes=[], from_db_stock_list=True, offset=1, limit=2) == ["000002", "300001"]


def test_normalize_quote_maps_common_fields() -> None:
    row = sync_today.normalize_quote("1", {"name": "平安银行", "latest_price": "12.34", "成交额": "123000000"}, source="unit")

    assert row is not None
    assert row["code"] == "000001"
    assert row["name"] == "平安银行"
    assert row["price"] == 12.34
    assert row["amount"] == 123000000.0
    assert row["source"] == "unit"


def test_sync_realtime_quotes_writes_non_empty_quotes() -> None:
    store = FakeStore()

    summary = sync_today.sync_realtime_quotes(store=store, codes=["000001", "000002"], provider=FakeProvider())

    assert summary["requested"] == 2
    assert summary["synced"] == 1
    assert summary["empty"] == 1
    assert summary["empty_codes"] == ["000002"]
    assert store.written is not None
    assert list(store.written["code"]) == ["000001"]
    assert store.written.iloc[0]["price"] == 12.34
