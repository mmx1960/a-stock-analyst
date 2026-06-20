import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.archive.backtest_v6 import check_weekly_trend


class _FakeBar:
    def __init__(self, dt, open_, high, low, close, vol=1000):
        self.dt = dt
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.vol = vol


class _FakeFX:
    def __init__(self, fx):
        self.fx = fx


class _FakeBI:
    def __init__(self, direction, end_fx):
        self.direction = direction
        self.fx_b = _FakeFX(end_fx)


class _FakeCZSC:
    def __init__(self, closes, highs, up_ends):
        dates = pd.date_range('2024-01-05', periods=len(closes), freq='W-FRI')
        self.bars_raw = [
            _FakeBar(dt, c, h, c - 0.5, c) for dt, c, h in zip(dates, closes, highs)
        ]
        self.bi_list = [
            _FakeBI('向下', closes[3]),
            _FakeBI('向上', up_ends[0]),
            _FakeBI('向下', closes[-4]),
            _FakeBI('向上', up_ends[1]),
        ]



def test_full_runner_weekly_trend_accepts_when_half_context_below_current_and_pre_pullback_is_highest():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 12.2, 13.2, 13.8, 14.2, 14.5, 15.0, 15.5, 15.8, 18.0, 19.1, 18.1, 17.1]
    czsc = _FakeCZSC(closes, highs, up_ends=[15.0, 16.0])
    assert check_weekly_trend(czsc) is True



def test_full_runner_weekly_trend_rejects_long_pullback():
    closes = list(range(10, 18)) + [16, 15, 14, 13, 12, 11, 10]
    highs = [c + 0.2 for c in closes]
    czsc = _FakeCZSC(closes, highs, up_ends=[16.0, 17.0])
    assert check_weekly_trend(czsc) is False



def test_full_runner_weekly_trend_rejects_when_less_than_half_of_context_is_below_current():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 17.5, 17.6, 17.7, 17.8, 17.9, 18.0, 18.1, 18.2, 18.3, 19.1, 18.1, 17.1]
    czsc = _FakeCZSC(closes, highs, up_ends=[15.0, 16.0])
    assert check_weekly_trend(czsc) is False



def test_full_runner_weekly_trend_rejects_when_pre_pullback_high_is_not_context_highest():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 19.0, 13.2, 13.8, 14.2, 14.5, 15.0, 15.5, 15.8, 18.0, 19.1, 18.1, 17.1]
    czsc = _FakeCZSC(closes, highs, up_ends=[15.0, 16.0])
    assert check_weekly_trend(czsc) is False
