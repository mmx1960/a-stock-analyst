"""
A股缠论滚动回测系统 v2
从2000年后，按时间窗口滚动分析，检测所有历史买点
"""
import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak
from czsc import CZSC, Freq, format_standard_kline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest/backtest_v2.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _r(val, decimals=2):
    return round(_safe_float(val), decimals)


def detect_buy_sell_points_at_time(bi_list: list) -> list:
    """检测当前时间点的所有买点"""
    if len(bi_list) < 3:
        return []
    
    points = []
    recent_bis = bi_list[-6:] if len(bi_list) >= 6 else bi_list
    
    # 1买：下跌趋势末尾的向上笔
    if len(recent_bis) >= 3:
        for i in range(len(recent_bis) - 2, 0, -1):
            bi = recent_bis[i]
            prev_bi = recent_bis[i-1]
            next_bi = recent_bis[i+1] if i+1 < len(recent_bis) else None
            if str(bi.direction) == "向上" and str(prev_bi.direction) == "向下":
                if next_bi and str(next_bi.direction) == "向下":
                    if next_bi.low > prev_bi.low:
                        points.append({"type": "1买", "price": _r(bi.low), "date": str(bi.sdt), "strength": "strong"})
                        break
    
    # 2买：1买后的第一次回调不破前低
    if len(recent_bis) >= 4:
        for i in range(len(recent_bis) - 2, 1, -1):
            bi = recent_bis[i]
            prev_bi = recent_bis[i-1]
            prev2_bi = recent_bis[i-2]
            if str(bi.direction) == "向下" and str(prev_bi.direction) == "向上":
                if str(prev2_bi.direction) == "向下" and bi.low > prev2_bi.low:
                    points.append({"type": "2买", "price": _r(bi.low), "date": str(bi.sdt), "strength": "normal"})
                    break
    
    # 3买：向上笔回调不破中枢上沿
    if len(recent_bis) >= 4:
        for i in range(len(recent_bis) - 2, 1, -1):
            bi = recent_bis[i]
            if str(bi.direction) == "向上" and str(recent_bis[i-1].direction) == "向下":
                if i >= 2 and str(recent_bis[i-2].direction) == "向上" and bi.low > recent_bis[i-2].high:
                    points.append({"type": "3买", "price": _r(bi.low), "date": str(bi.sdt), "strength": "strong"})
                    break
    
    return points


def get_trend_structure(bi_list: list) -> dict:
    """获取走势结构和趋势"""
    if len(bi_list) < 3:
        return {"trend": "数据不足", "structure": "未知", "bi_count": len(bi_list)}
    
    recent = bi_list[-4:] if len(bi_list) >= 4 else bi_list[-3:]
    up_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向上"]
    down_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向下"]
    
    higher_high = len(up_ends) >= 2 and up_ends[-1] > up_ends[-2]
    lower_low = len(down_ends) >= 2 and down_ends[-1] < down_ends[-2]
    
    last_bi = bi_list[-1]
    last_is_up = str(last_bi.direction) == "向上"
    
    if higher_high and not lower_low:
        trend = "上涨结构"
        desc = f"当前{'向上笔' if last_is_up else '回调'}"
    elif lower_low and not higher_high:
        trend = "下跌结构"
        desc = f"当前{'向上笔(反弹)' if last_is_up else '向下笔'}"
    else:
        trend = "震荡"
        desc = "高低点结构不明确"
    
    return {"trend": trend, "structure": desc, "bi_count": len(bi_list)}


def to_czsc_format(df: pd.DataFrame) -> Optional[list]:
    """将DataFrame转换为CZSC格式"""
    if df is None or len(df) < 50:
        return None
    
    std_df = pd.DataFrame()
    std_df['dt'] = df['date']
    std_df['symbol'] = 'TEST'
    std_df['open'] = df['open']
    std_df['close'] = df['close']
    std_df['high'] = df['high']
    std_df['low'] = df['low']
    std_df['vol'] = df.get('volume', 0)
    std_df['amount'] = df.get('turnover', 0)
    std_df = std_df.dropna()
    
    if std_df.empty:
        return None
    
    return format_standard_kline(std_df, freq=Freq.D)


def analyze_stock_czsc(df: pd.DataFrame) -> Optional[dict]:
    """CZSC缠论分析"""
    if df is None or len(df) < 50:
        return None
    
    try:
        czsc_bars = to_czsc_format(df)
        if not czsc_bars:
            return None
        
        # CZSC可能在某些数据上崩溃，需要捕获所有异常
        try:
            czsc_obj = CZSC(czsc_bars)
            bi_list = czsc_obj.bi_list
            
            if len(bi_list) < 3:
                return None
            
            return {"bi_list": bi_list, "fx_list": czsc_obj.fx_list, "czsc_obj": czsc_obj}
        except Exception:
            return None
    except Exception:
        return None


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


def calculate_future_performance(df: pd.DataFrame, buy_date: str, buy_price: float, weeks: int = 30) -> dict:
    """计算买点后N周表现"""
    try:
        buy_dt = pd.to_datetime(buy_date)
        future_df = df[df['date'] >= buy_dt].copy()
        
        if future_df.empty or len(future_df) < 5:
            return {"max_return": 0, "status": "数据不足"}
        
        max_days = weeks * 7
        end_date = buy_dt + timedelta(days=max_days)
        future_df = future_df[future_df['date'] <= end_date]
        
        if future_df.empty:
            return {"max_return": 0, "status": "无后续数据"}
        
        max_price = future_df['close'].max()
        max_return = (max_price - buy_price) / buy_price * 100
        
        max_idx = future_df['close'].idxmax()
        max_date = future_df.loc[max_idx, 'date']
        days = (max_date - buy_dt).days
        
        last_close = future_df.iloc[-1]['close']
        current_return = (last_close - buy_price) / buy_price * 100
        
        return {
            "max_return": _r(max_return),
            "max_price": _r(max_price),
            "max_date": str(max_date)[:10],
            "days": days,
            "weeks": _r(days / 7, 1),
            "current_return": _r(current_return),
            "status": "已实现"
        }
    except Exception as e:
        return {"max_return": 0, "status": f"计算失败: {e}"}


def rolling_backtest_single_stock(code: str, start_year: int = 2000, step_days: int = 30, weeks: int = 30) -> Optional[dict]:
    """单只股票滚动回测"""
    try:
        # 获取全量历史数据
        df = get_stock_history(code, start_date=f"{start_year}0101")
        if df is None or len(df) < 200:
            return None
        
        all_buy_points = []
        
        # 滚动窗口：从第200条K线开始，每次步进step_days天
        start_idx = 200
        current_idx = start_idx
        
        while current_idx < len(df) - 50:
            # 截取到当前时间点的数据
            window_df = df.iloc[:current_idx].copy()
            
            # CZSC分析
            analysis = analyze_stock_czsc(window_df)
            if analysis:
                bi_list = analysis['bi_list']
                
                # 检测买点
                buy_points = detect_buy_sell_points_at_time(bi_list)
                
                # 记录买点
                for bp in buy_points:
                    # 检查是否已经记录过（去重）
                    bp_date = pd.to_datetime(bp['date'])
                    if any(abs((pd.to_datetime(existing['date']) - bp_date).days) < 5 for existing in all_buy_points):
                        continue
                    
                    # 计算未来表现
                    future_df = df[df['date'] >= bp_date]
                    if len(future_df) >= 5:
                        perf = calculate_future_performance(df, bp['date'], bp['price'], weeks=weeks)
                        trend = get_trend_structure(bi_list)
                        
                        all_buy_points.append({
                            "type": bp['type'],
                            "date": bp['date'][:10],
                            "price": bp['price'],
                            "strength": bp['strength'],
                            "max_return": perf.get('max_return', 0),
                            "max_price": perf.get('max_price', 0),
                            "max_date": perf.get('max_date', ''),
                            "days": perf.get('days', 0),
                            "weeks": perf.get('weeks', 0),
                            "current_return": perf.get('current_return', 0),
                            "trend": trend['trend'],
                            "structure": trend['structure'],
                            "bi_count": trend['bi_count'],
                        })
            
            current_idx += step_days
        
        if not all_buy_points:
            return None
        
        # 统计
        buy_returns = [bp['max_return'] for bp in all_buy_points if bp['max_return'] > 0]
        
        return {
            "code": code,
            "total_buy_points": len(all_buy_points),
            "buy_points": all_buy_points,
            "avg_max_return": _r(np.mean(buy_returns)) if buy_returns else 0,
            "median_max_return": _r(np.median(buy_returns)) if buy_returns else 0,
            "max_return": _r(max(buy_returns)) if buy_returns else 0,
            "min_return": _r(min(buy_returns)) if buy_returns else 0,
            "win_rate": _r(sum(1 for r in buy_returns if r > 10) / len(buy_returns) * 100) if buy_returns else 0,
        }
        
    except Exception as e:
        logger.error(f"{code}回测失败: {e}")
        return None


def run_rolling_backtest(codes: list = None, start_year: int = 2000, step_days: int = 30, 
                        weeks: int = 30, output_dir: str = "backtest/results_rolling"):
    """运行滚动回测"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if codes is None:
        logger.info("获取全部A股列表...")
        try:
            df = ak.stock_zh_a_spot_em()
            codes = df['代码'].tolist()
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return
    
    logger.info(f"开始滚动回测 {len(codes)} 只股票，起始年: {start_year}, 步进: {step_days}天")
    
    results = []
    success_count = 0
    error_count = 0
    
    for i, code in enumerate(codes):
        if (i + 1) % 50 == 0:
            logger.info(f"进度: {i+1}/{len(codes)}")
        
        result = rolling_backtest_single_stock(code, start_year, step_days, weeks)
        if result:
            results.append(result)
            success_count += 1
        else:
            error_count += 1
        
        time.sleep(0.3)  # 限流
    
    logger.info(f"回测完成: 成功{success_count}, 失败{error_count}")
    
    # 保存结果
    result_file = output_path / f"rolling_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"结果已保存: {result_file}")
    
    # 生成统计报告
    generate_rolling_summary(results, output_path)
    
    return results


def generate_rolling_summary(results: list, output_path: Path):
    """生成滚动回测统计报告"""
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
            "avg_return": _r(np.mean(returns)) if returns else 0,
            "median_return": _r(np.median(returns)) if returns else 0,
            "max_return": _r(max(returns)) if returns else 0,
            "win_rate": _r(sum(1 for r in returns if r > 10) / len(returns) * 100) if returns else 0,
        }
    
    # 最佳股票（按平均涨幅排序）
    best_stocks = sorted(results, key=lambda x: x['avg_max_return'], reverse=True)[:20]
    
    summary = {
        "total_stocks": total_stocks,
        "total_buy_points": total_buy_points,
        "avg_max_return": _r(avg_return),
        "median_max_return": _r(median_return),
        "max_return": _r(max_return),
        "win_rate": _r(win_rate),
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
    
    summary_file = output_path / f"summary_rolling_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logger.info(f"统计报告已保存: {summary_file}")
    logger.info(f"总股票数: {total_stocks}")
    logger.info(f"总买点数: {total_buy_points}")
    logger.info(f"平均最大涨幅: {avg_return:.2f}%")
    logger.info(f"最大涨幅: {max_return:.2f}%")
    logger.info(f"涨幅>10%胜率: {win_rate:.1f}%")


if __name__ == "__main__":
    # 测试单只股票
    # result = rolling_backtest_single_stock("600519", start_year=2020)
    # print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    
    # 全量滚动回测
    run_rolling_backtest(start_year=2000, step_days=30, weeks=30)
