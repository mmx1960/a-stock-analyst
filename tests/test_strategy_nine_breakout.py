from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider
from backtest.strategies.registry import get_strategy, list_strategies
from backtest.strategies.strategy_nine_breakout import (
    analyze_strategy_nine_breakout,
    check_half_year_uptrend_high_breakout,
    scan_strategy_nine_breakout,
)


def _near_prior_high_frame(*, breakout: bool = False, weak_trend: bool = False) -> pd.DataFrame:
    start = datetime(2025, 12, 1)
    rows = []
    price = 10.0
    for i in range(130):
        if weak_trend:
            price *= 0.998
        else:
            price *= 1.006
        high = price * 1.015
        low = price * 0.985
        if i == 108:
            high = price * 1.12
        rows.append(
            {
                "trade_date": start + timedelta(days=i),
                "open": price * 0.995,
                "high": high,
                "low": low,
                "close": price,
                "volume": 1_000_000 + i * 2000,
                "amount": 80_000_000 + i * 100_000,
            }
        )
    frame = pd.DataFrame(rows)
    prior_high = float(frame.iloc[:-1]["high"].max())
    if breakout:
        frame.loc[frame.index[-1], "open"] = prior_high * 0.995
        frame.loc[frame.index[-1], "close"] = prior_high * 1.02
        frame.loc[frame.index[-1], "high"] = prior_high * 1.035
        frame.loc[frame.index[-1], "low"] = prior_high * 0.99
    else:
        frame.loc[frame.index[-1], "open"] = prior_high * 0.965
        frame.loc[frame.index[-1], "close"] = prior_high * 0.975
        frame.loc[frame.index[-1], "high"] = prior_high * 0.992
        frame.loc[frame.index[-1], "low"] = prior_high * 0.958
    return frame


def test_strategy_nine_accepts_near_prior_high_in_half_year_uptrend() -> None:
    ok, meta = check_half_year_uptrend_high_breakout(_near_prior_high_frame())

    assert ok is True
    assert meta["nine_reason"] == "nine_breakout_ok"
    assert meta["near_prior_high_breakout"] is True
    assert meta["fresh_prior_high_breakout"] is False
    assert -5 <= meta["distance_to_prior_high_pct"] < 0


def test_strategy_nine_accepts_fresh_prior_high_breakout() -> None:
    signal = analyze_strategy_nine_breakout(_near_prior_high_frame(breakout=True), now=datetime(2026, 6, 18))

    assert signal is not None
    assert signal["signal_reason"] == "strategy_nine_strong_sector_prior_high_breakout"
    assert signal["fresh_prior_high_breakout"] is True
    assert signal["structure_period"] == "daily"
    assert signal["signal_score"] >= 70


def test_strategy_nine_rejects_non_uptrend_near_high() -> None:
    ok, meta = check_half_year_uptrend_high_breakout(_near_prior_high_frame(weak_trend=True))

    assert ok is False
    assert meta["nine_reason"] == "nine_breakout_failed"
    assert meta["daily_ma_alignment_ok"] is False or meta["half_year_return_pct"] < 18


def test_strategy_registry_exposes_strategy_nine() -> None:
    ids = {strategy.strategy_id for strategy in list_strategies()}

    assert "strategy_nine_breakout" in ids
    assert get_strategy("strategy_nine_breakout").strategy_name.startswith("九号策略")


def test_real_db_provider_scan_strategy_nine_runs_without_mocks() -> None:
    provider = DuckDBMarketDataProvider()
    results = scan_strategy_nine_breakout(
        provider=provider,
        max_stocks=3,
        signal_window_days=400,
        throttle_seconds=0,
    )

    assert isinstance(results, list)
    for item in results:
        assert item["strategy_version"] == "strategy-nine-strong-sector-prior-high-breakout-v1"
        assert item["code"]
        assert item["signal_price"] > 0
