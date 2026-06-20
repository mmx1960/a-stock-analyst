from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.strategies.strategy_v3_2_ranked import analyze_v3_2_signal


def _make_df(rows):
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df


def test_v321_rejects_low_volume_zero_axis_pullback():
    df = _make_df([
        {
            'date': '2026-01-01', 'open': 10, 'high': 10.5, 'low': 9.8, 'close': 10.1,
            'volume': 1000, 'dif': 0.01, 'dea': 0.00, 'macd': 0.02,
        },
        {
            'date': '2026-01-02', 'open': 10.2, 'high': 10.6, 'low': 10.0, 'close': 10.3,
            'volume': 1100, 'dif': 0.03, 'dea': 0.02, 'macd': 0.02,
        },
        {
            'date': '2026-01-03', 'open': 10.3, 'high': 10.7, 'low': 10.1, 'close': 10.5,
            'volume': 800, 'dif': 0.04, 'dea': 0.03, 'macd': 0.02,
        },
    ])
    now = datetime(2026, 1, 3)
    result = analyze_v3_2_signal(
        df_daily=df,
        now=now,
        precomputed_signal={
            'buy_date': '2026-01-03',
            'price': 10.5,
            'days_ago': 0,
            'signal_type': 'zero_axis_pullback',
            'pullback_pct': 6.0,
            'support_reason': 'test',
            'higher_low': False,
            'macd_area_divergence': False,
            'zero_axis_pullback': True,
            'area_last': 0.0,
            'area_prev': 1.0,
        },
    )
    assert result is not None
    assert result['ranking_filter_pass'] is False


def test_v321_accepts_strong_zero_axis_pullback_with_confirmation():
    df = _make_df([
        {
            'date': '2026-01-01', 'open': 10, 'high': 10.5, 'low': 9.8, 'close': 10.1,
            'volume': 1000, 'dif': 0.01, 'dea': 0.00, 'macd': 0.02,
        },
        {
            'date': '2026-01-02', 'open': 10.2, 'high': 10.8, 'low': 10.0, 'close': 10.6,
            'volume': 1200, 'dif': 0.05, 'dea': 0.02, 'macd': 0.06,
        },
        {
            'date': '2026-01-03', 'open': 10.5, 'high': 11.0, 'low': 10.4, 'close': 10.9,
            'volume': 2000, 'dif': 0.12, 'dea': 0.07, 'macd': 0.10,
        },
    ])
    now = datetime(2026, 1, 3)
    result = analyze_v3_2_signal(
        df_daily=df,
        now=now,
        precomputed_signal={
            'buy_date': '2026-01-03',
            'price': 10.9,
            'days_ago': 0,
            'signal_type': 'zero_axis_pullback',
            'pullback_pct': 6.0,
            'support_reason': 'test',
            'higher_low': True,
            'macd_area_divergence': False,
            'zero_axis_pullback': True,
            'area_last': 0.0,
            'area_prev': 1.0,
            'entry_recent_5bars_red_bar_count': 2,
            'volume_gate_reason': 'weekly_slow_volume_build',
            'weekly_context_bypassed': True,
            'weekly_gate_reason': 'weekly_context_bypass',
            'intraday_second_buy_ok': False,
        },
    )
    assert result is not None
    assert result['signal_priority'] == 'P2'
    assert result['score_breakdown']['pullback_bonus'] == 8.0


def test_v32_prioritizes_higher_low_and_rewards_new_components():
    df = _make_df([
        {
            'date': '2026-01-01', 'open': 10, 'high': 10.4, 'low': 9.9, 'close': 10.1,
            'volume': 1000, 'dif': 0.02, 'dea': 0.01, 'macd': 0.02,
        },
        {
            'date': '2026-01-02', 'open': 10.1, 'high': 10.5, 'low': 10.0, 'close': 10.3,
            'volume': 1200, 'dif': 0.04, 'dea': 0.02, 'macd': 0.04,
        },
        {
            'date': '2026-01-04', 'open': 10.3, 'high': 10.7, 'low': 10.2, 'close': 10.5,
            'volume': 1700, 'dif': 0.08, 'dea': 0.04, 'macd': 0.08,
        },
        {
            'date': '2026-01-05', 'open': 10.4, 'high': 10.8, 'low': 10.3, 'close': 10.6,
            'volume': 2000, 'dif': 0.10, 'dea': 0.05, 'macd': 0.10,
        },
    ])
    result = analyze_v3_2_signal(
        df_daily=df,
        now=datetime(2026, 1, 5),
        precomputed_signal={
            'buy_date': '2026-01-05',
            'price': 10.6,
            'days_ago': 0,
            'signal_type': 'higher_low',
            'pullback_pct': 9.2,
            'support_reason': 'test',
            'higher_low': True,
            'macd_area_divergence': False,
            'zero_axis_pullback': False,
            'area_last': 0.3,
            'area_prev': 1.0,
            'entry_recent_5bars_red_bar_count': 4,
            'volume_gate_reason': 'daily_volume_spike_retrace',
            'weekly_context_bypassed': False,
            'weekly_gate_reason': 'weekly_context_ok',
            'intraday_second_buy_ok': True,
            'filter_metrics': {
                'obv_last5_trend': 0.2,
                'dif_trend_20d': 0.01,
                'dea_trend_20d': 0.01,
                'macd_negative_days_last10': 0,
                'price_below_dea_on_buy': False,
                'buy_day_volume_vs_ma20': 1.2,
            },
            'buy_day_volume_vs_ma20': 1.2,
        },
    )
    assert result is not None
    assert result['signal_priority'] == 'P1'
    assert result['ranking_filter_pass'] is False
    assert result['score_breakdown']['pullback_bonus'] == 14.0
    assert result['score_breakdown']['stopfall_bonus'] == 6.0
    assert result['score_breakdown']['volume_bonus'] == 6.0
    assert result['score_breakdown']['weekly_context_bonus'] == 4.0
    assert result['score_breakdown']['intraday_bonus'] == 2.0
    assert result['signal_score'] == 132.0


def test_v32_rewards_mid_pullback_more_than_oversized_pullback():
    base_signal = {
        'buy_date': '2026-01-03',
        'price': 10.9,
        'days_ago': 0,
        'signal_type': 'zero_axis_pullback',
        'support_reason': 'test',
        'higher_low': False,
        'macd_area_divergence': False,
        'zero_axis_pullback': True,
        'area_last': 0.0,
        'area_prev': 1.0,
        'entry_recent_5bars_red_bar_count': 2,
        'volume_gate_reason': 'weekly_slow_volume_build',
        'weekly_context_bypassed': True,
        'weekly_gate_reason': 'weekly_context_bypass',
        'intraday_second_buy_ok': False,
    }
    df = _make_df([
        {
            'date': '2026-01-01', 'open': 10, 'high': 10.5, 'low': 9.8, 'close': 10.1,
            'volume': 1000, 'dif': 0.01, 'dea': 0.00, 'macd': 0.02,
        },
        {
            'date': '2026-01-02', 'open': 10.2, 'high': 10.8, 'low': 10.0, 'close': 10.6,
            'volume': 1200, 'dif': 0.05, 'dea': 0.02, 'macd': 0.06,
        },
        {
            'date': '2026-01-03', 'open': 10.5, 'high': 11.0, 'low': 10.4, 'close': 10.9,
            'volume': 2000, 'dif': 0.12, 'dea': 0.07, 'macd': 0.10,
        },
    ])
    mid = analyze_v3_2_signal(df_daily=df, now=datetime(2026, 1, 3), precomputed_signal={**base_signal, 'pullback_pct': 9.0})
    deep = analyze_v3_2_signal(df_daily=df, now=datetime(2026, 1, 3), precomputed_signal={**base_signal, 'pullback_pct': 13.0})
    assert mid is not None and deep is not None
    assert mid['score_breakdown']['pullback_bonus'] == 14.0
    assert deep['score_breakdown']['pullback_bonus'] == 10.0
    assert mid['signal_score'] > deep['signal_score']


def test_v321_rejects_higher_low_when_obv_turns_down_and_macd_too_weak():
    df = _make_df([
        {
            'date': '2026-01-01', 'open': 10, 'high': 10.5, 'low': 9.8, 'close': 10.2,
            'volume': 1000, 'dif': 0.12, 'dea': 0.15, 'macd': -0.03,
        },
        {
            'date': '2026-01-02', 'open': 10.2, 'high': 10.3, 'low': 9.9, 'close': 10.1,
            'volume': 900, 'dif': 0.08, 'dea': 0.12, 'macd': -0.04,
        },
        {
            'date': '2026-01-03', 'open': 10.0, 'high': 10.1, 'low': 9.7, 'close': 9.9,
            'volume': 800, 'dif': 0.03, 'dea': 0.09, 'macd': -0.05,
        },
        {
            'date': '2026-01-04', 'open': 9.9, 'high': 10.0, 'low': 9.6, 'close': 9.8,
            'volume': 700, 'dif': 0.01, 'dea': 0.07, 'macd': -0.06,
        },
        {
            'date': '2026-01-05', 'open': 9.8, 'high': 10.0, 'low': 9.7, 'close': 9.85,
            'volume': 650, 'dif': 0.00, 'dea': 0.05, 'macd': -0.04,
        },
    ])
    now = datetime(2026, 1, 5)
    result = analyze_v3_2_signal(
        df_daily=df,
        now=now,
        precomputed_signal={
            'buy_date': '2026-01-05',
            'price': 9.85,
            'days_ago': 0,
            'signal_type': 'higher_low',
            'pullback_pct': 8.5,
            'support_reason': 'test',
            'higher_low': True,
            'macd_area_divergence': False,
            'zero_axis_pullback': False,
            'area_last': 0.4,
            'area_prev': 1.0,
        },
    )
    assert result is not None
    assert result['ranking_filter_pass'] is False
