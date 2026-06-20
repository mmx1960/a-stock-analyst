from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from backtest.runners.run_v3_2_full_universe_yearly import backtest_stock_full


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Chunk worker for full-universe Chanlun v3.2 backtest')
    parser.add_argument('--payload', required=True, help='chunk payload json path')
    parser.add_argument('--output', required=True, help='chunk result json path')
    return parser.parse_args()


def load_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    args = parse_args()
    payload_path = Path(args.payload)
    output_path = Path(args.output)
    payload = load_payload(payload_path)

    chunk_index = int(payload['chunk_index'])
    start_year = int(payload['start_year'])
    hold_weeks = int(payload['hold_weeks'])
    stocks = payload.get('stocks', []) or []

    results: list[dict] = []
    signal_type_breakdown: dict[str, int] = defaultdict(int)
    signal_priority_breakdown: dict[str, int] = defaultdict(int)
    total_signals = 0

    for i, item in enumerate(stocks, start=1):
        code = str(item.get('code', '')).strip()
        name = str(item.get('name', code)).strip() or code
        res = backtest_stock_full(code, name, start_year=start_year, hold_weeks=hold_weeks)
        if res.get('total_buy_points', 0) > 0:
            results.append(res)
            for k, v in res.get('signal_type_breakdown', {}).items():
                signal_type_breakdown[k] += int(v)
            for k, v in res.get('signal_priority_breakdown', {}).items():
                signal_priority_breakdown[k] += int(v)
            total_signals += sum(1 for bp in res.get('buy_points', []) if bp.get('status') == '已实现')
        if i % 20 == 0 or i == len(stocks):
            print(f'chunk={chunk_index:04d} progress {i}/{len(stocks)} valid={len(results)} total_signals={total_signals}', flush=True)

    report = {
        'chunk_index': chunk_index,
        'processed_count': len(stocks),
        'valid_stocks': len(results),
        'total_signals': total_signals,
        'signal_type_breakdown': dict(signal_type_breakdown),
        'signal_priority_breakdown': dict(signal_priority_breakdown),
        'results': results,
    }
    write_json(output_path, report)
    print(f'chunk={chunk_index:04d} done output={output_path}', flush=True)


if __name__ == '__main__':
    main()
