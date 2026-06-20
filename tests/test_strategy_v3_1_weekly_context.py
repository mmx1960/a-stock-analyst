import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.strategies.strategy_v3_1_realtime import (
    check_bullish_volume_retrace_pattern,
    check_current_price_vs_historical_high,
    check_current_price_vs_ma250,
    check_daily_entry_confirmation,
    check_intraday_second_buy,
    check_weekly_slow_volume_build,
    check_weekly_uptrend_context,
)


def _weekly_df(closes, highs=None):
    if highs is None:
        highs = [c - 0.1 for c in closes]
    dates = pd.date_range('2024-01-05', periods=len(closes), freq='W-FRI')
    return pd.DataFrame({
        'date': dates,
        'open': closes,
        'high': highs,
        'low': [c - 0.5 for c in closes],
        'close': closes,
        'volume': [1000] * len(closes),
    })


def test_weekly_uptrend_context_accepts_when_half_context_below_current_and_pre_pullback_is_highest():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 12.2, 13.2, 13.8, 14.2, 14.5, 15.0, 15.5, 15.8, 18.0, 19.1, 18.1, 17.1]
    weekly = _weekly_df(closes, highs=highs)

    ok, meta = check_weekly_uptrend_context(weekly)

    assert ok is True
    assert meta['weekly_pullback_bars'] == 3
    assert meta['weekly_context_window'] == 9
    assert meta['weekly_uptrend_reason'] == 'ok'
    assert meta['weekly_context_half_below_ratio'] >= 0.5
    assert meta['weekly_pre_pullback_high'] >= meta['weekly_context_high']


def test_weekly_uptrend_context_rejects_when_pullback_exceeds_6_bars():
    closes = list(range(10, 18)) + [16, 15, 14, 13, 12, 11, 10]
    weekly = _weekly_df(closes)

    ok, meta = check_weekly_uptrend_context(weekly)

    assert ok is False
    assert meta['weekly_pullback_bars'] == 7
    assert meta['weekly_uptrend_reason'] == 'pullback_too_long'


def test_weekly_uptrend_context_rejects_when_less_than_half_of_context_is_below_current():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 17.5, 17.6, 17.7, 17.8, 17.9, 18.0, 18.1, 18.2, 18.3, 19.1, 18.1, 17.1]
    weekly = _weekly_df(closes, highs=highs)

    ok, meta = check_weekly_uptrend_context(weekly)

    assert ok is False
    assert meta['weekly_uptrend_reason'] == 'prior_3x_window_less_than_half_below_current'
    assert meta['weekly_context_half_below_ratio'] < 0.5


def test_weekly_uptrend_context_rejects_when_pre_pullback_high_is_not_context_highest():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 19, 18, 17]
    highs =  [10.2, 11.2, 19.0, 13.2, 13.8, 14.2, 14.5, 15.0, 15.5, 15.8, 18.0, 19.1, 18.1, 17.1]
    weekly = _weekly_df(closes, highs=highs)

    ok, meta = check_weekly_uptrend_context(weekly)

    assert ok is False
    assert meta['weekly_uptrend_reason'] == 'pre_pullback_high_not_context_highest'
    assert meta['weekly_pre_pullback_high'] < meta['weekly_context_high']


def _daily_df(rows):
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df


def test_daily_entry_confirmation_accepts_5bar_stop_fall_with_two_red_bars():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.2, 'high': 10.3, 'low': 9.9, 'close': 10.0, 'volume': 1000},
        {'date': '2026-01-02', 'open': 10.0, 'high': 10.05, 'low': 9.7, 'close': 9.8, 'volume': 1100},
        {'date': '2026-01-03', 'open': 9.82, 'high': 9.9, 'low': 9.72, 'close': 9.88, 'volume': 1200},
        {'date': '2026-01-04', 'open': 9.84, 'high': 9.9, 'low': 9.74, 'close': 9.8, 'volume': 1150},
        {'date': '2026-01-05', 'open': 9.81, 'high': 9.95, 'low': 9.75, 'close': 9.9, 'volume': 1300},
    ])

    ok, meta = check_daily_entry_confirmation(df, 4)

    assert ok is True
    assert meta['entry_reason'] == 'ok'
    assert meta['entry_score'] == 2
    assert meta['entry_score_max'] == 2
    assert meta['entry_score_breakdown'] == {
        'recent_5bars_no_new_low': 1,
        'recent_5bars_red_bar_count': 1,
    }
    assert meta['entry_recent_5bars_no_new_low_ok'] is True
    assert meta['entry_recent_5bars_red_bar_count'] >= 2


def test_daily_entry_confirmation_rejects_when_recent_5bars_make_new_low():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.2, 'high': 10.3, 'low': 9.9, 'close': 10.0, 'volume': 1000},
        {'date': '2026-01-02', 'open': 10.0, 'high': 10.05, 'low': 9.7, 'close': 9.8, 'volume': 1100},
        {'date': '2026-01-03', 'open': 9.82, 'high': 9.9, 'low': 9.72, 'close': 9.88, 'volume': 1200},
        {'date': '2026-01-04', 'open': 9.84, 'high': 9.9, 'low': 9.68, 'close': 9.8, 'volume': 1150},
        {'date': '2026-01-05', 'open': 9.81, 'high': 9.95, 'low': 9.66, 'close': 9.9, 'volume': 1300},
    ])

    ok, meta = check_daily_entry_confirmation(df, 4)

    assert ok is False
    assert meta['entry_score'] == 1
    assert 'entry_recent_5bars_made_new_low' in meta['entry_failed_checks']
    assert meta['entry_recent_5bars_no_new_low_ok'] is False


def test_daily_entry_confirmation_rejects_when_recent_5bars_have_less_than_two_red_bars():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.2, 'high': 10.3, 'low': 9.9, 'close': 10.0, 'volume': 1000},
        {'date': '2026-01-02', 'open': 10.0, 'high': 10.05, 'low': 9.7, 'close': 9.8, 'volume': 1100},
        {'date': '2026-01-03', 'open': 9.82, 'high': 9.86, 'low': 9.72, 'close': 9.79, 'volume': 1200},
        {'date': '2026-01-04', 'open': 9.84, 'high': 9.88, 'low': 9.74, 'close': 9.8, 'volume': 1150},
        {'date': '2026-01-05', 'open': 9.81, 'high': 9.9, 'low': 9.75, 'close': 9.78, 'volume': 1300},
    ])

    ok, meta = check_daily_entry_confirmation(df, 4)

    assert ok is False
    assert meta['entry_score'] == 1
    assert 'entry_recent_5bars_not_enough_red_bars' in meta['entry_failed_checks']
    assert meta['entry_recent_5bars_red_bar_count'] < 2


def test_current_price_vs_historical_high_accepts_when_not_below_one_third():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.0, 'high': 12.0, 'low': 9.8, 'close': 10.5, 'volume': 1000},
        {'date': '2026-01-02', 'open': 10.6, 'high': 18.0, 'low': 10.0, 'close': 11.0, 'volume': 1100},
        {'date': '2026-01-03', 'open': 6.0, 'high': 6.5, 'low': 5.8, 'close': 6.2, 'volume': 1200},
    ])

    ok, meta = check_current_price_vs_historical_high(df, 2)

    assert ok is True
    assert meta['historical_high_reason'] == 'ok'
    assert meta['historical_high_price'] == 18.0
    assert meta['buy_close_price'] == 6.2
    assert meta['current_to_historical_high_ratio'] >= 1 / 3


def test_current_price_vs_historical_high_rejects_when_below_one_third():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.0, 'high': 12.0, 'low': 9.8, 'close': 10.5, 'volume': 1000},
        {'date': '2026-01-02', 'open': 10.6, 'high': 18.0, 'low': 10.0, 'close': 11.0, 'volume': 1100},
        {'date': '2026-01-03', 'open': 5.7, 'high': 6.0, 'low': 5.5, 'close': 5.9, 'volume': 1200},
    ])

    ok, meta = check_current_price_vs_historical_high(df, 2)

    assert ok is False
    assert meta['historical_high_reason'] == 'current_price_below_historical_high_ratio'
    assert meta['historical_high_price'] == 18.0
    assert meta['buy_close_price'] == 5.9
    assert meta['current_to_historical_high_ratio'] < 1 / 3


def test_current_price_vs_ma250_accepts_when_close_above_ma250():
    closes = [100.0] * 249 + [120.0]
    rows = [
        {
            'date': f'2025-01-{(idx % 28) + 1:02d}',
            'open': close,
            'high': close + 1,
            'low': close - 1,
            'close': close,
            'volume': 1000,
        }
        for idx, close in enumerate(closes)
    ]
    df = _daily_df(rows)

    ok, meta = check_current_price_vs_ma250(df, len(df) - 1)

    assert ok is True
    assert meta['ma250_reason'] == 'ok'
    assert meta['ma250_value'] == 100.08
    assert meta['buy_close_price'] == 120.0
    assert meta['current_to_ma250_ratio'] > 1.0


def test_current_price_vs_ma250_rejects_when_close_below_ma250():
    closes = [100.0] * 249 + [90.0]
    rows = [
        {
            'date': f'2025-02-{(idx % 28) + 1:02d}',
            'open': close,
            'high': close + 1,
            'low': close - 1,
            'close': close,
            'volume': 1000,
        }
        for idx, close in enumerate(closes)
    ]
    df = _daily_df(rows)

    ok, meta = check_current_price_vs_ma250(df, len(df) - 1)

    assert ok is False
    assert meta['ma250_reason'] == 'current_price_below_ma250'
    assert meta['ma250_value'] == 99.96
    assert meta['buy_close_price'] == 90.0
    assert meta['current_to_ma250_ratio'] < 1.0


def test_volume_pattern_accepts_bullish_spike_with_subsequent_contracted_bearish_bars():
    rows = []
    for idx in range(40):
        rows.append({
            'date': f'2025-03-{(idx % 28) + 1:02d}',
            'open': 10.0,
            'high': 10.3,
            'low': 9.8,
            'close': 10.1,
            'volume': 1000,
        })
    rows[24].update({'open': 10.0, 'close': 10.8, 'high': 10.9, 'low': 9.95, 'volume': 3200})
    rows[26].update({'open': 10.6, 'close': 10.3, 'high': 10.7, 'low': 10.2, 'volume': 2500})
    rows[28].update({'open': 10.4, 'close': 10.2, 'high': 10.45, 'low': 10.1, 'volume': 2900})
    df = _daily_df(rows)

    ok, meta = check_bullish_volume_retrace_pattern(df, 29)

    assert ok is True
    assert meta['volume_pattern_reason'] == 'ok'
    assert meta['volume_pattern_anchor_idx'] == 24
    assert meta['volume_pattern_anchor_volume'] == 3200.0
    assert meta['volume_pattern_anchor_spike_ratio'] == 3.2


def test_volume_pattern_rejects_when_bearish_bar_exceeds_anchor_volume():
    rows = []
    for idx in range(40):
        rows.append({
            'date': f'2025-04-{(idx % 28) + 1:02d}',
            'open': 10.0,
            'high': 10.3,
            'low': 9.8,
            'close': 10.1,
            'volume': 1000,
        })
    rows[24].update({'open': 10.0, 'close': 10.8, 'high': 10.9, 'low': 9.95, 'volume': 3200})
    rows[27].update({'open': 10.7, 'close': 10.1, 'high': 10.75, 'low': 10.0, 'volume': 3300})
    df = _daily_df(rows)

    ok, meta = check_bullish_volume_retrace_pattern(df, 29)

    assert ok is False
    assert meta['volume_pattern_reason'] == 'bearish_volume_not_contracted_after_anchor'
    assert meta['volume_pattern_anchor_idx'] == 24
    assert meta['volume_pattern_bearish_violation_volume'] == 3300.0
    assert meta['volume_pattern_bearish_violation_ratio'] >= 1.0


def test_weekly_slow_volume_build_accepts_three_red_bars_without_spike():
    weekly = pd.DataFrame({
        'date': pd.date_range('2025-01-03', periods=12, freq='W-FRI'),
        'open': [10.1, 10.2, 10.15, 10.3, 10.25, 10.4, 10.35, 10.5, 10.6, 10.7, 10.8, 10.9],
        'high': [10.2, 10.3, 10.25, 10.4, 10.35, 10.5, 10.45, 10.6, 10.8, 10.9, 11.0, 11.1],
        'low': [9.9, 10.0, 9.95, 10.1, 10.05, 10.2, 10.15, 10.3, 10.45, 10.55, 10.65, 10.75],
        'close': [10.0, 10.1, 10.05, 10.2, 10.15, 10.3, 10.25, 10.4, 10.7, 10.82, 10.93, 11.05],
        'volume': [100, 102, 98, 101, 99, 103, 104, 105, 150, 165, 175, 185],
    })

    ok, meta = check_weekly_slow_volume_build(weekly)

    assert ok is True
    assert meta['weekly_slow_volume_reason'] == 'ok'
    assert meta['weekly_slow_volume_consecutive_red_bars'] >= 3
    assert meta['weekly_slow_volume_max_ratio'] > 0


def test_weekly_slow_volume_build_accepts_even_when_recent_red_bars_have_high_ratio():
    weekly = pd.DataFrame({
        'date': pd.date_range('2025-02-07', periods=12, freq='W-FRI'),
        'open': [10.1, 10.2, 10.15, 10.3, 10.25, 10.4, 10.35, 10.5, 10.6, 10.7, 10.8, 10.9],
        'high': [10.2, 10.3, 10.25, 10.4, 10.35, 10.5, 10.45, 10.6, 10.8, 10.9, 11.0, 11.1],
        'low': [9.9, 10.0, 9.95, 10.1, 10.05, 10.2, 10.15, 10.3, 10.45, 10.55, 10.65, 10.75],
        'close': [10.0, 10.1, 10.05, 10.2, 10.15, 10.3, 10.25, 10.4, 10.7, 10.82, 10.93, 11.05],
        'volume': [100, 102, 98, 101, 99, 103, 104, 105, 150, 165, 175, 320],
    })

    ok, meta = check_weekly_slow_volume_build(weekly)

    assert ok is True
    assert meta['weekly_slow_volume_reason'] == 'ok'
    assert meta['weekly_slow_volume_max_ratio'] >= 2.5
