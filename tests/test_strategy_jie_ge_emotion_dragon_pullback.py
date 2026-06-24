from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from backtest.strategies.registry import get_strategy
from backtest.strategies.strategy_jie_ge_emotion_dragon_pullback import (
    analyze_jie_ge_emotion_dragon_pullback,
    check_jie_ge_dragon_context,
    check_jie_ge_high_position_second_wave_exclusion,
)


def _daily_frame(rows: int = 120) -> pd.DataFrame:
    base = datetime(2025, 1, 1)
    data = []
    price = 10.0
    for idx in range(rows):
        if idx < 90:
            price *= 1.002
        elif idx < 108:
            price *= 1.018
        else:
            price *= 0.992
        open_price = price * 0.99
        close = price
        high = price * 1.03
        low = price * 0.97
        data.append(
            {
                "trade_date": base + timedelta(days=idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 10_000_000 + idx * 10_000,
                "amount": 120_000_000,
            }
        )
    return pd.DataFrame(data)


def _minute_frame(rows: int = 80) -> pd.DataFrame:
    base = datetime(2025, 5, 1, 9, 30)
    data = []
    price = 20.0
    for idx in range(rows):
        if idx < rows - 3:
            price *= 1.001
            open_price = price * 0.998
            close = price
            high = close * 1.004
            low = close * 0.996
        elif idx == rows - 3:
            price *= 0.985
            open_price = price * 1.004
            close = price
            high = open_price * 1.002
            low = close * 0.992
        elif idx == rows - 2:
            price *= 0.996
            open_price = price * 1.002
            close = price
            high = open_price * 1.002
            low = close * 0.993
        else:
            price *= 1.014
            open_price = price * 0.992
            close = price
            high = close * 1.005
            low = close * 0.985
        data.append(
            {
                "trade_dt": base + timedelta(minutes=30 * idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000,
                "amount": 20_000_000,
            }
        )
    return pd.DataFrame(data)



def _high_position_second_wave_frame() -> pd.DataFrame:
    base = datetime(2025, 1, 1)
    data = []
    price = 10.0
    for idx in range(120):
        if idx < 70:
            price *= 1.001
        elif idx < 95:
            price *= 1.055
        else:
            price *= 0.992
        data.append(
            {
                "trade_date": base + timedelta(days=idx),
                "open": price * 0.99,
                "high": price * 1.03,
                "low": price * 0.97,
                "close": price,
                "volume": 20_000_000,
                "amount": 160_000_000,
            }
        )
    return pd.DataFrame(data)


def test_jie_ge_second_wave_filter_accepts_clean_pullback():
    ok, meta = check_jie_ge_high_position_second_wave_exclusion(_daily_frame())
    assert ok
    assert meta["jie_ge_second_wave_reason"] == "passed_translation_test"


def test_jie_ge_second_wave_filter_rejects_high_position_extreme_sample():
    ok, meta = check_jie_ge_high_position_second_wave_exclusion(_high_position_second_wave_frame())
    assert not ok
    assert meta["jie_ge_second_wave_reason"] == "high_position_second_wave_excluded"


def test_jie_ge_context_accepts_core_pullback():
    ok, meta = check_jie_ge_dragon_context(_daily_frame())
    assert ok
    assert meta["jie_ge_core_score"] > 60
    assert "资金态度" in meta["jie_ge_tags"]


def test_jie_ge_strategy_detects_30m_rebound():
    signal = analyze_jie_ge_emotion_dragon_pullback(_daily_frame(), _minute_frame(), now=datetime(2025, 5, 3))
    assert signal is not None
    assert signal["signal_reason"] == "jie_ge_emotion_dragon_pullback"
    assert signal["theme"] == "杰哥龙头低吸"
    assert signal["jie_ge_trade_plan"]["risk"]


def test_registry_exposes_jie_ge_strategy():
    strategy = get_strategy("jie_ge_emotion_dragon_pullback")
    assert strategy.strategy_name == "杰哥情绪龙头低吸"

def test_jie_ge_distribution_risk_rejects_high_volume_upper_shadow() -> None:
    from scripts.run_jie_ge_h1_vector_topn_backtest import check_recent_distribution_risk

    daily = _daily_frame()
    idx = len(daily) - 1
    daily.loc[idx, "open"] = 10.0
    daily.loc[idx, "high"] = 12.0
    daily.loc[idx, "low"] = 9.8
    daily.loc[idx, "close"] = 10.2
    daily.loc[idx, "volume"] = daily["volume"].tail(5).mean() * 5

    ok, meta = check_recent_distribution_risk(daily)

    assert not ok
    assert meta["jie_ge_distribution_risk_reason"] == "high_volume_upper_shadow"

