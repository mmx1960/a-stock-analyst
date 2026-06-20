from pathlib import Path

import pandas as pd

from app.core.providers.kaipanla_provider import KaipanlaProvider
from app.core.storage.duckdb_store import DuckDBStore


def test_kaipanla_normalize_limit_up_frames_and_cache_hot_map(tmp_path: Path):
    store = DuckDBStore(tmp_path / "test.duckdb")
    provider = KaipanlaProvider(store=store)
    payload = {
        "summary": {"trade_date": "2026-06-19"},
        "sectors": [
            {
                "sector_code": "801001",
                "sector_name": "机器人",
                "stock_count": 2,
                "stocks": [
                    {
                        "code": "000001",
                        "name": "测试A",
                        "limit_up_price": 10.0,
                        "turnover": 0.0,
                        "circulating_market_cap": 100,
                        "total_market_cap": 200,
                        "consecutive_days": 2,
                        "consecutive_count": 2,
                        "concept_tags": "机器人",
                        "theme": "机器人",
                        "reason": "测试原因",
                        "seal_amount": 120_000_000,
                        "main_net_inflow": 10_000_000,
                        "first_limit_up_time": "09:31:00",
                        "is_first_board": 0,
                        "raw": {"x": 1},
                    }
                ],
                "raw": {"sector": 1},
            }
        ],
    }

    sectors_df, stocks_df = provider.normalize_limit_up_frames(payload)
    assert len(sectors_df) == 1
    assert len(stocks_df) == 1
    assert stocks_df.iloc[0]["code"] == "000001"

    store.upsert_kaipanla_limit_up(sectors_df, stocks_df)
    cached = provider.get_cached_hot_stock_map("2026-06-19")
    assert "000001" in cached
    assert cached["000001"]["theme"] == "测试原因"
    assert cached["000001"]["stock_limit_up_days"] == 2
    assert cached["000001"]["theme_heat_score"] > 45


def test_kaipanla_market_sentiment_upsert_query(tmp_path: Path):
    store = DuckDBStore(tmp_path / "test.duckdb")
    provider = KaipanlaProvider(store=store)
    frame = provider.normalize_market_sentiment_frame(
        {
            "trade_date": "2026-06-19",
            "up_count": 3000,
            "down_count": 2000,
            "flat_count": 100,
            "limit_up_count": 80,
            "actual_limit_up_count": 70,
            "limit_down_count": 5,
            "actual_limit_down_count": 4,
            "rise_fall_ratio": 1.5,
            "yesterday_rise_fall_ratio": 1.2,
            "sh_index": 3100.5,
            "sh_change_pct": "1.2",
            "sh_amount": 500_000_000,
            "first_board_count": 40,
            "second_board_count": 8,
            "third_board_count": 3,
            "fourth_plus_board_count": 1,
            "consecutive_board_rate": 20.0,
            "sharp_withdrawal_count": 9,
            "source": "kaipanla",
            "raw_json": "{}",
        }
    )
    store.upsert_kaipanla_market_sentiment(frame)
    result = store.get_kaipanla_market_sentiment("2026-06-19", "2026-06-19")
    assert len(result) == 1
    assert int(result.iloc[0]["limit_up_count"]) == 80


def test_kaipanla_ladder_normalize(tmp_path: Path):
    store = DuckDBStore(tmp_path / "test.duckdb")
    provider = KaipanlaProvider(store=store)
    payload = {
        "date": "2026-06-19",
        "ladder": {2: [{"code": "000001", "name": "测试A", "tips": ""}]},
        "broken_stocks": [{"code": "000002", "name": "测试B", "tips": "3天2板", "consecutive_days": 2}],
        "height_marks": [],
    }
    frame = provider.normalize_ladder_frame(payload)
    assert len(frame) == 2
    assert set(frame["code"]) == {"000001", "000002"}
    store.upsert_kaipanla_limit_up_ladder(frame)
