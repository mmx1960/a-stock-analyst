"""
正式的 v3.2 排序版回测 runner。
在 v3.1 基线回测基础上增加 signal_score / signal_priority 排序输出。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.strategies.strategy_v3_1_backtest import (
    calculate_future_return,
    fetch_stock_list_with_fallback,
    get_stock_history,
    load_stock_list_from_file,
)
from backtest.strategies.strategy_v3_1_realtime import LOOKBACK_DAYS
from backtest.strategies.strategy_v3_2_ranked import analyze_v3_2_signal

DEFAULT_FALLBACK_STOCK_FILE = Path('backtest/stocks/all_a_stocks_cache.json')
DEFAULT_OUTPUT_DIR = Path('backtest/results_v6')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Chanlun v3.2 ranked backtest runner')
    parser.add_argument('--codes', nargs='+', help='指定股票代码列表，如: 600449 002832')
    parser.add_argument('--stock-file', help='本地股票池 JSON 文件路径')
    parser.add_argument('--start-year', type=int, default=2020, help='回测起始年份，默认 2020')
    parser.add_argument('--hold-weeks', type=int, default=10, help='持有周数，默认 10')
    parser.add_argument('--sample-size', type=int, default=100, help='批量模式抽样数量，默认 100')
    parser.add_argument('--seed', type=int, default=42, help='随机抽样种子，默认 42')
    parser.add_argument('--output', help='输出 JSON 文件路径')
    return parser.parse_args()



def backtest_stock_v3_2(code: str, name: str, start_year: int = 2020, hold_weeks: int = 10) -> dict:
    try:
        df_daily = get_stock_history(code, start_date=f'{start_year}0101')
        if len(df_daily) < 220:
            return {'code': code, 'name': name, 'error': '数据不足', 'buy_points': []}

        buy_points = []
        last_buy_date = None
        start_idx = max(LOOKBACK_DAYS, 180)
        for i in range(start_idx, len(df_daily)):
            window_df = df_daily.iloc[:i + 1].copy()
            signal = analyze_v3_2_signal(window_df, now=window_df.iloc[-1]['date'])
            if not signal:
                continue

            buy_date = pd.to_datetime(signal['buy_date'])
            buy_price = float(signal['price'])
            if last_buy_date and (buy_date - last_buy_date).days < 20:
                continue

            future = calculate_future_return(df_daily, buy_date, buy_price, weeks=hold_weeks)
            buy_points.append({
                'buy_date': str(buy_date)[:10],
                'buy_price': round(buy_price, 2),
                'signal_type': signal['signal_type'],
                'signal_priority': signal['signal_priority'],
                'signal_score': signal['signal_score'],
                'score_breakdown': signal['score_breakdown'],
                'days_ago_at_detection': signal['days_ago'],
                'pullback_pct': signal['pullback_pct'],
                'support_reason': signal['support_reason'],
                **future,
            })
            last_buy_date = buy_date

        buy_points.sort(key=lambda x: (-x['signal_score'], x['buy_date']))
        realized_returns = [bp['max_return'] for bp in buy_points if bp.get('status') == '已实现']
        signal_types = {}
        priority_counts = {}
        for bp in buy_points:
            st = bp.get('signal_type', 'unknown')
            signal_types[st] = signal_types.get(st, 0) + 1
            pr = bp.get('signal_priority', 'P9')
            priority_counts[pr] = priority_counts.get(pr, 0) + 1

        if buy_points:
            scores = [bp['signal_score'] for bp in buy_points]
            return {
                'code': code,
                'name': name,
                'total_buy_points': len(buy_points),
                'avg_return': round(np.mean(realized_returns), 2) if realized_returns else 0,
                'max_return': round(max(realized_returns), 2) if realized_returns else 0,
                'win_rate': round(sum(1 for r in realized_returns if r > 10) / len(realized_returns) * 100, 1) if realized_returns else 0,
                'avg_signal_score': round(np.mean(scores), 2),
                'max_signal_score': round(max(scores), 2),
                'signal_type_breakdown': signal_types,
                'signal_priority_breakdown': priority_counts,
                'top_signal': buy_points[0],
                'buy_points': buy_points,
            }
        return {'code': code, 'name': name, 'total_buy_points': 0, 'buy_points': []}
    except Exception as exc:
        return {'code': code, 'name': name, 'error': str(exc), 'buy_points': []}



def _resolve_stock_name(code: str, stock_file: str | None) -> str:
    if stock_file:
        for item_code, item_name in load_stock_list_from_file(stock_file):
            if item_code == code:
                return item_name
    return code



def run_single_codes(args: argparse.Namespace) -> Path:
    results = []
    for code in args.codes:
        name = _resolve_stock_name(code, args.stock_file)
        results.append(backtest_stock_v3_2(code, name, start_year=args.start_year, hold_weeks=args.hold_weeks))

    output_path = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f"backtest_v3_2_codes_{args.start_year}_{args.hold_weeks}w.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'strategy': 'v3.2',
        'mode': 'single_codes',
        'codes': args.codes,
        'start_year': args.start_year,
        'hold_weeks': args.hold_weeks,
        'results': results,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output_path)
    return output_path



def run_batch(args: argparse.Namespace) -> Path:
    if args.stock_file:
        stock_list = load_stock_list_from_file(args.stock_file)
    else:
        stock_list = fetch_stock_list_with_fallback(fallback_paths=[DEFAULT_FALLBACK_STOCK_FILE])

    if args.sample_size < len(stock_list):
        import random
        random.seed(args.seed)
        stock_list = random.sample(stock_list, args.sample_size)

    results = []
    for code, name in stock_list:
        res = backtest_stock_v3_2(code, name, start_year=args.start_year, hold_weeks=args.hold_weeks)
        if res.get('total_buy_points', 0) > 0:
            results.append(res)

    total_bp = sum(r.get('total_buy_points', 0) for r in results)
    all_returns = []
    signal_type_breakdown = {}
    signal_priority_breakdown = {}
    top_ranked_signals = []
    for r in results:
        for k, v in r.get('signal_type_breakdown', {}).items():
            signal_type_breakdown[k] = signal_type_breakdown.get(k, 0) + v
        for k, v in r.get('signal_priority_breakdown', {}).items():
            signal_priority_breakdown[k] = signal_priority_breakdown.get(k, 0) + v
        if r.get('top_signal'):
            top_ranked_signals.append({
                'code': r['code'],
                'name': r['name'],
                **r['top_signal'],
            })
        for bp in r.get('buy_points', []):
            if bp.get('status') == '已实现':
                all_returns.append(bp['max_return'])

    top_ranked_signals.sort(key=lambda x: (-x['signal_score'], x['buy_date']))
    report = {
        'strategy': 'v3.2',
        'sample_size': len(stock_list),
        'valid_stocks': len(results),
        'total_buy_points': total_bp,
        'total_signals': len(all_returns),
        'hold_weeks': args.hold_weeks,
        'avg_return': round(np.mean(all_returns), 2) if all_returns else 0,
        'median_return': round(np.median(all_returns), 2) if all_returns else 0,
        'max_return': round(max(all_returns), 2) if all_returns else 0,
        'win_rate': round(sum(1 for r in all_returns if r > 10) / len(all_returns) * 100, 1) if all_returns else 0,
        'signal_type_breakdown': signal_type_breakdown,
        'signal_priority_breakdown': signal_priority_breakdown,
        'top_ranked_signals': top_ranked_signals[:20],
        'detailed': results,
    }

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = Path(args.output) if args.output else (DEFAULT_OUTPUT_DIR / f'backtest_v3_2_ranked_{timestamp}.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output_path)
    return output_path



def main() -> None:
    args = parse_args()
    if args.codes:
        run_single_codes(args)
        return
    run_batch(args)


if __name__ == '__main__':
    main()
