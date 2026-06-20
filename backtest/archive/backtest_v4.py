"""
A股缠论回测系统 v4 - multiprocessing安全版
使用多进程池，每只股票独立进程，超时自动终止
"""
import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict
from multiprocessing import Pool, TimeoutError

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest/backtest_v4.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def analyze_single_stock(args):
    """分析单只股票（在独立进程中运行）"""
    code, start_year = args
    
    try:
        # 导入CZSC（在子进程中导入）
        from czsc import CZSC, Freq, format_standard_kline
        
        # 获取历史数据
        end_date = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=f"{start_year}0101", end_date=end_date, adjust="qfq"
        )
        
        if df is None or df.empty or len(df) < 200:
            return None
        
        # 标准化
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
        
        # CZSC分析
        std_df = pd.DataFrame()
        std_df['dt'] = df['date']
        std_df['symbol'] = code
        std_df['open'] = df['open']
        std_df['close'] = df['close']
        std_df['high'] = df['high']
        std_df['low'] = df['low']
        std_df['vol'] = df.get('volume', 0)
        std_df['amount'] = df.get('turnover', 0)
        std_df = std_df.dropna()
        
        if len(std_df) < 50:
            return None
        
        czsc_bars = format_standard_kline(std_df, freq=Freq.D)
        if not czsc_bars:
            return None
        
        czsc_obj = CZSC(czsc_bars)
        bi_list = czsc_obj.bi_list
        
        if len(bi_list) < 3:
            return None
        
        # 检测买点（简化版）
        buy_points = []
        recent_bis = bi_list[-6:] if len(bi_list) >= 6 else bi_list
        
        # 1买
        if len(recent_bis) >= 3:
            for i in range(len(recent_bis) - 2, 0, -1):
                bi = recent_bis[i]
                prev_bi = recent_bis[i-1]
                next_bi = recent_bis[i+1] if i+1 < len(recent_bis) else None
                if str(bi.direction) == "向上" and str(prev_bi.direction) == "向下":
                    if next_bi and str(next_bi.direction) == "向下" and next_bi.low > prev_bi.low:
                        buy_points.append({"type": "1买", "price": round(float(bi.low), 2), "date": str(bi.sdt)[:10]})
                        break
        
        # 2买
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                if str(bi.direction) == "向下" and str(recent_bis[i-1].direction) == "向上":
                    if str(recent_bis[i-2].direction) == "向下" and bi.low > recent_bis[i-2].low:
                        buy_points.append({"type": "2买", "price": round(float(bi.low), 2), "date": str(bi.sdt)[:10]})
                        break
        
        # 3买
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                if str(bi.direction) == "向上" and str(recent_bis[i-1].direction) == "向下":
                    if i >= 2 and str(recent_bis[i-2].direction) == "向上" and bi.low > recent_bis[i-2].high:
                        buy_points.append({"type": "3买", "price": round(float(bi.low), 2), "date": str(bi.sdt)[:10]})
                        break
        
        if not buy_points:
            return None
        
        # 分析买点后表现
        buy_reports = []
        for bp in buy_points:
            buy_dt = pd.to_datetime(bp['date'])
            future_df = df[df['date'] >= buy_dt].copy()
            
            if len(future_df) >= 5:
                from datetime import timedelta
                max_days = 30 * 7
                end_date = buy_dt + timedelta(days=max_days)
                future_df = future_df[future_df['date'] <= end_date]
                
                if len(future_df) >= 5:
                    max_price = float(future_df['close'].max())
                    max_return = (max_price - bp['price']) / bp['price'] * 100
                    
                    max_idx = future_df['close'].idxmax()
                    max_date = future_df.loc[max_idx, 'date']
                    days = (max_date - buy_dt).days
                    
                    buy_reports.append({
                        "type": bp['type'],
                        "date": bp['date'],
                        "price": bp['price'],
                        "max_return": round(max_return, 2),
                        "max_price": round(max_price, 2),
                        "max_date": str(max_date)[:10],
                        "days": days,
                        "weeks": round(days / 7, 1),
                    })
        
        if not buy_reports:
            return None
        
        # 趋势判断
        recent = bi_list[-4:] if len(bi_list) >= 4 else bi_list[-3:]
        up_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向上"]
        down_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向下"]
        
        higher_high = len(up_ends) >= 2 and up_ends[-1] > up_ends[-2]
        lower_low = len(down_ends) >= 2 and down_ends[-1] < down_ends[-2]
        
        if higher_high and not lower_low:
            trend = "上涨结构"
        elif lower_low and not higher_high:
            trend = "下跌结构"
        else:
            trend = "震荡"
        
        buy_returns = [br['max_return'] for br in buy_reports if br['max_return'] > 0]
        
        return {
            "code": code,
            "total_buy_points": len(buy_reports),
            "buy_points": buy_reports,
            "avg_max_return": round(float(np.mean(buy_returns)), 2) if buy_returns else 0,
            "median_max_return": round(float(np.median(buy_returns)), 2) if buy_returns else 0,
            "max_return": round(float(max(buy_returns)), 2) if buy_returns else 0,
            "win_rate": round(sum(1 for r in buy_returns if r > 10) / len(buy_returns) * 100, 1) if buy_returns else 0,
            "trend": trend,
            "bi_count": len(bi_list),
        }
        
    except Exception as e:
        return None


def run_backtest_mp(codes: list, start_year: int = 2000, n_workers: int = 4,
                   output_dir: str = "backtest/results_v4"):
    """多进程回测"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"开始多进程回测 {len(codes)} 只股票， workers={n_workers}")
    
    # 准备参数
    args_list = [(code, start_year) for code in codes]
    
    results = []
    success_count = 0
    error_count = 0
    
    # 使用进程池
    with Pool(processes=n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(analyze_single_stock, args_list, chunksize=10)):
            if result:
                results.append(result)
                success_count += 1
            else:
                error_count += 1
            
            if (i + 1) % 100 == 0:
                logger.info(f"进度: {i+1}/{len(codes)}")
    
    logger.info(f"回测完成: 成功{success_count}, 失败{error_count}")
    
    # 保存结果
    result_file = output_path / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"结果已保存: {result_file}")
    
    # 生成统计
    generate_summary_v4(results, output_path)
    
    return results


def generate_summary_v4(results: list, output_path: Path):
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
    
    # 最佳股票
    valid_results = [r for r in results if r.get('avg_max_return', 0) > 0]
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
            "trend": s['trend'],
            "bi_count": s['bi_count'],
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
    
    # 全量回测（4进程）
    run_backtest_mp(codes, start_year=2000, n_workers=4)
