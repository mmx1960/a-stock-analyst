from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import akshare as ak

from backtest.strategies.strategy_v3_1_backtest import get_stock_history, load_stock_list_from_file
from backtest.strategies.strategy_v3_1_realtime import LOOKBACK_DAYS
from backtest.strategies.strategy_v3_2_ranked import analyze_v3_2_signal

DEFAULT_STOCK_FILE = Path('backtest/stocks/all_a_stocks_cache.json')
DEFAULT_OUTPUT_DIR = Path('backtest/results_v6')
META_CACHE_DIR = Path('backtest/cache/stock_meta')
CHECKPOINT_EVERY = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run full-universe Chanlun v3.2 backtest with yearly win-rate report')
    parser.add_argument('--stock-file', default=str(DEFAULT_STOCK_FILE), help='本地股票池 JSON 文件路径')
    parser.add_argument('--start-year', type=int, default=2010, help='回测起始年份，默认 2010')
    parser.add_argument('--hold-weeks', type=int, default=10, help='持有周数，默认 10')
    parser.add_argument('--output', help='输出 JSON 文件路径')
    parser.add_argument('--resume', action='store_true', help='存在 checkpoint 时继续跑，不从头开始')
    return parser.parse_args()


def _safe_stock_name(name: str) -> str:
    return str(name or '').strip().replace(' ', '')


def _is_st_name(name: str) -> bool:
    normalized = _safe_stock_name(name).upper()
    return 'ST' in normalized or '退' in normalized


def _meta_cache_file(code: str) -> Path:
    META_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return META_CACHE_DIR / f'{code}.json'


def get_stock_meta(code: str) -> dict:
    cache_file = _meta_cache_file(code)
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding='utf-8'))
        except Exception:
            pass

    meta = {'code': code, 'name': code, 'list_date': None}
    try:
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            values = {str(row['item']).strip(): str(row['value']).strip() for _, row in df.iterrows()}
            meta['name'] = values.get('股票简称') or values.get('证券简称') or code
            list_date = values.get('上市时间')
            if list_date and list_date.isdigit() and len(list_date) == 8:
                meta['list_date'] = list_date
    except Exception:
        pass

    cache_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta


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


def backtest_stock_full(code: str, name: str, start_year: int = 2010, hold_weeks: int = 10) -> dict:
    meta = get_stock_meta(code)
    canonical_name = meta.get('name') or name or code
    list_date_raw = meta.get('list_date')
    list_date = pd.to_datetime(list_date_raw, format='%Y%m%d', errors='coerce') if list_date_raw else pd.NaT

    try:
        df_daily = get_stock_history(code, start_date=f'{start_year}0101')
        if len(df_daily) < 220:
            return {'code': code, 'name': canonical_name, 'error': '数据不足', 'buy_points': []}

        buy_points = []
        last_buy_date = None
        start_idx = max(LOOKBACK_DAYS, 180)
        for i in range(start_idx, len(df_daily)):
            window_df = df_daily.iloc[:i + 1].copy()
            now_dt = pd.to_datetime(window_df.iloc[-1]['date'])

            if pd.notna(list_date) and now_dt < list_date + pd.Timedelta(days=180):
                continue

            current_name = canonical_name
            if _is_st_name(current_name):
                continue

            signal = analyze_v3_2_signal(window_df, now=now_dt)
            if not signal:
                continue

            buy_date = pd.to_datetime(signal['buy_date'])
            buy_price = float(signal['price'])
            if last_buy_date and (buy_date - last_buy_date).days < 20:
                continue

            future = calculate_future_return(df_daily, buy_date, buy_price, weeks=hold_weeks)
            buy_points.append({
                'buy_date': str(buy_date)[:10],
                'buy_year': int(buy_date.year),
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
        yearly = defaultdict(list)
        for bp in buy_points:
            st = bp.get('signal_type', 'unknown')
            signal_types[st] = signal_types.get(st, 0) + 1
            pr = bp.get('signal_priority', 'P9')
            priority_counts[pr] = priority_counts.get(pr, 0) + 1
            if bp.get('status') == '已实现':
                yearly[int(bp['buy_year'])].append(float(bp['max_return']))

        return {
            'code': code,
            'name': canonical_name,
            'list_date': str(list_date.date()) if pd.notna(list_date) else None,
            'total_buy_points': len(buy_points),
            'avg_return': round(np.mean(realized_returns), 2) if realized_returns else 0,
            'max_return': round(max(realized_returns), 2) if realized_returns else 0,
            'win_rate': round(sum(1 for r in realized_returns if r > 10) / len(realized_returns) * 100, 1) if realized_returns else 0,
            'signal_type_breakdown': signal_types,
            'signal_priority_breakdown': priority_counts,
            'yearly_return_summary': {
                str(year): {
                    'signal_count': len(values),
                    'win_rate': round(sum(1 for r in values if r > 10) / len(values) * 100, 2) if values else 0,
                    'avg_return': round(sum(values) / len(values), 2) if values else 0,
                }
                for year, values in sorted(yearly.items())
            },
            'buy_points': buy_points,
        }
    except Exception as exc:
        return {'code': code, 'name': canonical_name, 'error': str(exc), 'buy_points': []}


def build_report(
    *,
    args: argparse.Namespace,
    stock_list: list[tuple[str, str]],
    results: list[dict],
) -> dict:
    all_returns: list[float] = []
    yearly_returns = defaultdict(list)
    signal_type_breakdown: dict[str, int] = {}
    signal_priority_breakdown: dict[str, int] = {}

    for res in results:
        for k, v in res.get('signal_type_breakdown', {}).items():
            signal_type_breakdown[k] = signal_type_breakdown.get(k, 0) + v
        for k, v in res.get('signal_priority_breakdown', {}).items():
            signal_priority_breakdown[k] = signal_priority_breakdown.get(k, 0) + v
        for bp in res.get('buy_points', []):
            if bp.get('status') == '已实现':
                all_returns.append(bp['max_return'])
                yearly_returns[int(bp['buy_year'])].append(float(bp['max_return']))

    yearly_stats = {
        str(year): {
            'signal_count': len(values),
            'win_count': sum(1 for r in values if r > 10),
            'win_rate': round(sum(1 for r in values if r > 10) / len(values) * 100, 2) if values else 0,
            'avg_return': round(sum(values) / len(values), 2) if values else 0,
            'median_return': round(float(np.median(values)), 2) if values else 0,
            'max_return': round(max(values), 2) if values else 0,
        }
        for year, values in sorted(yearly_returns.items())
    }

    return {
        'strategy': 'v3.2-full-universe',
        'mode': 'full_universe_yearly',
        'stock_file': args.stock_file,
        'sample_size': len(stock_list),
        'valid_stocks': len(results),
        'start_year': args.start_year,
        'hold_weeks': args.hold_weeks,
        'filters': {
            'exclude_st_by_name': True,
            'exclude_before_listing_plus_180d': True,
        },
        'total_buy_points': sum(r.get('total_buy_points', 0) for r in results),
        'total_signals': len(all_returns),
        'avg_return': round(np.mean(all_returns), 2) if all_returns else 0,
        'median_return': round(float(np.median(all_returns)), 2) if all_returns else 0,
        'max_return': round(max(all_returns), 2) if all_returns else 0,
        'win_rate': round(sum(1 for r in all_returns if r > 10) / len(all_returns) * 100, 2) if all_returns else 0,
        'signal_type_breakdown': signal_type_breakdown,
        'signal_priority_breakdown': signal_priority_breakdown,
        'yearly_stats': yearly_stats,
        'detailed': results,
    }


def checkpoint_path_for(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + '.checkpoint.json')


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_checkpoint(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def save_checkpoint(
    *,
    checkpoint_path: Path,
    output_path: Path,
    args: argparse.Namespace,
    stock_list: list[tuple[str, str]],
    processed_count: int,
    results: list[dict],
) -> None:
    report = build_report(args=args, stock_list=stock_list, results=results)
    payload = {
        'checkpoint_version': 1,
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        'output_path': str(output_path),
        'processed_count': processed_count,
        'sample_size': len(stock_list),
        'args': {
            'stock_file': args.stock_file,
            'start_year': args.start_year,
            'hold_weeks': args.hold_weeks,
        },
        'report': report,
    }
    write_json(checkpoint_path, payload)


def run_full(args: argparse.Namespace) -> Path:
    stock_list = load_stock_list_from_file(args.stock_file)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = Path(args.output) if args.output else (DEFAULT_OUTPUT_DIR / f'backtest_v3_2_full_universe_{args.start_year}_{timestamp}.json')
    checkpoint_path = checkpoint_path_for(output_path)

    results: list[dict] = []
    start_idx = 0

    if args.resume:
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            saved_args = checkpoint.get('args', {})
            same_job = (
                str(saved_args.get('stock_file')) == str(args.stock_file)
                and int(saved_args.get('start_year', -1)) == int(args.start_year)
                and int(saved_args.get('hold_weeks', -1)) == int(args.hold_weeks)
            )
            if same_job:
                report = checkpoint.get('report', {})
                results = report.get('detailed', []) or []
                start_idx = int(checkpoint.get('processed_count', 0) or 0)
                print(f'resume checkpoint {start_idx}/{len(stock_list)} valid={len(results)}')
            else:
                print('checkpoint 参数不匹配，忽略旧 checkpoint，重新开始')

    for idx in range(start_idx, len(stock_list)):
        code, name = stock_list[idx]
        res = backtest_stock_full(code, name, start_year=args.start_year, hold_weeks=args.hold_weeks)
        if res.get('total_buy_points', 0) > 0:
            results.append(res)

        processed_count = idx + 1
        if processed_count % 100 == 0:
            current_report = build_report(args=args, stock_list=stock_list, results=results)
            print(f"progress {processed_count}/{len(stock_list)} valid={len(results)} total_signals={current_report['total_signals']}")

        if processed_count % CHECKPOINT_EVERY == 0 or processed_count == len(stock_list):
            save_checkpoint(
                checkpoint_path=checkpoint_path,
                output_path=output_path,
                args=args,
                stock_list=stock_list,
                processed_count=processed_count,
                results=results,
            )

    report = build_report(args=args, stock_list=stock_list, results=results)
    write_json(output_path, report)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    print(output_path)
    return output_path


def main() -> None:
    args = parse_args()
    run_full(args)


if __name__ == '__main__':
    main()
