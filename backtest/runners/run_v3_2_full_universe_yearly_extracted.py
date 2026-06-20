from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT_DIR = Path('backtest/results_v6')
DEFAULT_STOCK_FILE = Path('backtest/stocks/all_a_stocks_cache.json')
CHECKPOINT_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run extracted full-universe Chanlun v3.2 backtest with segmented workers')
    parser.add_argument('--stock-file', default=str(DEFAULT_STOCK_FILE), help='本地股票池 JSON 文件路径')
    parser.add_argument('--start-year', type=int, default=2010, help='回测起始年份，默认 2010')
    parser.add_argument('--hold-weeks', type=int, default=10, help='持有周数，默认 10')
    parser.add_argument('--output', help='最终汇总输出 JSON 文件路径')
    parser.add_argument('--chunk-size', type=int, default=100, help='每个分段处理的股票数，默认 100')
    parser.add_argument('--max-workers', type=int, default=4, help='并发 worker 数，默认 4')
    parser.add_argument('--resume', action='store_true', help='存在 chunk 结果与 checkpoint 时继续跑')
    parser.add_argument('--force-rerun', action='store_true', help='忽略已有 chunk 结果，强制全部重跑')
    return parser.parse_args()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def load_stock_list(stock_file: str) -> list[tuple[str, str]]:
    from backtest.strategies.strategy_v3_1_backtest import load_stock_list_from_file

    return load_stock_list_from_file(stock_file)


def build_report(*, stock_file: str, start_year: int, hold_weeks: int, stock_list: list[tuple[str, str]], results: list[dict]) -> dict:
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
        'strategy': 'v3.2-full-universe-extracted',
        'mode': 'full_universe_yearly_extracted',
        'stock_file': stock_file,
        'sample_size': len(stock_list),
        'valid_stocks': len(results),
        'start_year': start_year,
        'hold_weeks': hold_weeks,
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


def output_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    output_path = Path(args.output) if args.output else (DEFAULT_OUTPUT_DIR / f'backtest_v3_2_full_universe_{args.start_year}_yearly_extracted.json')
    checkpoint_path = output_path.with_suffix(output_path.suffix + '.checkpoint.json')
    chunk_dir = output_path.with_suffix(output_path.suffix + '.chunks')
    return output_path, checkpoint_path, chunk_dir


def save_checkpoint(
    *,
    checkpoint_path: Path,
    output_path: Path,
    chunk_dir: Path,
    args: argparse.Namespace,
    stock_list: list[tuple[str, str]],
    processed_count: int,
    completed_chunks: list[int],
    valid_results: int,
    total_signals: int,
) -> None:
    payload = {
        'checkpoint_version': CHECKPOINT_VERSION,
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        'output_path': str(output_path),
        'chunk_dir': str(chunk_dir),
        'processed_count': processed_count,
        'sample_size': len(stock_list),
        'completed_chunks': completed_chunks,
        'valid_results': valid_results,
        'total_signals': total_signals,
        'args': {
            'stock_file': args.stock_file,
            'start_year': args.start_year,
            'hold_weeks': args.hold_weeks,
            'chunk_size': args.chunk_size,
            'max_workers': args.max_workers,
        },
    }
    write_json(checkpoint_path, payload)


def create_chunk_payload(
    *,
    chunk_index: int,
    chunk_stocks: list[tuple[str, str]],
    args: argparse.Namespace,
    chunk_dir: Path,
) -> tuple[Path, Path]:
    payload_path = chunk_dir / f'chunk_{chunk_index:04d}.stocks.json'
    result_path = chunk_dir / f'chunk_{chunk_index:04d}.result.json'
    payload = {
        'chunk_index': chunk_index,
        'start_year': args.start_year,
        'hold_weeks': args.hold_weeks,
        'stocks': [{'code': code, 'name': name} for code, name in chunk_stocks],
    }
    write_json(payload_path, payload)
    return payload_path, result_path


def chunk_worker_command(payload_path: Path, result_path: Path) -> list[str]:
    return [
        sys.executable,
        '-m',
        'backtest.runners.run_v3_2_full_universe_chunk',
        '--payload',
        str(payload_path),
        '--output',
        str(result_path),
    ]


def run_pending_chunks(
    *,
    chunk_specs: list[dict],
    max_workers: int,
) -> None:
    if not chunk_specs:
        return

    active: list[tuple[subprocess.Popen, dict]] = []
    pending = list(chunk_specs)

    while pending or active:
        while pending and len(active) < max_workers:
            spec = pending.pop(0)
            log_path = spec['log_path']
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fp = open(log_path, 'w', encoding='utf-8')
            proc = subprocess.Popen(
                chunk_worker_command(spec['payload_path'], spec['result_path']),
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                text=True,
            )
            spec['log_fp'] = log_fp
            active.append((proc, spec))
            print(f"spawn chunk {spec['chunk_index']:04d} pid={proc.pid} stocks={spec['stock_count']}")

        next_active: list[tuple[subprocess.Popen, dict]] = []
        for proc, spec in active:
            rc = proc.poll()
            if rc is None:
                next_active.append((proc, spec))
                continue
            spec['log_fp'].close()
            if rc != 0:
                raise RuntimeError(f"chunk {spec['chunk_index']:04d} failed with exit code {rc}; log={spec['log_path']}")
            if not spec['result_path'].exists():
                raise RuntimeError(f"chunk {spec['chunk_index']:04d} exited without result file; log={spec['log_path']}")
            print(f"done chunk {spec['chunk_index']:04d} -> {spec['result_path'].name}")
        active = next_active

        if active:
            import time
            time.sleep(0.5)


def load_chunk_result(path: Path) -> dict:
    data = load_json(path)
    if not data:
        raise RuntimeError(f'chunk result unreadable: {path}')
    return data


def main() -> None:
    args = parse_args()
    if args.chunk_size <= 0:
        raise ValueError('--chunk-size 必须大于 0')
    if args.max_workers <= 0:
        raise ValueError('--max-workers 必须大于 0')

    stock_list = load_stock_list(args.stock_file)
    output_path, checkpoint_path, chunk_dir = output_paths(args)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    total_chunks = math.ceil(len(stock_list) / args.chunk_size)
    chunk_specs: list[dict] = []

    for chunk_index in range(total_chunks):
        start = chunk_index * args.chunk_size
        end = min(len(stock_list), start + args.chunk_size)
        chunk_stocks = stock_list[start:end]
        payload_path, result_path = create_chunk_payload(
            chunk_index=chunk_index,
            chunk_stocks=chunk_stocks,
            args=args,
            chunk_dir=chunk_dir,
        )
        log_path = chunk_dir / f'chunk_{chunk_index:04d}.log'
        chunk_specs.append({
            'chunk_index': chunk_index,
            'stock_count': len(chunk_stocks),
            'payload_path': payload_path,
            'result_path': result_path,
            'log_path': log_path,
        })

    if args.force_rerun:
        for spec in chunk_specs:
            if spec['result_path'].exists():
                spec['result_path'].unlink()

    pending_specs = []
    completed_chunks: list[int] = []
    for spec in chunk_specs:
        if args.resume and spec['result_path'].exists() and not args.force_rerun:
            completed_chunks.append(spec['chunk_index'])
            continue
        pending_specs.append(spec)

    print(f'chunks total={total_chunks} completed={len(completed_chunks)} pending={len(pending_specs)}')
    run_pending_chunks(chunk_specs=pending_specs, max_workers=args.max_workers)

    all_results: list[dict] = []
    processed_count = 0
    completed_chunks = []
    total_signals = 0
    for spec in chunk_specs:
        result = load_chunk_result(spec['result_path'])
        completed_chunks.append(spec['chunk_index'])
        processed_count += int(result.get('processed_count', spec['stock_count']))
        chunk_results = result.get('results', []) or []
        all_results.extend(chunk_results)
        total_signals += int(result.get('total_signals', 0) or 0)

        save_checkpoint(
            checkpoint_path=checkpoint_path,
            output_path=output_path,
            chunk_dir=chunk_dir,
            args=args,
            stock_list=stock_list,
            processed_count=processed_count,
            completed_chunks=completed_chunks,
            valid_results=len(all_results),
            total_signals=total_signals,
        )
        print(f'aggregate {processed_count}/{len(stock_list)} valid={len(all_results)} total_signals={total_signals}')

    report = build_report(
        stock_file=args.stock_file,
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
        stock_list=stock_list,
        results=all_results,
    )
    write_json(output_path, report)
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    print(output_path)


if __name__ == '__main__':
    main()
