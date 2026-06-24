from __future__ import annotations

import pandas as pd

from app.core.storage.duckdb_store import DuckDBStore


def test_realtime_quote_snapshot_roundtrip(tmp_path) -> None:
    store = DuckDBStore(db_path=tmp_path / "quotes.duckdb")

    store.upsert_realtime_quote_snapshot(
        pd.DataFrame([
            {
                "code": "1",
                "name": "平安银行",
                "latest_price": 12.34,
                "change_pct": 1.2,
                "成交额": 123_000_000,
                "source": "unit_test",
            }
        ])
    )

    result = store.get_realtime_quote_snapshot("000001")

    assert len(result) == 1
    assert result.iloc[0]["code"] == "000001"
    assert result.iloc[0]["name"] == "平安银行"
    assert result.iloc[0]["price"] == 12.34
    assert result.iloc[0]["amount"] == 123_000_000
    assert result.iloc[0]["source"] == "unit_test"
