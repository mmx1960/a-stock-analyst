from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider
from backtest.strategies.registry import get_strategy, list_strategies
from backtest.strategies.strategy_d_shen_trend_pullback import (
    analyze_d_shen_trend_30m_pullback,
    check_d_shen_daily_context,
    classify_d_shen_technical_pool_position,
    detect_d_shen_30m_pullback,
    is_risky_stock_name,
    scan_d_shen_trend_30m_pullback,
)


def _d_shen_daily_frame() -> pd.DataFrame:
    start = datetime(2025, 1, 1)
    rows = []
    price = 10.0
    for i in range(180):
        price *= 1.0025
        rows.append(
            {
                "trade_date": start + timedelta(days=i),
                "open": price * 0.992,
                "high": price * 1.018,
                "low": price * 0.988,
                "close": price,
                "volume": 1_000_000 + i * 1200,
                "amount": 80_000_000 + i * 50_000,
            }
        )
    return pd.DataFrame(rows)


def _d_shen_30m_frame() -> pd.DataFrame:
    start = datetime(2026, 6, 18, 9, 30)
    rows = []
    price = 20.0
    for i in range(55):
        price *= 1.0006
        rows.append(
            {
                "trade_dt": start + timedelta(minutes=30 * i),
                "open": price * 0.999,
                "high": price * 1.004,
                "low": price * 0.997,
                "close": price,
                "volume": 800_000 + i * 500,
            }
        )
    frame = pd.DataFrame(rows)
    ma13_series = pd.Series(frame["close"]).rolling(13).mean().tolist()
    ma13 = float(ma13_series[-2])
    frame.loc[frame.index[-1], "open"] = ma13 * 0.998
    frame.loc[frame.index[-1], "low"] = ma13 * 0.992
    frame.loc[frame.index[-1], "close"] = ma13 * 1.008
    frame.loc[frame.index[-1], "high"] = ma13 * 1.012
    return frame


def test_d_shen_technical_pool_position_classification() -> None:
    attack_15 = classify_d_shen_technical_pool_position(15)
    attack_30 = classify_d_shen_technical_pool_position("30")
    defense_60 = classify_d_shen_technical_pool_position(60)
    defense_120 = classify_d_shen_technical_pool_position("120")
    unknown = classify_d_shen_technical_pool_position("#N/A")

    assert attack_15["d_shen_technical_pool_type"] == "attack"
    assert attack_30["d_shen_technical_pool_label"] == "进攻标的"
    assert defense_60["d_shen_technical_pool_type"] == "defense"
    assert defense_120["preferred_periods"] == ["60", "120"]
    assert unknown["d_shen_technical_pool_type"] == "unknown"


def test_d_shen_daily_context_requires_multi_ma_uptrend() -> None:
    ok, meta = check_d_shen_daily_context(_d_shen_daily_frame())

    assert ok is True
    assert meta["d_shen_context_reason"] == "d_shen_daily_context_ok"
    assert meta["daily_alignment_ok"] is True
    assert meta["daily_ma20_slope_ok"] is True
    assert meta["d_shen_daily_context_score"] >= 62


def test_d_shen_30m_pullback_detects_ma_convergence_recover() -> None:
    signal = detect_d_shen_30m_pullback(_d_shen_30m_frame(), now=datetime(2026, 6, 18, 15, 0))

    assert signal is not None
    assert signal["signal_reason"] == "d_shen_trend_30m_pullback"
    assert signal["structure_period"] == "30"
    assert signal["ma_convergence_score"] >= 45
    assert signal["bullish_recover"] is True


def test_d_shen_strategy_combines_context_and_pullback() -> None:
    signal = analyze_d_shen_trend_30m_pullback(
        _d_shen_daily_frame(),
        _d_shen_30m_frame(),
        now=datetime(2026, 6, 18, 15, 0),
    )

    assert signal is not None
    assert signal["theme"] == "D神趋势回踩"
    assert signal["strategy_version"] == "d-shen-trend-30m-pullback-v1"
    assert "资金一致性不会假" in signal["strategy_metadata"]["d_shen_principles"]


def test_strategy_registry_exposes_d_shen_strategy() -> None:
    ids = {strategy.strategy_id for strategy in list_strategies()}

    assert "d_shen_trend_30m_pullback" in ids
    assert "D神" in get_strategy("d_shen_trend_30m_pullback").strategy_name


def test_d_shen_filters_risky_stock_names() -> None:
    assert is_risky_stock_name("*ST兰黄") is True
    assert is_risky_stock_name("退市某股") is True
    assert is_risky_stock_name("滨海能源") is False


def test_d_shen_daily_context_rejects_low_liquidity_when_amount_available() -> None:
    frame = _d_shen_daily_frame()
    frame["amount"] = 10_000_000

    ok, meta = check_d_shen_daily_context(frame)

    assert ok is False
    assert meta["daily_liquidity_ok"] is False


def test_d_shen_daily_context_rejects_overextended_from_ma20() -> None:
    frame = _d_shen_daily_frame()
    ma20_latest = float(list(frame["close"].rolling(20).mean())[-1])
    frame.loc[frame.index[-1], "close"] = ma20_latest * 1.16
    frame.loc[frame.index[-1], "high"] = frame.loc[frame.index[-1], "close"] * 1.01
    frame.loc[frame.index[-1], "low"] = frame.loc[frame.index[-1], "close"] * 0.99

    ok, meta = check_d_shen_daily_context(frame)

    assert ok is False
    assert meta["daily_not_overextended_ok"] is False


def test_real_db_provider_scan_runs_without_mocks() -> None:
    provider = DuckDBMarketDataProvider()
    results = scan_d_shen_trend_30m_pullback(
        provider=provider,
        max_stocks=3,
        signal_window_days=400,
        throttle_seconds=0,
    )

    assert isinstance(results, list)
    for item in results:
        assert item["strategy_version"] == "d-shen-trend-30m-pullback-v1"
        assert item["code"]
        assert item["signal_price"] > 0
