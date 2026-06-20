#!/usr/bin/env python3
"""
A股缠论回测系统 v5 - 完全进程隔离版
每只股票通过subprocess独立运行，30秒超时保护，崩溃自动跳过
"""
import os
import sys
import json
import time
import signal
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest/backtest_v5.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

ANALYZER_SCRIPT = str(Path(__file__).parent / "analyze_stock.py")


def analyze_single_stock_subprocess(code: str, start_year: int = 2000, timeout_sec: int = 30) -> dict:
    """通过子进程分析单只股票"""
    # 使用与主进程相同的 Python 解释器
    python_exe = sys.executable
    
    try:
        result = subprocess.run(
            [python_exe, ANALYZER_SCRIPT, code, str(start_year)],
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def run_backtest_subprocess(codes: list, start_year: int = 2000, 
                            output_dir: str = "backtest/results_v5",
                            delay: float = 0.2):
    """逐个子进程回测"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"开始子进程回测 {len(codes)} 只股票，起始年: {start_year}")
    
    results = []
    success_count = 0
    error_count = 0
    crash_count = 0
    timeout_count = 0
    
    start_time = time.time()
    
    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(codes) - i - 1) / rate if rate > 0 else 0
            logger.info(f"进度: {i+1}/{len(codes)} 成功={success_count} 失败={error_count} 崩溃={crash_count} 超时={timeout_count} 速率={rate:.1f}股/秒 ETA={eta:.0f}秒")
        
        result = analyze_single_stock_subprocess(code, start_year, timeout_sec=30)
        
        if result and 'error' not in result:
            results.append(result)
            success_count += 1
        elif result and 'error' in result:
            error_count += 1
        else:
            crash_count += 1
        
        time.sleep(delay)
    
    elapsed = time.time() - start_time
    logger.info(f"回测完成: 总耗时={elapsed:.0f}秒 成功={success_count} 失败={error_count} 崩溃/超时={crash_count}")
    
    # 保存结果
    result_file = output_path / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"结果已保存: {result_file}")
    
    # 生成统计
    generate_summary_v5(results, output_path)
    
    return results


def generate_summary_v5(results: list, output_path: Path):
    """生成统计报告"""
    if not results:
        return
    
    total_stocks = len(results)
    total_buy_points = sum(r.get('total_buy_points', 0) for r in results)
    
    all_returns = []
    type_stats = defaultdict(list)
    
    for r in results:
        for bp in r.get('buy_points', []):
            if bp.get('max_return', 0) > 0:
                all_returns.append(bp['max_return'])
                type_stats[bp['type']].append(bp['max_return'])
    
    if all_returns:
        avg_return = float(np.mean(all_returns))
        median_return = float(np.median(all_returns))
        max_return = float(max(all_returns))
        win_rate = sum(1 for r in all_returns if r > 10) / len(all_returns) * 100
    else:
        avg_return = median_return = max_return = 0
        win_rate = 0
    
    type_summary = {}
    for bp_type, returns in type_stats.items():
        type_summary[bp_type] = {
            "count": len(returns),
            "avg_return": round(float(np.mean(returns)), 2),
            "median_return": round(float(np.median(returns)), 2),
            "max_return": round(float(max(returns)), 2),
            "win_rate": round(sum(1 for r in returns if r > 10) / len(returns) * 100, 1),
        }
    
    # 最佳股票
    valid = [r for r in results if r.get('avg_max_return', 0) > 0]
    best = sorted(valid, key=lambda x: x['avg_max_return'], reverse=True)[:20]
    
    summary = {
        "total_stocks": total_stocks,
        "total_buy_points": total_buy_points,
        "avg_max_return": round(avg_return, 2),
        "median_max_return": round(median_return, 2),
        "max_return": round(max_return, 2),
        "win_rate": round(win_rate, 1),
        "type_summary": type_summary,
        "best_stocks": [{
            "code": s['code'],
            "buy_points": s['total_buy_points'],
            "avg_return": s['avg_max_return'],
            "max_return": s['max_return'],
            "win_rate": s['win_rate'],
            "trend": s.get('trend', ''),
        } for s in best],
    }
    
    summary_file = output_path / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logger.info("=" * 60)
    logger.info("回测统计报告")
    logger.info("=" * 60)
    logger.info(f"总股票数: {total_stocks}")
    logger.info(f"总买点数: {total_buy_points}")
    logger.info(f"平均最大涨幅: {avg_return:.2f}%")
    logger.info(f"中位数涨幅: {median_return:.2f}%")
    logger.info(f"最大涨幅: {max_return:.2f}%")
    logger.info(f"涨幅>10%胜率: {win_rate:.1f}%")
    logger.info(f"报告已保存: {summary_file}")


if __name__ == "__main__":
    try:
        df = ak.stock_zh_a_spot_em()
        codes = df['代码'].tolist()
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        sys.exit(1)
    
    logger.info(f"总股票数: {len(codes)}")
    run_backtest_subprocess(codes, start_year=2000, delay=0.05)
