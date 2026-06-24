from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.strategy_attack_third_buy import (
    MIN_DAILY_BARS,
    detect_attack_third_buy_structure,
)
from backtest.strategies.kaipanla_sector_strength_score import (
    score_sector_strength_safe,
    summarize_strength_buckets,
)
from backtest.strategies.strategy_v3_1_backtest import (
    fetch_stock_list_with_fallback,
    get_stock_history,
    load_stock_list_from_file,
)

DEFAULT_STOCK_FILE = Path('backtest/stocks/all_a_stocks_cache.json')
DEFAULT_OUTPUT_DIR = Path('backtest/results_v6')
CHECKPOINT_EVERY = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run full-universe attack-third-buy historical backtest')
    parser.add_argument('--stock-file', default=str(DEFAULT_STOCK_FILE), help='本地股票池 JSON 文件路径')
    parser.add_argument('--start-year', type=int, default=2024, help='回测起始年份，默认 2024')
    parser.add_argument('--hold-weeks', type=int, default=10, help='持有周数，默认 10')
    parser.add_argument('--sample-size', type=int, default=0, help='小样本数量；0 表示全市场')
    parser.add_argument('--structure-period', choices=['daily'], default='daily', help='历史回测使用日线结构')
    parser.add_argument('--dedupe-days', type=int, default=20, help='同股信号最小间隔天数，默认 20')
    parser.add_argument('--sector-strength-lookback', type=int, default=10, help='开盘啦板块强度回看交易日；0 表示关闭')
    parser.add_argument('--output', help='输出 JSON 文件路径')
    parser.add_argument('--resume', action='store_true', help='存在 checkpoint 时继续跑，不从头开始')
    return parser.parse_args()


def calculate_future_return(df: pd.DataFrame, buy_date: pd.Timestamp, buy_price: float, weeks: int = 10) -> dict:
    future = df[df['date'] > buy_date].copy()
    if len(future) < 5:
        return {'max_return': 0, 'max_date': None, 'weeks_to_max': 0, 'status': '数据不足'}

    end_date = buy_date + timedelta(days=weeks * 7)
    future = future[future['date'] <= end_date]
    if future.empty:
        return {'max_return': 0, 'max_date': None, 'weeks_to_max': 0, 'status': '无后续数据'}

    max_price = float(future['close'].max())
    max_return = (max_price - buy_price) / buy_price * 100
    max_idx = future['close'].idxmax()
    max_date = future.loc[max_idx, 'date']
    days = (max_date - buy_date).days
    return {
        'max_return': round(max_return, 2),
        'max_price': round(max_price, 2),
        'max_date': str(max_date)[:10],
        'days': int(days),
        'weeks_to_max': round(days / 7, 1),
        'status': '已实现',
    }


def _load_stocks(stock_file: str | None) -> list[tuple[str, str]]:
    if stock_file and Path(stock_file).exists():
        return load_stock_list_from_file(stock_file)
    return fetch_stock_list_with_fallback(fallback_paths=[DEFAULT_STOCK_FILE])


def backtest_stock_attack_third_buy(
    code: str,
    name: str,
    *,
    start_year: int = 2024,
    hold_weeks: int = 10,
    structure_period: str = 'daily',
    dedupe_days: int = 20,
    sector_strength_lookback: int = 10,
) -> dict:
    df_daily = get_stock_history(code, start_date=f'{start_year}0101')
    if df_daily is None or len(df_daily) < max(MIN_DAILY_BARS + 5, 220):
        return {'code': code, 'name': name, 'error': '数据不足', 'buy_points': []}

    buy_points = []
    last_buy_date: pd.Timestamp | None = None
    start_idx = max(MIN_DAILY_BARS, 180)
    for idx in range(start_idx, len(df_daily)):
        window_df = df_daily.iloc[:idx + 1].copy()
        now_dt = pd.to_datetime(window_df.iloc[-1]['date'])
        signal = detect_attack_third_buy_structure(window_df, now=now_dt, structure_period=structure_period)
        if not signal:
            continue

        buy_date = pd.to_datetime(signal['buy_date'])
        if last_buy_date is not None and (buy_date - last_buy_date).days < dedupe_days:
            continue

        buy_price = float(signal['price'])
        future = calculate_future_return(df_daily, buy_date, buy_price, weeks=hold_weeks)
        sector_strength = {}
        if sector_strength_lookback > 0:
            sector_strength = score_sector_strength_safe(
                code=code,
                buy_date=str(buy_date)[:10],
                lookback_trade_days=sector_strength_lookback,
            )
        buy_points.append({
            'buy_date': str(buy_date)[:10],
            'buy_year': int(buy_date.year),
            'detection_date': str(now_dt)[:10],
            'detection_idx': int(idx),
            'buy_price': round(buy_price, 2),
            'third_buy_reason': signal.get('third_buy_reason'),
            'structure_period': signal.get('structure_period'),
            'structure_freq': signal.get('structure_freq'),
            'platform_high': signal.get('platform_high'),
            'platform_low': signal.get('platform_low'),
            'platform_range_pct': signal.get('platform_range_pct'),
            'pre_uptrend_pct': signal.get('pre_uptrend_pct'),
            'breakout_close': signal.get('breakout_close'),
            'pullback_low': signal.get('pullback_low'),
            'restart_volume_ratio': signal.get('restart_volume_ratio'),
            'current_to_120d_high': signal.get('current_to_120d_high'),
            **sector_strength,
            **future,
        })
        last_buy_date = buy_date

    realized_returns = [bp['max_return'] for bp in buy_points if bp.get('status') == '已实现']
    return {
        'code': code,
        'name': name,
        'total_buy_points': len(buy_points),
        'avg_return': round(np.mean(realized_returns), 2) if realized_returns else 0,
        'max_return': round(max(realized_returns), 2) if realized_returns else 0,
        'win_rate': round(sum(1 for r in realized_returns if r > 10) / len(realized_returns) * 100, 1) if realized_returns else 0,
        'buy_points': buy_points,
    }


def build_report(
    *,
    stocks: list[tuple[str, str]],
    processed: int,
    results: list[dict],
    errors: list[dict],
    start_year: int,
    hold_weeks: int,
    structure_period: str,
    dedupe_days: int,
    sector_strength_lookback: int,
    elapsed_seconds: float,
) -> dict:
    all_returns = []
    yearly = defaultdict(list)
    total_buy_points = 0
    for result in results:
        total_buy_points += int(result.get('total_buy_points', 0))
        for point in result.get('buy_points', []):
            if point.get('status') != '已实现':
                continue
            value = float(point['max_return'])
            all_returns.append(value)
            yearly[int(point['buy_year'])].append(value)

    signal_rows = [point for result in results for point in result.get('buy_points', [])]
    strength_summary = summarize_strength_buckets(signal_rows) if sector_strength_lookback > 0 else {
        'bucket_summary': [],
        'recommended_filters': [],
    }
    strength_scores = [float(point.get('kaipanla_strength_score') or 0) for point in signal_rows]
    scored_signals = sum(1 for value in strength_scores if value > 0)

    return {
        'strategy': 'attack-third-buy-v1',
        'mode': 'full_universe' if len(stocks) == processed else 'partial',
        'start_year': start_year,
        'hold_weeks': hold_weeks,
        'structure_period': structure_period,
        'dedupe_days': dedupe_days,
        'sector_strength_lookback': sector_strength_lookback,
        'stock_count': len(stocks),
        'processed_stocks': processed,
        'valid_stocks': len(results),
        'error_count': len(errors),
        'total_buy_points': total_buy_points,
        'total_signals': len(all_returns),
        'avg_return': round(float(np.mean(all_returns)), 2) if all_returns else 0,
        'median_return': round(float(np.median(all_returns)), 2) if all_returns else 0,
        'max_return': round(max(all_returns), 2) if all_returns else 0,
        'win_rate': round(sum(1 for r in all_returns if r > 10) / len(all_returns) * 100, 1) if all_returns else 0,
        'yearly_return_summary': {
            str(year): {
                'signal_count': len(values),
                'win_rate': round(sum(1 for r in values if r > 10) / len(values) * 100, 2) if values else 0,
                'avg_return': round(sum(values) / len(values), 2) if values else 0,
                'median_return': round(float(np.median(values)), 2) if values else 0,
                'max_return': round(max(values), 2) if values else 0,
            }
            for year, values in sorted(yearly.items())
        },
        'sector_strength_summary': {
            'enabled': sector_strength_lookback > 0,
            'scored_signals': scored_signals,
            'scored_ratio_pct': round(scored_signals / len(signal_rows) * 100, 2) if signal_rows else 0.0,
            **strength_summary,
        },
        'elapsed_seconds': round(elapsed_seconds, 1),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'errors': errors[:50],
        'detailed': results,
    }


def run_backtest(args: argparse.Namespace) -> Path:
    stocks = _load_stocks(args.stock_file)
    if args.sample_size > 0:
        stocks = stocks[: args.sample_size]

    output_path = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f"attack_third_buy_full_universe_{args.start_year}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_path.with_suffix('.checkpoint.json')

    results: list[dict] = []
    errors: list[dict] = []
    start_index = 0
    if args.resume and checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        results = checkpoint.get('detailed', [])
        errors = checkpoint.get('errors', [])
        start_index = int(checkpoint.get('processed_stocks', 0))
        print(f"resume checkpoint: processed={start_index} results={len(results)} errors={len(errors)}")

    start_time = time.time()
    for offset, (code, name) in enumerate(stocks[start_index:], start=start_index):
        try:
            result = backtest_stock_attack_third_buy(
                code,
                name,
                start_year=args.start_year,
                hold_weeks=args.hold_weeks,
                structure_period=args.structure_period,
                dedupe_days=args.dedupe_days,
                sector_strength_lookback=args.sector_strength_lookback,
            )
            if result.get('total_buy_points', 0) > 0:
                results.append(result)
                print(f"hit {code} {name} buy_points={result['total_buy_points']} avg={result['avg_return']}% max={result['max_return']}%")
            elif result.get('error'):
                errors.append({'code': code, 'name': name, 'error': result.get('error')})
        except Exception as exc:
            errors.append({'code': code, 'name': name, 'error': str(exc)})

        processed = offset + 1
        if processed % 20 == 0 or processed == len(stocks):
            elapsed = time.time() - start_time
            print(f"progress {processed}/{len(stocks)} hits={len(results)} errors={len(errors)} elapsed={elapsed:.0f}s")
        if processed % CHECKPOINT_EVERY == 0 or processed == len(stocks):
            report = build_report(
                stocks=stocks,
                processed=processed,
                results=results,
                errors=errors,
                start_year=args.start_year,
                hold_weeks=args.hold_weeks,
                structure_period=args.structure_period,
                dedupe_days=args.dedupe_days,
                sector_strength_lookback=args.sector_strength_lookback,
                elapsed_seconds=time.time() - start_time,
            )
            checkpoint_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    report = build_report(
        stocks=stocks,
        processed=len(stocks),
        results=results,
        errors=errors,
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
        structure_period=args.structure_period,
        dedupe_days=args.dedupe_days,
        sector_strength_lookback=args.sector_strength_lookback,
        elapsed_seconds=time.time() - start_time,
    )
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output_path)
    print(json.dumps({k: report[k] for k in ['stock_count', 'processed_stocks', 'valid_stocks', 'total_buy_points', 'total_signals', 'avg_return', 'median_return', 'max_return', 'win_rate']}, ensure_ascii=False, indent=2))
    return output_path


def main() -> None:
    run_backtest(parse_args())


if __name__ == '__main__':
    main()
