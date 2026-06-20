"""
正式的 v3.1 回测 runner。
支持单票、本地股票池、以及自动 fallback 股票池三种模式。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.strategy_v3_1_backtest import (
    backtest_random_100_v3_1,
    backtest_stock_v3_1,
    load_stock_list_from_file,
)

DEFAULT_FALLBACK_STOCK_FILE = Path('backtest/stocks/all_a_stocks_cache.json')
DEFAULT_OUTPUT_DIR = Path('backtest/results_v6')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Chanlun v3.1 backtest runner')
    parser.add_argument('--codes', nargs='+', help='指定股票代码列表，如: 600449 002832')
    parser.add_argument('--stock-file', help='本地股票池 JSON 文件路径')
    parser.add_argument('--start-year', type=int, default=2020, help='回测起始年份，默认 2020')
    parser.add_argument('--hold-weeks', type=int, default=10, help='持有周数，默认 10')
    parser.add_argument('--sample-size', type=int, default=100, help='批量模式抽样数量，默认 100')
    parser.add_argument('--seed', type=int, default=42, help='随机抽样种子，默认 42')
    parser.add_argument('--output', help='单票模式输出 JSON 文件路径')
    return parser.parse_args()



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
        results.append(backtest_stock_v3_1(code, name, start_year=args.start_year, hold_weeks=args.hold_weeks))

    output_path = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f"backtest_v3_1_codes_{args.start_year}_{args.hold_weeks}w.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'strategy': 'v3.1',
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
    stock_list = None
    fallback_paths = [DEFAULT_FALLBACK_STOCK_FILE]
    if args.stock_file:
        stock_list = load_stock_list_from_file(args.stock_file)
        fallback_paths = None

    return backtest_random_100_v3_1(
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
        sample_size=args.sample_size,
        seed=args.seed,
        stock_list=stock_list,
        fallback_paths=fallback_paths,
    )



def main() -> None:
    args = parse_args()
    if args.codes:
        run_single_codes(args)
        return
    run_batch(args)


if __name__ == '__main__':
    main()
