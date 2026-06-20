import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtest.archive.backtest_v6 import check_daily_entry_confirmation
from backtest.strategies.strategy_v3_1_realtime import (
    check_bullish_volume_retrace_pattern,
    check_current_price_vs_ma250,
)


def _daily_df(rows):
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df


def test_full_runner_daily_entry_accepts_strong_reversal_bar():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.0, 'high': 10.2, 'low': 9.8, 'close': 9.9, 'volume': 1000},
        {'date': '2026-01-02', 'open': 9.9, 'high': 10.0, 'low': 9.6, 'close': 9.7, 'volume': 1100},
        {'date': '2026-01-03', 'open': 9.7, 'high': 9.95, 'low': 9.6, 'close': 9.88, 'volume': 1500},
    ])

    ok, meta = check_daily_entry_confirmation(df, 2)

    assert ok is True
    assert meta['entry_reason'] == 'ok'


def test_full_runner_daily_entry_accepts_score_three_low_volume_reversal():
    df = _daily_df([
        {'date': '2026-01-01', 'open': 10.0, 'high': 10.2, 'low': 9.8, 'close': 9.9, 'volume': 2000},
        {'date': '2026-01-02', 'open': 9.9, 'high': 10.0, 'low': 9.6, 'close': 9.7, 'volume': 2100},
        {'date': '2026-01-03', 'open': 9.7, 'high': 9.95, 'low': 9.6, 'close': 9.88, 'volume': 1000},
    ])

    ok, meta = check_daily_entry_confirmation(df, 2)

    assert ok is True
    assert meta['entry_score'] == 3
    assert meta['entry_reason'] == 'score_pass_with_soft_failures'
    assert meta['entry_failed_checks'] == ['entry_volume_too_low']


def test_ma250_filter_accepts_when_close_above_ma250():
    closes = [100.0] * 249 + [120.0]
    rows = [
        {
            'date': f'2025-03-{(idx % 28) + 1:02d}',
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


def test_ma250_filter_rejects_when_close_below_ma250():
    closes = [100.0] * 249 + [90.0]
    rows = [
        {
            'date': f'2025-04-{(idx % 28) + 1:02d}',
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


def test_volume_pattern_accepts_bullish_spike_with_subsequent_contracted_bearish_bars():
    rows = []
    for idx in range(40):
        rows.append({
            'date': f'2025-05-{(idx % 28) + 1:02d}',
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


def test_volume_pattern_rejects_when_bearish_bar_matches_anchor_volume():
    rows = []
    for idx in range(40):
        rows.append({
            'date': f'2025-06-{(idx % 28) + 1:02d}',
            'open': 10.0,
            'high': 10.3,
            'low': 9.8,
            'close': 10.1,
            'volume': 1000,
        })
    rows[24].update({'open': 10.0, 'close': 10.8, 'high': 10.9, 'low': 9.95, 'volume': 3200})
    rows[27].update({'open': 10.7, 'close': 10.1, 'high': 10.75, 'low': 10.0, 'volume': 3100})
    df = _daily_df(rows)

    ok, meta = check_bullish_volume_retrace_pattern(df, 29)

    assert ok is False
    assert meta['volume_pattern_reason'] == 'bearish_volume_not_contracted_after_anchor'
    assert meta['volume_pattern_anchor_idx'] == 24
    assert meta['volume_pattern_bearish_violation_volume'] == 3100.0
    assert meta['volume_pattern_bearish_violation_ratio'] >= 0.95
