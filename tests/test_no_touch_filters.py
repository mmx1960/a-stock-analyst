from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from backtest.filters.no_touch_filters import (
    check_high_turnover_before_buy,
    check_multiple_one_word_boards,
    check_top_bearish_high_turnover,
)


def _base_daily(rows: int = 80) -> pd.DataFrame:
    base = datetime(2025, 1, 1)
    data = []
    price = 10.0
    for idx in range(rows):
        price *= 1.002
        data.append({
            "trade_date": base + timedelta(days=idx),
            "open": price * 0.99,
            "high": price * 1.02,
            "low": price * 0.98,
            "close": price,
            "volume": 1_000_000,
            "turnover_rate": 5.0,
            "change_pct": 0.2,
        })
    return pd.DataFrame(data)


def test_one_word_board_rejects_any_board():
    daily = _base_daily()
    idx = 72
    close = daily.loc[idx, "close"] * 1.1
    daily.loc[idx, ["open", "high", "low", "close"]] = close
    daily.loc[idx, "change_pct"] = 10.0
    ok, meta = check_multiple_one_word_boards(daily)
    assert not ok
    assert meta["no_touch_one_word_board_count"] == 1
    assert meta["no_touch_one_word_lookback_trade_days"] == 10


def test_multiple_one_word_boards_ignores_boards_before_10_trade_days():
    daily = _base_daily()
    for idx in [60, 62]:
        close = daily.loc[idx, "close"] * 1.1
        daily.loc[idx, ["open", "high", "low", "close"]] = close
        daily.loc[idx, "change_pct"] = 10.0
    ok, meta = check_multiple_one_word_boards(daily)
    assert ok
    assert meta["no_touch_one_word_board_count"] == 0


def test_top_bearish_high_turnover_rejects_heavy_distribution():
    daily = _base_daily()
    idx = len(daily) - 3
    daily.loc[idx, "open"] = 30.0
    daily.loc[idx, "close"] = 26.0
    daily.loc[idx, "high"] = 31.0
    daily.loc[idx, "low"] = 25.0
    daily.loc[idx, "volume"] = 5_000_000
    daily.loc[idx, "turnover_rate"] = 45.0
    ok, meta = check_top_bearish_high_turnover(daily)
    assert not ok
    assert meta["no_touch_top_bearish_found"] is True
    assert meta["no_touch_top_bearish_lookback_trade_days"] == 10


def test_top_bearish_high_turnover_ignores_distribution_before_10_trade_days():
    daily = _base_daily()
    idx = len(daily) - 20
    daily.loc[idx, "open"] = 30.0
    daily.loc[idx, "close"] = 26.0
    daily.loc[idx, "high"] = 31.0
    daily.loc[idx, "low"] = 25.0
    daily.loc[idx, "volume"] = 5_000_000
    daily.loc[idx, "turnover_rate"] = 45.0
    ok, meta = check_top_bearish_high_turnover(daily)
    assert ok
    assert meta["no_touch_top_bearish_found"] is False


def test_high_turnover_before_buy_rejects_any_40pct_turnover_in_10_trade_days():
    daily = _base_daily()
    idx = len(daily) - 2
    daily.loc[idx, "turnover_rate"] = 42.0
    ok, meta = check_high_turnover_before_buy(daily)
    assert not ok
    assert meta["no_touch_high_turnover_found"] is True
    assert meta["no_touch_high_turnover_rate"] == 42.0


def test_high_turnover_before_buy_ignores_40pct_turnover_before_10_trade_days():
    daily = _base_daily()
    idx = len(daily) - 20
    daily.loc[idx, "turnover_rate"] = 42.0
    ok, meta = check_high_turnover_before_buy(daily)
    assert ok
    assert meta["no_touch_high_turnover_found"] is False
