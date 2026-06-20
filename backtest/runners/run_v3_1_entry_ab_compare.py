import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.strategy_v3_1_backtest import (
    backtest_stock_v3_1,
    fetch_stock_list_with_fallback,
)


RESULT_DIR = Path('backtest/results_v6')
FALLBACK_PATHS = [
    'backtest/stocks/fallback_stocks_v3_1.json',
]


def _signal_key(signal: dict) -> tuple[str, str, str]:
    return (
        str(signal.get('code', '')),
        str(signal.get('detection_date') or signal.get('buy_date', '')),
        str(signal.get('signal_type', 'unknown')),
    )


def _scenario_entry_reason(signal: dict) -> str:
    return str(signal.get('entry_reason', '') or '')


def _is_baseline_signal(signal: dict) -> bool:
    return True


def _is_confirmed_signal(signal: dict) -> bool:
    return True


def _flatten_buy_points(detailed: list[dict]) -> list[dict]:
    flattened = []
    for stock in detailed:
        code = stock.get('code')
        name = stock.get('name')
        for bp in stock.get('buy_points', []):
            row = dict(bp)
            row['code'] = code
            row['name'] = name
            flattened.append(row)
    return flattened


def _build_diff_report(baseline_detailed: list[dict], confirmed_detailed: list[dict]) -> dict:
    baseline_points_all = _flatten_buy_points(baseline_detailed)
    confirmed_points_all = _flatten_buy_points(confirmed_detailed)

    baseline_points = baseline_points_all
    confirmed_points = confirmed_points_all

    baseline_map = {_signal_key(item): item for item in baseline_points}
    confirmed_map = {_signal_key(item): item for item in confirmed_points}

    debug_dir = RESULT_DIR / 'ab_invariant_debug'
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / 'baseline_keys.json').write_text(
        json.dumps([list(key) for key in sorted(baseline_map.keys())], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (debug_dir / 'confirmed_keys.json').write_text(
        json.dumps([list(key) for key in sorted(confirmed_map.keys())], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    rejection_reasons = Counter()
    rejection_reasons_by_signal = {}
    rejected_signals = []

    for key, baseline_signal in baseline_map.items():
        if key in confirmed_map:
            continue

        signal_type = baseline_signal.get('signal_type', 'unknown')
        reasons = baseline_signal.get('entry_failed_checks') or []
        if not reasons:
            reason = baseline_signal.get('entry_reason')
            reasons = [reason] if reason else ['missing_in_confirmed']

        rejection_reasons_by_signal.setdefault(signal_type, Counter())
        for reason in reasons:
            rejection_reasons[reason] += 1
            rejection_reasons_by_signal[signal_type][reason] += 1

        rejected_signals.append({
            'code': baseline_signal.get('code'),
            'name': baseline_signal.get('name'),
            'buy_date': baseline_signal.get('buy_date'),
            'signal_type': signal_type,
            'entry_score': baseline_signal.get('entry_score'),
            'entry_score_max': baseline_signal.get('entry_score_max'),
            'entry_reason': baseline_signal.get('entry_reason'),
            'entry_failed_checks': baseline_signal.get('entry_failed_checks', []),
            'max_return': baseline_signal.get('max_return'),
            'status': baseline_signal.get('status'),
        })

    return {
        'baseline_total_signals': len(baseline_points),
        'confirmed_total_signals': len(confirmed_points),
        'baseline_total_signals_raw': len(baseline_points_all),
        'confirmed_total_signals_raw': len(confirmed_points_all),
        'baseline_signal_keys': [list(key) for key in sorted(baseline_map.keys())],
        'confirmed_signal_keys': [list(key) for key in sorted(confirmed_map.keys())],
        'unexpected_confirmed_only_keys': [list(key) for key in sorted(set(confirmed_map) - set(baseline_map))],
        'unexpected_confirmed_only_signals': [confirmed_map[key] for key in sorted(set(confirmed_map) - set(baseline_map))],
        'rejected_total': len(rejected_signals),
        'rejection_reason_breakdown': dict(rejection_reasons),
        'rejection_reason_by_signal_type': {
            key: dict(value) for key, value in rejection_reasons_by_signal.items()
        },
        'rejected_signals': rejected_signals,
    }


def _assert_diff_invariants(diff_report: dict) -> None:
    unexpected = diff_report.get('unexpected_confirmed_only_keys', [])
    if unexpected:
        preview = unexpected[:10]
        raise ValueError(
            'AB invariant violated: confirmed contains signals missing from baseline. '
            f'count={len(unexpected)} preview={preview}'
        )


def _compute_summary(detailed: list[dict]) -> dict:
    realized = []
    signal_type_breakdown = Counter()
    entry_reason_breakdown = Counter()
    non_positive_max_return = 0
    negative_current_return = 0
    current_returns = []

    total_buy_points = 0
    for stock in detailed:
        buy_points = stock.get('buy_points', [])
        total_buy_points += len(buy_points)
        for bp in buy_points:
            signal_type_breakdown[bp.get('signal_type', 'unknown')] += 1
            entry_reason_breakdown[bp.get('entry_reason', 'unknown')] += 1

            if bp.get('status') == '已实现':
                max_return = float(bp.get('max_return', 0) or 0)
                realized.append(max_return)
                if max_return <= 0:
                    non_positive_max_return += 1

                buy_price = float(bp.get('buy_price', 0) or 0)
                max_price = float(bp.get('max_price', 0) or 0)
                if buy_price > 0 and max_price > 0:
                    current_return = (max_price - buy_price) / buy_price * 100
                    current_returns.append(current_return)
                    if current_return < 0:
                        negative_current_return += 1

    return {
        'valid_stocks': len(detailed),
        'total_buy_points': total_buy_points,
        'total_signals': len(realized),
        'avg_return': round(float(np.mean(realized)), 2) if realized else 0,
        'median_return': round(float(np.median(realized)), 2) if realized else 0,
        'max_return': round(float(max(realized)), 2) if realized else 0,
        'win_rate': round(sum(1 for r in realized if r > 10) / len(realized) * 100, 1) if realized else 0,
        'non_positive_max_return_count': non_positive_max_return,
        'negative_current_return_count': negative_current_return,
        'signal_type_breakdown': dict(signal_type_breakdown),
        'entry_reason_breakdown': dict(entry_reason_breakdown),
    }


def _compute_summary_for_scenario(detailed: list[dict], scenario_name: str) -> dict:
    scenario_detailed = []
    for stock in detailed:
        kept = []
        for bp in stock.get('buy_points', []):
            if scenario_name == 'baseline':
                if _scenario_entry_reason(bp) == 'baseline_no_entry_confirmation':
                    kept.append(bp)
            else:
                if _scenario_entry_reason(bp) != 'baseline_no_entry_confirmation':
                    kept.append(bp)
        if kept:
            cloned = dict(stock)
            cloned['buy_points'] = kept
            scenario_detailed.append(cloned)
    return _compute_summary(scenario_detailed)


def run_ab_compare(start_year: int = 2020, hold_weeks: int = 10, sample_size: int = 100, seed: int = 42, max_stocks: int | None = None) -> Path:
    all_stocks = fetch_stock_list_with_fallback(fallback_paths=FALLBACK_PATHS)
    rng = random
    rng.seed(seed)
    sample_stocks = rng.sample(all_stocks, min(sample_size, len(all_stocks)))
    if max_stocks is not None:
        sample_stocks = sample_stocks[:max_stocks]

    baseline_detailed = []
    for code, name in sample_stocks:
        res = backtest_stock_v3_1(
            code,
            name,
            start_year=start_year,
            hold_weeks=hold_weeks,
            enforce_historical_high_filter=True,
            enforce_ma250_filter=False,
            enforce_entry_confirmation=False,
        )
        if res.get('total_buy_points', 0) > 0:
            baseline_detailed.append(res)

    confirmed_detailed = []
    for code, name in sample_stocks:
        res = backtest_stock_v3_1(
            code,
            name,
            start_year=start_year,
            hold_weeks=hold_weeks,
            enforce_historical_high_filter=True,
            enforce_ma250_filter=True,
            enforce_entry_confirmation=True,
        )
        if res.get('total_buy_points', 0) > 0:
            confirmed_detailed.append(res)

    scenarios = [
        {
            'name': 'baseline',
            'entry_confirmation_enabled': False,
            'ma250_filter_enabled': False,
            'historical_high_filter_enabled': True,
            'summary': _compute_summary(baseline_detailed),
            'detailed': baseline_detailed,
        },
        {
            'name': 'entry_confirmed',
            'entry_confirmation_enabled': True,
            'ma250_filter_enabled': True,
            'historical_high_filter_enabled': True,
            'summary': _compute_summary(confirmed_detailed),
            'detailed': confirmed_detailed,
        },
    ]

    baseline_summary = scenarios[0]['summary']
    confirmed_summary = scenarios[1]['summary']
    delta = {}
    for key in [
        'valid_stocks',
        'total_buy_points',
        'total_signals',
        'avg_return',
        'median_return',
        'max_return',
        'win_rate',
        'non_positive_max_return_count',
        'negative_current_return_count',
    ]:
        delta[key] = round(confirmed_summary.get(key, 0) - baseline_summary.get(key, 0), 2)

    diff_report = _build_diff_report(baseline_detailed, confirmed_detailed)
    _assert_diff_invariants(diff_report)

    report = {
        'strategy': 'v3.1_strict_ab_compare',
        'start_year': start_year,
        'hold_weeks': hold_weeks,
        'sample_size': len(sample_stocks),
        'seed': seed,
        'stock_pool_size': len(all_stocks),
        'sample_stocks': [{'code': code, 'name': name} for code, name in sample_stocks],
        'rejection_report': diff_report,
        'scenarios': scenarios,
        'delta_confirmed_minus_baseline': delta,
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f'ab_compare_v3_1_entry_confirmation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return out


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=2020)
    parser.add_argument('--hold-weeks', type=int, default=10)
    parser.add_argument('--sample-size', type=int, default=100)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-stocks', type=int, default=None)
    args = parser.parse_args()

    output = run_ab_compare(
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
        sample_size=args.sample_size,
        seed=args.seed,
        max_stocks=args.max_stocks,
    )
    print(output)
