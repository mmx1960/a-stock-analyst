import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.runners.run_v3_1_entry_ab_compare import (
    _assert_diff_invariants,
    _build_diff_report,
    _compute_summary,
)


def test_compute_summary_counts_entry_and_failure_metrics():
    detailed = [
        {
            'code': '000001',
            'buy_points': [
                {
                    'signal_type': 'macd_area_divergence',
                    'entry_reason': 'baseline_no_entry_confirmation',
                    'status': '已实现',
                    'max_return': -2.0,
                    'buy_price': 10.0,
                    'max_price': 9.8,
                },
                {
                    'signal_type': 'higher_low',
                    'entry_reason': 'ok',
                    'status': '已实现',
                    'max_return': 12.0,
                    'buy_price': 10.0,
                    'max_price': 11.2,
                },
            ],
        }
    ]

    summary = _compute_summary(detailed)

    assert summary['valid_stocks'] == 1
    assert summary['total_buy_points'] == 2
    assert summary['total_signals'] == 2
    assert summary['avg_return'] == 5.0
    assert summary['median_return'] == 5.0
    assert summary['max_return'] == 12.0
    assert summary['win_rate'] == 50.0
    assert summary['non_positive_max_return_count'] == 1
    assert summary['negative_current_return_count'] == 1
    assert summary['entry_reason_breakdown'] == {
        'baseline_no_entry_confirmation': 1,
        'ok': 1,
    }
    assert summary['signal_type_breakdown'] == {
        'macd_area_divergence': 1,
        'higher_low': 1,
    }


def test_build_diff_report_uses_matched_signal_diff():
    baseline_detailed = [
        {
            'code': '000001',
            'name': 'PingAn',
            'buy_points': [
                {
                    'buy_date': '2026-01-03',
                    'detection_date': '2026-01-03',
                    'signal_type': 'higher_low',
                    'entry_score': 1,
                    'entry_score_max': 2,
                    'entry_reason': 'entry_recent_5bars_made_new_low',
                    'entry_failed_checks': ['entry_recent_5bars_made_new_low', 'entry_recent_5bars_not_enough_red_bars'],
                    'max_return': -3.2,
                    'status': '已实现',
                },
                {
                    'buy_date': '2026-01-10',
                    'detection_date': '2026-01-10',
                    'signal_type': 'macd_area_divergence',
                    'entry_score': 3,
                    'entry_score_max': 4,
                    'entry_reason': 'baseline_no_entry_confirmation',
                    'entry_failed_checks': [],
                    'max_return': 8.6,
                    'status': '已实现',
                },
            ],
        }
    ]
    confirmed_detailed = [
        {
            'code': '000001',
            'name': 'PingAn',
            'buy_points': [
                {
                    'buy_date': '2026-01-10',
                    'detection_date': '2026-01-10',
                    'signal_type': 'macd_area_divergence',
                    'entry_score': 3,
                    'entry_score_max': 4,
                    'entry_reason': 'score_pass_with_soft_failures',
                    'entry_failed_checks': ['entry_close_too_close_to_low'],
                    'max_return': 8.6,
                    'status': '已实现',
                },
            ],
        }
    ]

    report = _build_diff_report(baseline_detailed, confirmed_detailed)

    assert report['unexpected_confirmed_only_keys'] == []
    assert report['baseline_total_signals'] == 2
    assert report['confirmed_total_signals'] == 1
    assert report['baseline_total_signals_raw'] == 2
    assert report['confirmed_total_signals_raw'] == 1
    assert report['rejection_reason_breakdown'] == {
        'entry_recent_5bars_made_new_low': 1,
        'entry_recent_5bars_not_enough_red_bars': 1,
    }
    assert report['rejection_reason_by_signal_type'] == {
        'higher_low': {
            'entry_recent_5bars_made_new_low': 1,
            'entry_recent_5bars_not_enough_red_bars': 1,
        }
    }
    assert report['rejected_signals'] == [{
        'code': '000001',
        'name': 'PingAn',
        'buy_date': '2026-01-03',
        'signal_type': 'higher_low',
        'entry_score': 1,
        'entry_score_max': 2,
        'entry_reason': 'entry_recent_5bars_made_new_low',
        'entry_failed_checks': ['entry_recent_5bars_made_new_low', 'entry_recent_5bars_not_enough_red_bars'],
        'max_return': -3.2,
        'status': '已实现',
    }]


def test_build_diff_report_rejects_confirmed_only_signals():
    baseline_detailed = [
        {
            'code': '000001',
            'name': 'PingAn',
            'buy_points': [
                {
                    'buy_date': '2026-01-10',
                    'detection_date': '2026-01-10',
                    'signal_type': 'macd_area_divergence',
                    'entry_score': 3,
                    'entry_score_max': 4,
                    'entry_reason': 'baseline_no_entry_confirmation',
                    'entry_failed_checks': [],
                    'max_return': 8.6,
                    'status': '已实现',
                },
            ],
        }
    ]
    confirmed_detailed = [
        {
            'code': '000001',
            'name': 'PingAn',
            'buy_points': [
                {
                    'buy_date': '2026-01-03',
                    'detection_date': '2026-01-03',
                    'signal_type': 'higher_low',
                    'entry_reason': 'entry_recent_5bars_not_enough_red_bars',
                    'entry_failed_checks': ['entry_recent_5bars_not_enough_red_bars'],
                    'status': '已实现',
                    'max_return': 5.0,
                },
            ],
        }
    ]

    report = _build_diff_report(baseline_detailed, confirmed_detailed)

    assert report['unexpected_confirmed_only_keys'] == [[
        '000001', '2026-01-03', 'higher_low'
    ]]


