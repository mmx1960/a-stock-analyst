from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider
from backtest.strategies.registry import get_strategy, list_strategies
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import (
    analyze_daily_uptrend_30m_ma13_pullback,
    check_daily_uptrend,
    detect_30m_ma13_pullback,
    scan_daily_uptrend_30m_ma13_pullback,
)


def _daily_uptrend_frame() -> pd.DataFrame:
    start = datetime(2025, 1, 1)
    rows = []
    price = 10.0
    for i in range(100):
        price *= 1.004
        rows.append(
            {
                "trade_date": start + timedelta(days=i),
                "open": price * 0.99,
                "high": price * 1.02,
                "low": price * 0.985,
                "close": price,
                "volume": 1_000_000 + i * 1000,
            }
        )
    return pd.DataFrame(rows)


def _minute_pullback_frame() -> pd.DataFrame:
    start = datetime(2026, 6, 18, 9, 30)
    rows = []
    price = 20.0
    for i in range(45):
        price *= 1.001
        rows.append(
            {
                "trade_dt": start + timedelta(minutes=30 * i),
                "open": price * 0.998,
                "high": price * 1.006,
                "low": price * 0.996,
                "close": price,
                "volume": 800_000 + i * 500,
            }
        )
    frame = pd.DataFrame(rows)
    ma13_series = pd.Series(frame["close"]).rolling(13).mean()
    ma13 = float(ma13_series.iloc[-2])
    frame.loc[frame.index[-1], "open"] = ma13 * 0.998
    frame.loc[frame.index[-1], "low"] = ma13 * 0.992
    frame.loc[frame.index[-1], "close"] = ma13 * 1.01
    frame.loc[frame.index[-1], "high"] = ma13 * 1.015
    return frame


def test_daily_uptrend_check_passes_clear_uptrend() -> None:
    ok, meta = check_daily_uptrend(_daily_uptrend_frame())

    assert ok is True
    assert meta["daily_trend_reason"] == "daily_uptrend_ok"
    assert meta["daily_ma_alignment_ok"] is True
    assert meta["daily_trend_score"] >= 50


def test_30m_ma13_pullback_detects_touch_and_recover() -> None:
    signal = detect_30m_ma13_pullback(_minute_pullback_frame(), now=datetime(2026, 6, 18, 15, 0))

    assert signal is not None
    assert signal["signal_reason"] == "daily_uptrend_30m_ma13_pullback"
    assert signal["structure_period"] == "30"
    assert signal["ma13_slope_ok"] is True
    assert signal["bullish_close"] is True


def test_analyze_strategy_combines_daily_and_30m_conditions() -> None:
    signal = analyze_daily_uptrend_30m_ma13_pullback(
        _daily_uptrend_frame(),
        _minute_pullback_frame(),
        now=datetime(2026, 6, 18, 15, 0),
    )

    assert signal is not None
    assert signal["theme"] == "日线上涨趋势回踩"
    assert signal["theme_heat_score"] == 50.0
    assert signal["signal_score"] >= 50


def test_strategy_registry_exposes_daily_uptrend_ma13_pullback() -> None:
    ids = {strategy.strategy_id for strategy in list_strategies()}

    assert "daily_uptrend_30m_ma13_pullback" in ids
    assert get_strategy("daily_uptrend_30m_ma13_pullback").strategy_name.startswith("日线上涨趋势")


def test_real_db_provider_scan_runs_without_mocks() -> None:
    provider = DuckDBMarketDataProvider()
    results = scan_daily_uptrend_30m_ma13_pullback(
        provider=provider,
        max_stocks=3,
        signal_window_days=400,
        throttle_seconds=0,
    )

    assert isinstance(results, list)
    for item in results:
        assert item["strategy_version"] == "daily-uptrend-30m-ma13-pullback-v1"
        assert item["code"]
        assert item["signal_price"] > 0
