from __future__ import annotations

import pandas as pd

from scripts import sync_history_kline


def test_normalize_minute_frame_maps_common_columns() -> None:
    frame = pd.DataFrame(
        [
            {
                "datetime": "2026-06-18 10:00:00",
                "open": 10,
                "high": 10.5,
                "low": 9.9,
                "close": 10.2,
                "vol": 1000,
                "turnover": 2000,
            }
        ]
    )

    normalized = sync_history_kline.normalize_minute_frame(frame, code="1", period="30")

    assert list(normalized.columns) == [
        "code",
        "period",
        "trade_dt",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
        "updated_at",
    ]
    assert normalized.iloc[0]["code"] == "1"
    assert normalized.iloc[0]["period"] == "30"
    assert normalized.iloc[0]["volume"] == 1000
    assert normalized.iloc[0]["amount"] == 2000


def test_resolve_codes_from_db_applies_limit_after_offset() -> None:
    class Store:
        def get_stock_basic(self, limit=None):
            return pd.DataFrame([{"code": "000001"}, {"code": "000002"}, {"code": "000003"}])

    class Args:
        codes = []
        from_db_stock_list = True
        limit = 1
        offset = 1

    assert sync_history_kline.resolve_codes(Args(), Store()) == ["000002"]
