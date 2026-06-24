from __future__ import annotations

import pandas as pd

from backtest.strategies import kaipanla_sector_strength_score as score_module
from backtest.strategies.kaipanla_sector_strength_score import (
    enrich_buy_points_with_sector_strength,
    expand_sector_aliases,
    infer_candidate_sectors,
    score_market_heat,
    score_plate_strength_and_capital,
    score_sector_strength_safe,
    score_sector_strength,
    summarize_strength_buckets,
)


def test_sector_aliases_expand_common_kaipanla_names() -> None:
    aliases = expand_sector_aliases({"机器人"})

    assert "机器人" in aliases
    assert "机器人概念" in aliases
    assert "未知板块" not in aliases


def test_sector_aliases_expand_sub_sectors_to_parent_themes() -> None:
    aliases = expand_sector_aliases({"人形机器人", "算力租赁", "存储芯片", "AI智能体"})

    assert "机器人概念" in aliases
    assert "算力" in aliases
    assert "芯片" in aliases
    assert "AI应用" in aliases


def test_infer_candidate_sectors_prefers_kaipanla_stock_pool() -> None:
    stock_window = pd.DataFrame([
        {"sector_name": "机器人", "theme": "人工智能"},
        {"sector_name": "机器人", "theme": None},
    ])
    membership = pd.DataFrame([
        {"sector_name": "半导体", "source": "ths_concept_page"},
    ])

    sectors = infer_candidate_sectors(stock_window, explicit_sector="算力", membership=membership)

    assert sectors == {"机器人", "人工智能"}


def test_infer_candidate_sectors_prefers_kaipanla_constituents_over_limit_up_history() -> None:
    stock_window = pd.DataFrame([
        {"sector_name": "其他", "theme": "涨停原因"},
    ])
    membership = pd.DataFrame([
        {"sector_name": "芯片", "source": "kaipanla_sector_constituents"},
        {"sector_name": "其他", "source": "kaipanla_limit_up_history"},
        {"sector_name": "半导体", "source": "ths_concept_page"},
    ])

    sectors = infer_candidate_sectors(stock_window, explicit_sector="算力", membership=membership)

    assert sectors == {"芯片"}


def test_infer_candidate_sectors_falls_back_to_ths_before_other_sources() -> None:
    membership = pd.DataFrame([
        {"sector_name": "半导体", "source": "ths_concept_page"},
        {"sector_name": "银行", "source": "cninfo_巨潮行业"},
    ])

    sectors = infer_candidate_sectors(pd.DataFrame(), explicit_sector="算力", membership=membership)

    assert sectors == {"半导体"}


def test_infer_candidate_sectors_falls_back_to_cninfo_when_ths_missing() -> None:
    membership = pd.DataFrame([
        {"sector_name": "银行", "source": "cninfo_巨潮行业"},
    ])

    sectors = infer_candidate_sectors(pd.DataFrame(), explicit_sector="算力", membership=membership)

    assert sectors == {"银行"}


def test_infer_candidate_sectors_prefers_tdx_deepest_industry_level() -> None:
    membership = pd.DataFrame([
        {"sector_name": "信息技术", "sector_type": "tdx_industry_l1", "source": "tdx"},
        {"sector_name": "软件服务", "sector_type": "tdx_industry_l2", "source": "tdx"},
        {"sector_name": "基础软件", "sector_type": "tdx_industry_l3", "source": "tdx"},
        {"sector_name": "AI智能体", "sector_type": "concept", "source": "ths_concept_page"},
    ])

    sectors = infer_candidate_sectors(pd.DataFrame(), explicit_sector="算力", membership=membership)

    assert sectors == {"基础软件"}


def test_infer_candidate_sectors_falls_back_to_tdx_l2_when_l3_missing() -> None:
    membership = pd.DataFrame([
        {"sector_name": "信息技术", "sector_type": "tdx_industry_l1", "source": "tdx"},
        {"sector_name": "软件服务", "sector_type": "tdx_industry_l2", "source": "tdx"},
    ])

    sectors = infer_candidate_sectors(pd.DataFrame(), membership=membership)

    assert sectors == {"软件服务"}


def test_score_sector_strength_uses_db_stock_sector_membership(monkeypatch) -> None:
    ranked = pd.DataFrame([
        {"trade_date": "2026-06-19", "sector_name": "半导体", "sector_rank": 1, "stock_count": 20},
    ])
    membership = pd.DataFrame([
        {"code": "688001", "sector_name": "半导体", "sector_type": "industry", "source": "akshare_em_industry"},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: ranked)
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: membership)
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: pd.DataFrame())

    result = score_sector_strength(code="688001", buy_date="2026-06-20")

    assert result["kaipanla_candidate_sectors"] == ["半导体"]
    assert result["stock_sector_membership_count"] == 1
    assert result["kaipanla_sector_appearances"] == 1
    assert result["kaipanla_strength_score"] > 0


def test_score_plate_strength_and_capital_uses_strength_capital_and_recency() -> None:
    plate_window = pd.DataFrame([
        {"trade_date": "2026-06-20", "sector_code": "801159", "sector_name": "机器人概念", "strength_score": 30000, "capital_score": 120, "main_net_inflow": 12_000_000_000, "turnover": 900_000_000_000, "limit_up_count": 3, "max_consecutive_days": 2},
        {"trade_date": "2026-06-19", "sector_code": "801159", "sector_name": "机器人概念", "strength_score": 10000, "capital_score": 50, "main_net_inflow": 5_000_000_000, "turnover": 600_000_000_000, "limit_up_count": 1, "max_consecutive_days": 1},
        {"trade_date": "2026-06-20", "sector_code": "801807", "sector_name": "算力", "strength_score": 5000, "capital_score": 10, "main_net_inflow": 1_000_000_000, "turnover": 200_000_000_000, "limit_up_count": 0, "max_consecutive_days": 0},
    ])

    result = score_plate_strength_and_capital(
        plate_window=plate_window,
        candidate_sectors={"机器人概念"},
        lookback_trade_days=10,
    )

    assert result is not None
    assert 35 < result["kaipanla_strength_score"] < 95
    assert result["kaipanla_strength_grade"] in {"C", "B", "A"}
    assert result["kaipanla_plate_strength_days"] == 2
    assert result["kaipanla_best_plate_strength"] == 30000
    assert result["kaipanla_best_capital_score"] == 120
    assert result["kaipanla_score_breakdown"]["reason"] == "matched_kaipanla_plate_strength_and_capital"


def test_score_plate_strength_matches_sector_alias() -> None:
    plate_window = pd.DataFrame([
        {"trade_date": "2026-06-20", "sector_code": "801159", "sector_name": "机器人概念", "strength_score": 30000, "capital_score": 120, "main_net_inflow": 12_000_000_000, "turnover": 900_000_000_000, "limit_up_count": 3, "max_consecutive_days": 2},
    ])

    result = score_plate_strength_and_capital(
        plate_window=plate_window,
        candidate_sectors={"机器人"},
        lookback_trade_days=10,
    )

    assert result is not None
    assert result["kaipanla_plate_strength_days"] == 1
    assert result["kaipanla_matched_plate_strength_rows"][0]["sector_name"] == "机器人概念"


def test_score_sector_strength_prefers_plate_strength_capital_over_legacy_rank(monkeypatch) -> None:
    membership = pd.DataFrame([
        {"code": "300001", "sector_name": "机器人概念", "sector_type": "concept", "source": "ths_concept_page"},
    ])
    plate_window = pd.DataFrame([
        {"trade_date": "2026-06-20", "sector_code": "801159", "sector_name": "机器人概念", "strength_score": 40000, "capital_score": 180, "main_net_inflow": 18_000_000_000, "turnover": 1_000_000_000_000, "limit_up_count": 4, "max_consecutive_days": 2},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: membership)
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: plate_window)

    result = score_sector_strength(code="300001", buy_date="2026-06-20")

    assert 45 < result["kaipanla_strength_score"] < 95
    assert result["kaipanla_candidate_sectors"] == ["机器人概念"]
    assert result["kaipanla_score_breakdown"]["reason"] == "matched_kaipanla_plate_strength_and_capital"
    assert result["kaipanla_available_trade_days"] == 1


def test_score_sector_strength_uses_kaipanla_constituents_before_limit_up_history(monkeypatch) -> None:
    membership = pd.DataFrame([
        {"code": "300001", "sector_name": "芯片", "sector_type": "kaipanla_sector", "source": "kaipanla_sector_constituents"},
        {"code": "300001", "sector_name": "其他", "sector_type": "hotspot", "source": "kaipanla_limit_up_history"},
    ])
    stock_window = pd.DataFrame([
        {"trade_date": "2026-06-18", "sector_name": "其他", "theme": "涨停原因", "consecutive_days": 1},
    ])
    plate_window = pd.DataFrame([
        {"trade_date": "2026-06-18", "sector_code": "801001", "sector_name": "芯片", "strength_score": 10726, "capital_score": 50.92, "main_net_inflow": 5_092_000_000, "turnover": 1_708_572_000_000, "limit_up_count": 0, "max_consecutive_days": 0},
        {"trade_date": "2026-06-18", "sector_code": "0", "sector_name": "其他", "strength_score": 1, "capital_score": 0, "main_net_inflow": 0, "turnover": 1, "limit_up_count": 0, "max_consecutive_days": 0},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: stock_window)
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: membership)
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: plate_window)

    result = score_sector_strength(code="300001", buy_date="2026-06-18")

    assert result["kaipanla_candidate_sectors"] == ["芯片"]
    assert result["kaipanla_matched_plate_strength_rows"][0]["sector_name"] == "芯片"
    assert result["kaipanla_score_breakdown"]["reason"] == "matched_kaipanla_plate_strength_and_capital"


def test_score_sector_strength_scores_matched_sector(monkeypatch) -> None:
    ranked = pd.DataFrame([
        {"trade_date": "2026-06-19", "sector_name": "机器人", "sector_rank": 1, "stock_count": 12},
        {"trade_date": "2026-06-18", "sector_name": "机器人", "sector_rank": 2, "stock_count": 8},
        {"trade_date": "2026-06-17", "sector_name": "消费", "sector_rank": 1, "stock_count": 15},
    ])
    stock_window = pd.DataFrame([
        {"trade_date": "2026-06-19", "sector_name": "机器人", "theme": "机器人", "consecutive_days": 2},
        {"trade_date": "2026-06-18", "sector_name": "机器人", "theme": "机器人", "consecutive_days": 1},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: ranked)
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: stock_window)
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: pd.DataFrame())

    result = score_sector_strength(code="1", buy_date="2026-06-20", lookback_trade_days=10)

    assert result["kaipanla_strength_score"] <= 45
    assert result["kaipanla_strength_grade"] == "C"
    assert result["kaipanla_candidate_sectors"] == ["机器人"]
    assert result["kaipanla_sector_appearances"] == 2
    assert result["kaipanla_best_sector_rank"] == 1
    assert result["kaipanla_stock_match_days"] == 2


def test_score_market_heat_builds_positive_score() -> None:
    market = pd.DataFrame([
        {"actual_limit_up_count": 120, "limit_up_count": 130, "first_board_count": 80, "second_board_count": 10, "third_board_count": 4, "fourth_plus_board_count": 2, "recency_rank": 1},
    ])

    result = score_market_heat(market)

    assert result["kaipanla_market_heat_score"] > 0
    assert result["kaipanla_market_heat_days"] == 1
    assert result["kaipanla_score_breakdown"]["market_heat_score"] == result["kaipanla_market_heat_score"]


def test_score_sector_strength_uses_market_heat_without_sector_history(monkeypatch) -> None:
    market = pd.DataFrame([
        {"trade_date": "2026-06-19", "actual_limit_up_count": 100, "limit_up_count": 110, "first_board_count": 70, "second_board_count": 8, "third_board_count": 4, "fourth_plus_board_count": 1, "recency_rank": 1},
        {"trade_date": "2026-06-18", "actual_limit_up_count": 60, "limit_up_count": 70, "first_board_count": 40, "second_board_count": 3, "third_board_count": 1, "fourth_plus_board_count": 0, "recency_rank": 2},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: market)

    result = score_sector_strength(code="1", buy_date="2026-06-20")

    assert result["kaipanla_strength_score"] > 0
    assert result["kaipanla_strength_grade"] == "MARKET_ONLY"
    assert result["kaipanla_max_limit_up_days"] == 0
    assert result["kaipanla_market_heat_days"] == 2
    assert result["kaipanla_score_breakdown"]["reason"] == "missing_candidate_sector_or_strength_history"


def test_score_sector_strength_safe_returns_error_metadata(monkeypatch) -> None:
    monkeypatch.setattr(score_module, "score_sector_strength", lambda **_: (_ for _ in ()).throw(RuntimeError("duckdb locked")))

    result = score_sector_strength_safe(code="1", buy_date="2026-06-20", lookback_trade_days=5)

    assert result["kaipanla_strength_score"] == 0.0
    assert result["kaipanla_strength_grade"] == "ERROR"
    assert result["kaipanla_lookback_days"] == 5
    assert result["kaipanla_score_breakdown"]["reason"] == "sector_strength_scoring_error"


def test_enrich_buy_points_with_sector_strength(monkeypatch) -> None:
    monkeypatch.setattr(score_module, "score_sector_strength", lambda **_: {"kaipanla_strength_score": 88.0})
    report = {
        "detailed": [
            {"code": "1", "name": "平安银行", "buy_points": [{"buy_date": "2026-06-20", "max_return": 12.3}]}
        ]
    }

    rows = enrich_buy_points_with_sector_strength(report)

    assert rows == [
        {
            "code": "000001",
            "name": "平安银行",
            "buy_date": "2026-06-20",
            "max_return": 12.3,
            "kaipanla_strength_score": 88.0,
        }
    ]


def test_summarize_strength_buckets_builds_recommendations() -> None:
    rows = [
        {"kaipanla_strength_score": 80, "max_return": 20, "status": "已实现"},
        {"kaipanla_strength_score": 60, "max_return": 12, "status": "已实现"},
        {"kaipanla_strength_score": 30, "max_return": -5, "status": "已实现"},
        {"kaipanla_strength_score": 0, "max_return": 2, "status": "已实现"},
    ]

    summary = summarize_strength_buckets(rows)

    buckets = {row["bucket"]: row for row in summary["bucket_summary"]}
    assert buckets["A_75_100"]["avg_return"] == 20.0
    assert buckets["B_55_74"]["win_rate_10pct"] == 100.0
    assert summary["recommended_filters"][0]["min_score"] == 35
    assert summary["recommended_filters"][-1]["min_score"] == 75

def test_infer_candidate_sectors_uses_kaipanla_history_membership_fallback() -> None:
    stock_window = pd.DataFrame([{"sector_name": "未知板块", "theme": "local_daily_kline_limit_up"}])
    membership = pd.DataFrame([
        {"sector_name": "未知板块", "source": "kaipanla_limit_up_history"},
        {"sector_name": "抽水蓄能", "source": "kaipanla_limit_up_history"},
    ])

    sectors = infer_candidate_sectors(stock_window, explicit_sector="杰哥龙头低吸", membership=membership)

    assert sectors == {"抽水蓄能"}


def test_infer_candidate_sectors_keeps_confirmed_membership_source() -> None:
    membership = pd.DataFrame([{"sector_name": "抽水蓄能", "source": "ths_concept_page"}])

    sectors = infer_candidate_sectors(pd.DataFrame(), membership=membership)

    assert sectors == {"抽水蓄能"}

def test_legacy_rank_fallback_is_capped_without_plate_strength(monkeypatch) -> None:
    membership = pd.DataFrame([{"code": "603679", "sector_name": "AI智能体", "source": "ths_concept_page"}])
    ranked = pd.DataFrame([
        {"trade_date": "2026-06-17", "sector_name": "AI智能体", "sector_rank": 4, "stock_count": 3},
        {"trade_date": "2026-06-16", "sector_name": "AI智能体", "sector_rank": 5, "stock_count": 3},
        {"trade_date": "2026-06-15", "sector_name": "AI智能体", "sector_rank": 6, "stock_count": 3},
        {"trade_date": "2026-06-14", "sector_name": "AI智能体", "sector_rank": 7, "stock_count": 3},
        {"trade_date": "2026-06-13", "sector_name": "AI智能体", "sector_rank": 8, "stock_count": 3},
        {"trade_date": "2026-06-12", "sector_name": "AI智能体", "sector_rank": 9, "stock_count": 3},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: ranked)
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: membership)
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())

    result = score_sector_strength(code="603679", buy_date="2026-06-18")

    assert result["kaipanla_strength_score"] <= 45
    assert result["kaipanla_strength_grade"] == "C"
    assert result["kaipanla_score_breakdown"]["reason"] == "legacy_limit_up_rank_fallback_capped_without_plate_strength"


def test_legacy_rank_fallback_ignores_weak_single_stock_or_deep_rank(monkeypatch) -> None:
    membership = pd.DataFrame([{"code": "603679", "sector_name": "AI智能体", "source": "ths_concept_page"}])
    ranked = pd.DataFrame([
        {"trade_date": "2026-06-17", "sector_name": "AI智能体", "sector_rank": 4, "stock_count": 1},
        {"trade_date": "2026-06-16", "sector_name": "AI智能体", "sector_rank": 80, "stock_count": 5},
    ])
    monkeypatch.setattr(score_module, "load_kaipanla_plate_strength_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_kaipanla_strength_window", lambda **_: ranked)
    monkeypatch.setattr(score_module, "load_kaipanla_stock_sector_window", lambda **_: pd.DataFrame())
    monkeypatch.setattr(score_module, "load_stock_sector_membership", lambda **_: membership)
    monkeypatch.setattr(score_module, "load_kaipanla_market_heat_window", lambda **_: pd.DataFrame())

    result = score_sector_strength(code="603679", buy_date="2026-06-18")

    assert result["kaipanla_sector_appearances"] == 0
    assert result["kaipanla_strength_score"] == 0

