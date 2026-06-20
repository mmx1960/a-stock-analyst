"""
缠论共振策略全量历史回测系统 v6
遍历所有股票，从2000年起寻找所有符合策略的买点
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
from multiprocessing import Pool
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data_provider import data_provider
from backtest.strategies.strategy_v3_1_realtime import check_daily_entry_confirmation

# CZSC相关导入
try:
    from czsc import CZSC, Freq, format_standard_kline
    from czsc.utils import ta
except Exception as e:
    print(f"CZSC导入失败: {e}")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest/backtest_v6.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MAX_WEEKLY_PULLBACK_BARS = 6
WEEKLY_LOOKBACK_MULTIPLIER = 3


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _r(val, decimals=2):
    return round(_safe_float(val), decimals)


def get_stock_history(code: str, start_date: str = "20000101", end_date: str = None) -> Optional[pd.DataFrame]:
    """获取股票历史日K线"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    try:
        df = data_provider.get_kline_daily(code=code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None

        col_map = {
            '日期': 'date',
            'trade_date': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'turnover',
            'amount': 'turnover',
            '涨跌幅': 'change_pct',
        }
        df = df.rename(columns=col_map)

        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        if 'turnover' not in df.columns:
            df['turnover'] = 0
        if 'change_pct' not in df.columns:
            df['change_pct'] = 0

        required_cols = ['date', 'open', 'close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_cols):
            return None

        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        for col in ['open', 'close', 'high', 'low', 'volume', 'turnover', 'change_pct']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df[['date', 'open', 'close', 'high', 'low', 'volume', 'turnover', 'change_pct']]
    except Exception:
        return None


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线转周线"""
    if df is None or df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    df = df.set_index('date')
    weekly = df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'turnover': 'sum',
        'change_pct': lambda x: (1 + x/100).prod() * 100 - 100
    }).dropna(subset=['close'])
    
    weekly = weekly.reset_index()
    return weekly


def calculate_macd(df: pd.DataFrame, fast=12, slow=26, signal=9):
    """计算MACD"""
    if 'close' not in df.columns:
        return df
    
    close = df['close']
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = 2 * (dif - dea)
    
    df = df.copy()
    df['dif'] = dif
    df['dea'] = dea
    df['macd'] = macd
    return df


def detect_macd_divergence(df: pd.DataFrame, lookback=20) -> bool:
    """检测MACD底背离"""
    if len(df) < lookback * 2 or 'macd' not in df.columns:
        return False
    
    try:
        # 找到最近的两个低点
        recent = df.tail(lookback * 2)
        
        # 价格低点
        price_min_idx = recent['close'].idxmin()
        price_min_date = recent.loc[price_min_idx, 'date']
        
        # 第二个低点（在第一个低点之前）
        before = recent[recent['date'] < price_min_date]
        if len(before) < lookback // 2:
            return False
        
        second_min_idx = before['close'].idxmin()
        
        # 比较价格：创新低
        price_current = recent.loc[price_min_idx, 'close']
        price_prev = recent.loc[second_min_idx, 'close']
        
        if price_current >= price_prev:
            return False
        
        # 比较MACD：未创新低
        macd_current = recent.loc[price_min_idx, 'macd']
        macd_prev = recent.loc[second_min_idx, 'macd']
        
        # MACD底背离：价格新低但MACD未新低
        return macd_current > macd_prev
        
    except Exception:
        return False


def check_weekly_volume_pattern(df_weekly: pd.DataFrame, lookback=10) -> bool:
    """检查周线放量形态"""
    if len(df_weekly) < lookback:
        return False
    
    try:
        recent = df_weekly.tail(lookback)
        vol_ma = recent['volume'].rolling(window=5).mean().iloc[-1]
        
        if vol_ma == 0:
            return False
        
        # 最近1-3周的成交量
        recent_vols = recent['volume'].tail(3).values
        
        # 条件1：连续2周>1.5倍
        if len(recent_vols) >= 2:
            if all(v > 1.5 * vol_ma for v in recent_vols[-2:]):
                return True
        
        # 条件2：最近1周>2倍
        if recent_vols[-1] > 2.0 * vol_ma:
            return True
        
        return False
        
    except Exception:
        return False


def run_backtest_for_stock(args):
    """单只股票的回测逻辑（在子进程中运行）"""
    code, start_year = args
    
    try:
        # 1. 获取历史数据
        df_daily = get_stock_history(code, start_date=f"{start_year}0101")
        if df_daily is None or len(df_daily) < 200:
            return {"code": code, "error": "数据不足", "buy_points": []}
        
        # 2. 生成周线数据
        df_weekly = resample_weekly(df_daily)
        if len(df_weekly) < 50:
            return {"code": code, "error": "周线数据不足", "buy_points": []}
        
        # 3. 计算MACD
        df_weekly = calculate_macd(df_weekly)
        df_daily = calculate_macd(df_daily)
        
        # 4. 滚动回测：每周检查一次是否符合买入条件
        buy_points = []
        
        # 从第100周开始（确保有足够历史数据）
        for i in range(100, len(df_weekly)):
            # 截取到当前时间点的数据
            current_weekly = df_weekly.iloc[:i+1].copy()
            current_daily = df_daily[df_daily['date'] <= current_weekly.iloc[-1]['date']].copy()
            
            if len(current_daily) < 50:
                continue
            
            # 检查周线条件
            if not check_weekly_volume_pattern(current_weekly):
                continue
            
            # 检查周线CZSC结构
            try:
                czsc_weekly = czsc_analyze_safe(current_weekly.tail(80), Freq.W)
                if not czsc_weekly:
                    continue
                
                bi_weekly = czsc_weekly.bi_list
                if len(bi_weekly) < 3:
                    continue
                
                # 周线趋势：上涨结构
                trend_ok = check_weekly_trend(czsc_weekly)
                if not trend_ok:
                    continue
                
                # 检查日线条件
                if len(current_daily) < 50:
                    continue
                
                czsc_daily = czsc_analyze_safe(current_daily.tail(80), Freq.D)
                if not czsc_daily:
                    continue
                
                bi_daily = czsc_daily.bi_list
                if len(bi_daily) < 3:
                    continue
                
                # 日线条件：有向下笔（回调）
                has_down = any(str(bi.direction) == "向下" for bi in bi_daily[-3:])
                if not has_down:
                    continue
                
                # 日线MACD底背离
                macd_div = detect_macd_divergence(current_daily)
                if not macd_div:
                    continue
                
                entry_ok, entry_meta = check_daily_entry_confirmation(current_daily, len(current_daily) - 1)
                if not entry_ok:
                    continue

                # 符合条件！记录买点
                buy_date = current_weekly.iloc[-1]['date']
                buy_price = float(current_daily.iloc[-1]['close'])
                
                # 计算未来30周的表现
                future_30w = calculate_future_performance(df_daily, buy_date, buy_price, weeks=30)
                
                buy_points.append({
                    "buy_date": str(buy_date)[:10],
                    "buy_price": _r(buy_price),
                    "max_return": future_30w.get("max_return", 0),
                    "max_price": future_30w.get("max_price", 0),
                    "max_date": str(future_30w.get("max_date", ""))[:10],
                    "days_to_max": future_30w.get("days", 0),
                    "weeks_to_max": future_30w.get("weeks", 0),
                    "current_return": future_30w.get("current_return", 0),
                    "weekly_vol_ratio": get_vol_ratio(current_weekly),
                    "trend": get_trend_label(czsc_weekly),
                    "entry_reason": entry_meta.get("entry_reason"),
                    "entry_body_rebound_pct": entry_meta.get("entry_body_rebound_pct"),
                    "entry_close_from_low_pct": entry_meta.get("entry_close_from_low_pct"),
                    "entry_close_above_prev_close_pct": entry_meta.get("entry_close_above_prev_close_pct"),
                    "entry_volume_ratio": entry_meta.get("entry_volume_ratio"),
                })
                
            except Exception:
                continue
        
        # 汇总统计
        if buy_points:
            returns = [bp["max_return"] for bp in buy_points if bp["max_return"] > 0]
            return {
                "code": code,
                "total_buy_points": len(buy_points),
                "avg_max_return": _r(np.mean(returns)) if returns else 0,
                "max_return": _r(max(returns)) if returns else 0,
                "win_rate": _r(sum(1 for r in returns if r > 10) / len(returns) * 100) if returns else 0,
                "buy_points": buy_points,
            }
        else:
            return {
                "code": code,
                "total_buy_points": 0,
                "buy_points": []
            }
            
    except Exception as e:
        return {
            "code": code,
            "error": str(e),
            "buy_points": []
        }


def czsc_analyze_safe(df: pd.DataFrame, freq) -> Optional[CZSC]:
    """安全的CZSC分析"""
    try:
        if df is None or len(df) < 30:
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
        
        if len(std_df) < 30:
            return None
        
        czsc_bars = format_standard_kline(std_df, freq=freq)
        if not czsc_bars:
            return None
        
        return CZSC(czsc_bars)
    except Exception:
        return None


def check_weekly_trend(czsc_obj) -> bool:
    """检查周线趋势：上涨结构 + 回调窗口限制"""
    try:
        bi_list = czsc_obj.bi_list
        if len(bi_list) < 3:
            return False

        recent = bi_list[-4:] if len(bi_list) >= 4 else bi_list[-3:]
        up_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向上"]

        # 基础上涨结构：高点抬高
        if len(up_ends) >= 2 and not (up_ends[-1] > up_ends[-2]):
            return False

        bars_raw = getattr(czsc_obj, 'bars_raw', None)
        if not bars_raw or len(bars_raw) < 11:
            return False

        weekly = pd.DataFrame([
            {
                'date': getattr(bar, 'dt', None),
                'open': _safe_float(getattr(bar, 'open', None)),
                'high': _safe_float(getattr(bar, 'high', None)),
                'low': _safe_float(getattr(bar, 'low', None)),
                'close': _safe_float(getattr(bar, 'close', None)),
                'volume': _safe_float(getattr(bar, 'vol', 0)),
            }
            for bar in bars_raw
        ])
        weekly = weekly.dropna(subset=['date', 'close']).reset_index(drop=True)
        if len(weekly) < 11:
            return False

        closes = weekly['close'].tolist()
        pullback_bars = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                pullback_bars += 1
            else:
                break

        if pullback_bars == 0:
            return True

        if pullback_bars > MAX_WEEKLY_PULLBACK_BARS:
            return False

        lookback = pullback_bars * WEEKLY_LOOKBACK_MULTIPLIER
        start_idx = max(0, len(weekly) - pullback_bars - lookback)
        end_idx = len(weekly) - pullback_bars
        context = weekly.iloc[start_idx:end_idx]
        if context.empty:
            return False

        current_close = float(weekly.iloc[-1]['close'])
        context_high = float(context['high'].max())
        pre_pullback_idx = len(weekly) - pullback_bars - 1
        pre_pullback_high = float(weekly.iloc[pre_pullback_idx]['high']) if pre_pullback_idx >= 0 else None
        below_ratio = float((context['high'] < current_close).mean()) if len(context) > 0 else 0.0
        ratio_ok = below_ratio >= 0.5
        high_anchor_ok = pre_pullback_high is not None and pre_pullback_high >= context_high
        return ratio_ok and high_anchor_ok

    except Exception:
        return False


def get_trend_label(czsc_obj) -> str:
    """获取趋势标签"""
    try:
        bi_list = czsc_obj.bi_list
        if len(bi_list) < 3:
            return "未知"
        
        recent = bi_list[-4:] if len(bi_list) >= 4 else bi_list[-3:]
        up_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向上"]
        down_ends = [float(bi.fx_b.fx) for bi in recent if str(bi.direction) == "向下"]
        
        higher_high = len(up_ends) >= 2 and up_ends[-1] > up_ends[-2]
        lower_low = len(down_ends) >= 2 and down_ends[-1] < down_ends[-2]
        
        if higher_high and not lower_low:
            return "上涨结构"
        elif lower_low and not higher_high:
            return "下跌结构"
        else:
            return "震荡"
    except Exception:
        return "未知"


def get_vol_ratio(df_weekly) -> float:
    """获取最新一周的量比"""
    try:
        if len(df_weekly) < 6:
            return 1.0
        
        vol_ma = df_weekly['volume'].tail(6).head(5).mean()
        vol_current = df_weekly['volume'].iloc[-1]
        
        if vol_ma > 0:
            return round(vol_current / vol_ma, 2)
        return 1.0
    except Exception:
        return 1.0


def calculate_future_performance(df: pd.DataFrame, buy_date, buy_price: float, weeks: int = 30) -> dict:
    """计算未来N周的表现"""
    try:
        future = df[df['date'] >= buy_date].copy()
        if len(future) < 5:
            return {"max_return": 0, "status": "数据不足"}
        
        end_date = buy_date + timedelta(days=weeks * 7)
        future = future[future['date'] <= end_date]
        
        if future.empty:
            return {"max_return": 0, "status": "无后续数据"}
        
        max_price = float(future['close'].max())
        max_return = (max_price - buy_price) / buy_price * 100
        
        max_idx = future['close'].idxmax()
        max_date = future.loc[max_idx, 'date']
        days = (max_date - buy_date).days
        
        last_close = float(future.iloc[-1]['close'])
        current_return = (last_close - buy_price) / buy_price * 100
        
        return {
            "max_return": _r(max_return),
            "max_price": _r(max_price),
            "max_date": str(max_date),
            "days": days,
            "weeks": _r(days / 7, 1),
            "current_return": _r(current_return),
            "status": "已实现"
        }
    except Exception:
        return {"max_return": 0, "status": "计算失败"}


def run_full_backtest(start_year: int = 2000, n_workers: int = 4, 
                     output_dir: str = "backtest/results_v6"):
    """运行全量回测"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 1. 获取全部A股列表
    try:
        logger.info("获取全部A股列表...")
        stock_items = data_provider.get_stock_list() or []
        codes = [str(item.get('code', '')).strip() for item in stock_items if str(item.get('code', '')).strip()]
        logger.info(f"总股票数: {len(codes)}")
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return
    
    # 2. 多进程/单进程回测
    logger.info(f"开始全量回测 {len(codes)} 只股票，workers={n_workers}")
    
    args_list = [(code, start_year) for code in codes]
    
    results = []
    success_count = 0
    error_count = 0
    total_buy_points = 0

    if n_workers <= 1:
        iterator = (run_backtest_for_stock(args) for args in args_list)
    else:
        pool = Pool(processes=n_workers)
        iterator = pool.imap_unordered(run_backtest_for_stock, args_list, chunksize=10)
    
    try:
        for i, result in enumerate(iterator):
            if result and isinstance(result, dict) and result.get('buy_points'):
                if result.get('total_buy_points', 0) > 0:
                    results.append(result)
                    success_count += 1
                    total_buy_points += result.get('total_buy_points', 0)
                else:
                    error_count += 1
            else:
                error_count += 1
            
            if (i + 1) % 100 == 0:
                logger.info(f"进度: {i+1}/{len(codes)} 成功={success_count} 失败={error_count} 买点总数={total_buy_points}")
            
            if (i + 1) % 500 == 0:
                save_intermediate_results(results, output_path, i+1)
    finally:
        if n_workers > 1:
            pool.close()
            pool.join()
    
    logger.info(f"回测完成: 成功{success_count}, 失败{error_count}, 总买点{total_buy_points}")
    
    # 3. 保存最终结果
    save_final_results(results, output_path, start_year)
    
    return results


def save_intermediate_results(results: list, output_path: Path, progress: int):
    """保存中间结果"""
    temp_file = output_path / f"intermediate_{progress}.json"
    
    # 汇总统计
    stats = {
        "progress": progress,
        "total_stocks_with_bp": len(results),
        "total_buy_points": sum(r['total_buy_points'] for r in results),
        "avg_returns": _r(np.mean([r['avg_max_return'] for r in results if r.get('avg_max_return')])) if results else 0,
    }
    
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump({"stats": stats, "results": results}, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"中间结果已保存: {temp_file}")


def save_final_results(results: list, output_path: Path, start_year: int = 2000):
    """保存最终结果和统计报告"""
    # 1. 保存详细结果
    result_file = output_path / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"详细结果已保存: {result_file}")
    
    # 2. 生成统计报告
    generate_report(results, output_path, start_year)


def generate_report(results: list, output_path: Path, start_year: int = 2000):
    """生成统计报告"""
    if not results:
        return
    
    # 总体统计
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
    
    # 按买点统计
    buy_stats = {
        "total_stocks": total_stocks,
        "total_buy_points": total_buy_points,
        "avg_return_per_buy_point": _r(avg_return),
        "median_return": _r(median_return),
        "max_return": _r(max_return),
        "win_rate": _r(win_rate),
    }
    
    # 最佳股票
    best_stocks = sorted(results, key=lambda x: x.get('avg_max_return', 0), reverse=True)[:20]
    
    # 所有买点明细
    all_bp_details = []
    for r in results:
        for bp in r['buy_points']:
            all_bp_details.append({
                "code": r['code'],
                "buy_date": bp['buy_date'],
                "buy_price": bp['buy_price'],
                "max_return": bp['max_return'],
                "max_date": bp['max_date'],
                "weeks_to_max": bp['weeks_to_max'],
                "vol_ratio": bp['weekly_vol_ratio'],
                "trend": bp['trend'],
            })
    
    # 按年统计
    yearly_stats = defaultdict(lambda: {"count": 0, "returns": []})
    for bp in all_bp_details:
        year = bp['buy_date'][:4]
        yearly_stats[year]["count"] += 1
        yearly_stats[year]["returns"].append(bp['max_return'])
    
    yearly_summary = {}
    for year, data in sorted(yearly_stats.items()):
        if data["returns"]:
            yearly_summary[year] = {
                "buy_points": data["count"],
                "avg_return": _r(np.mean(data["returns"])),
                "win_rate": _r(sum(1 for r in data["returns"] if r > 10) / len(data["returns"]) * 100),
            }
    
    report = {
        "summary": buy_stats,
        "best_stocks": [{
            "code": s['code'],
            "buy_points": s['total_buy_points'],
            "avg_return": s['avg_max_return'],
            "max_return": s['max_return'],
            "win_rate": s['win_rate'],
        } for s in best_stocks],
        "yearly_summary": yearly_summary,
        "total_buy_points_detail": all_bp_details,
    }
    
    report_file = output_path / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"统计报告已保存: {report_file}")
    logger.info("="*60)
    logger.info("缠论共振策略历史回测报告")
    logger.info("="*60)
    logger.info(f"统计周期: {start_year} - 2026")
    logger.info(f"股票总数: {total_stocks}")
    logger.info(f"总买点数: {total_buy_points}")
    logger.info(f"平均涨幅: {avg_return:.2f}%")
    logger.info(f"最大涨幅: {max_return:.2f}%")
    logger.info(f"胜率(>10%): {win_rate:.1f}%")
    logger.info("="*60)


if __name__ == "__main__":
    run_full_backtest(start_year=2000, n_workers=4)
