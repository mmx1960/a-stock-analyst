from __future__ import annotations

import pandas as pd

from app.core.storage.sqlite_store import SQLiteStore


def test_sqlite_store_roundtrips_core_market_data(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")

    store.upsert_stock_basic(pd.DataFrame([{"code": "1", "name": "Ping An", "source": "unit"}]))
    assert store.get_stock_basic().iloc[0]["code"] == "000001"

    store.upsert_kline_bars(
        pd.DataFrame(
            [
                {
                    "code": "1",
                    "period": "d",
                    "trade_time": "2026-06-19",
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                    "volume": 100,
                    "amount": 1000,
                    "adjust": "hfq",
                    "source": "unit",
                }
            ]
        )
    )
    bars = store.get_kline_bars("000001", period="d", start_date="2026-06-01", end_date="2026-06-30")
    assert len(bars) == 1
    assert bars.iloc[0]["close"] == 11

    store.upsert_realtime_quote_snapshot(pd.DataFrame([{"code": "1", "price": 12.3, "source_main": "unit"}]))
    quote = store.get_realtime_quote_snapshot("000001")
    assert quote.iloc[0]["price"] == 12.3

    store.upsert_sector_strength(pd.DataFrame([{"trade_date": "2026-06-19", "sector_code": "BK1", "sector_name": "AI", "strength_score": 88}]))
    assert store.get_sector_strength("2026-06-19", "2026-06-19").iloc[0]["sector_name"] == "AI"

    store.upsert_stock_sector_membership(
        pd.DataFrame(
            [
                {"code": "1", "sector_code": "T1", "sector_name": "一级", "sector_type": "tdx_industry_l1"},
                {"code": "1", "sector_code": "BK1", "sector_name": "AI", "sector_type": "concept"},
                {"code": "1", "sector_code": "T3", "sector_name": "三级", "sector_type": "tdx_industry_l3"},
                {"code": "1", "sector_code": "T2", "sector_name": "二级", "sector_type": "tdx_industry_l2"},
            ]
        )
    )
    sectors = store.get_stock_sector_membership("000001")
    assert sectors.iloc[0]["sector_name"] == "三级"
    assert sectors["sector_type"].tolist()[:3] == ["tdx_industry_l3", "tdx_industry_l2", "tdx_industry_l1"]


def test_sqlite_store_records_sync_runs_and_freshness(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")

    run_id = store.start_sync_run("daily_kline", data_type="kline", params={"period": "d"})
    store.finish_sync_run(run_id, status="success", rows_written=3)
    store.update_data_freshness("kline", "daily", latest_time="2026-06-19", row_count=3)

    runs = store.get_sync_runs()
    freshness = store.get_data_freshness()

    assert runs.iloc[0]["job_name"] == "daily_kline"
    assert runs.iloc[0]["status"] == "success"
    assert freshness.iloc[0]["data_type"] == "kline"
    assert freshness.iloc[0]["latest_time"] == "2026-06-19"
