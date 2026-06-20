"""
A股缠论回测系统 v3 - 子进程隔离版
每只股票在独立子进程中分析，避免CZSC段错误影响主进程
"""
import os
import sys
import json
import time
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest/backtest_v3.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_stock_history(code: str, start_date: str = "20000101", end_date: str = None) -> Optional[pd.DataFrame]:
    """获取股票历史K线"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq"
        )
        
        if df is None or df.empty:
            return None
        
        col_map = {}
        for col in df.columns:
            if "日期" in col: col_map[col] = "date"
            elif "开盘" in col: col_map[col] = "open"
            elif "收盘" in col: col_map[col] = "close"
            elif "最高" in col: col_map[col] = "high"
            elif "最低" in col: col_map[col] = "low"
            elif "成交量" in col: col_map[col] = "volume"
            elif "成交额" in col: col_map[col] = "turnover"
        
        df = df.rename(columns=col_map)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        return df
    except Exception as e:
        return None


def run_single_stock_backtest(code: str, start_year: int = 2000) -> Optional[dict]:
    """在子进程中运行单只股票回测"""
    # 获取历史数据
    df = get_stock_history(code, start_date=f"{start_year}0101")
    if df is None or len(df) < 200:
        return None
    
    # 将数据保存为临时文件
    temp_file = Path(f"backtest/temp_{code}.csv")
    df.to_csv(temp_file, index=False)
    
    # 调用子进程分析
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "analyze_stock.py"), str(temp_file), code],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        else:
            logger.debug(f"{code}分析失败: {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"{code}分析超时")
        return None
    except Exception as e:
        logger.warning(f"{code}分析异常: {e}")
        return None
    finally:
        # 清理临时文件
        if temp_file.exists():
            temp_file.unlink()


def run_backtest_batch(codes: list, start_year: int = 2000, output_dir: str = "backtest/results_v3"):
    """批量回测"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"开始回测 {len(codes)} 只股票，起始年: {start_year}")
    
    results = []
    success_count = 0
    error_count = 0
    
    for i, code in enumerate(codes):
        if (i + 1) % 50 == 0:
            logger.info(f"进度: {i+1}/{len(codes)}")
        
        result = run_single_stock_backtest(code, start_year)
        if result:
            results.append(result)
            success_count += 1
        else:
            error_count += 1
        
        time.sleep(0.2)  # 限流
    
    logger.info(f"回测完成: 成功{success_count}, 失败{error_count}")
    
    # 保存结果
    result_file = output_path / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"结果已保存: {result_file}")
    
    # 生成统计报告
    generate_summary(results, output_path)
    
    return results


def generate_summary(results: list, output_path: Path):
    """生成统计报告"""
    if not results:
        return
    
    total_stocks = len(results)
    total_buy_points = sum(r['total_buy_points'] for r in results)
    
    all_returns = []
    for r in results:
        for bp in r['buy_points']:
            if bp['max_return'] > 0:
                all_returns.append(bp['max_return'])
    
    if all_returns:
        avg_return = np.mean(all_returns)
        median_return = np.median(all_returns)
        max_return = max(all_returns)
        win_rate = sum(1 for r in all_returns if r > 10) / len(all_returns) * 100
    else:
        avg_return = median_return = max_return = 0
        win_rate = 0
    
    # 按买点类型统计
    type_stats = defaultdict(list)
    for r in results:
        for bp in r['buy_points']:
            type_stats[bp['type']].append(bp['max_return'])
    
    type_summary = {}
    for bp_type, returns in type_stats.items():
        type_summary[bp_type] = {
            "count": len(returns),
            "avg_return": round(float(np.mean(returns)), 2) if returns else 0,
            "median_return": round(float(np.median(returns)), 2) if returns else 0,
            "max_return": round(float(max(returns)), 2) if returns else 0,
            "win_rate": round(sum(1 for r in returns if r > 10) / len(returns) * 100, 1) if returns else 0,
        }
    
    # 最佳股票（按平均涨幅排序）
    valid_results = [r for r in results if 'avg_max_return' in r]
    best_stocks = sorted(valid_results, key=lambda x: x['avg_max_return'], reverse=True)[:20]
    
    summary = {
        "total_stocks": total_stocks,
        "total_buy_points": total_buy_points,
        "avg_max_return": round(float(avg_return), 2),
        "median_max_return": round(float(median_return), 2),
        "max_return": round(float(max_return), 2),
        "win_rate": round(float(win_rate), 1),
        "type_summary": type_summary,
        "best_stocks": [{
            "code": s['code'],
            "buy_points": s['total_buy_points'],
            "avg_return": s['avg_max_return'],
            "median_return": s['median_max_return'],
            "max_return": s['max_return'],
            "win_rate": s['win_rate'],
        } for s in best_stocks],
    }
    
    summary_file = output_path / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logger.info(f"统计报告已保存: {summary_file}")
    logger.info(f"总股票数: {total_stocks}")
    logger.info(f"总买点数: {total_buy_points}")
    logger.info(f"平均最大涨幅: {avg_return:.2f}%")
    logger.info(f"最大涨幅: {max_return:.2f}%")
    logger.info(f"涨幅>10%胜率: {win_rate:.1f}%")


if __name__ == "__main__":
    # 获取全部A股列表
    try:
        df = ak.stock_zh_a_spot_em()
        codes = df['代码'].tolist()
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        sys.exit(1)
    
    logger.info(f"总股票数: {len(codes)}")
    
    # 全量回测
    run_backtest_batch(codes, start_year=2000)
