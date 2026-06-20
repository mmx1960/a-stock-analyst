"""
策略 v2 回测：随机 100 只股票，2020 年后买点回测
条件：
1. 周线放量：往前30根K线里，存在至少1根K线成交量 > 前5根均量的1.5倍
2. 周线上涨：往前30根K线，价格趋势向上（分段均价抬高）
3. 日线下跌：往前30根日线，价格趋势向下
4. 日线中枢：日线下跌过程中存在价格重叠区间
买点后记录 10 周内最高涨幅
"""
import os
import sys
import json
import time
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import akshare as ak

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def get_stock_history(code: str, start_date: str = "20190101", end_date: str = None) -> pd.DataFrame:
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="hfq")
    if df is None or df.empty:
        return pd.DataFrame()
    col_map = {"日期":"date", "开盘":"open", "收盘":"close", "最高":"high", "最低":"low", "成交量":"volume"}
    df = df.rename(columns=col_map)[['date','open','close','high','low','volume']]
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    for c in ['open','close','high','low','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) < 50:
        return pd.DataFrame()
    df = df.copy().set_index('date')
    weekly = df.resample('W-FRI').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close']).reset_index()
    return weekly

def check_weekly_volume(df_weekly: pd.DataFrame, lookback=30) -> bool:
    if len(df_weekly) < lookback + 5:
        return False
    recent = df_weekly.tail(lookback)
    for i in range(5, len(recent)):
        vol_ma5 = recent['volume'].iloc[i-5:i].mean()
        if vol_ma5 > 0 and recent['volume'].iloc[i] > 1.5 * vol_ma5:
            return True
    return False

def check_weekly_uptrend(df_weekly: pd.DataFrame, lookback=30) -> bool:
    if len(df_weekly) < lookback:
        return False
    close = df_weekly.tail(lookback)['close'].values
    seg1 = close[-5:].mean()
    seg2 = close[-15:-5].mean()
    seg3 = close[-30:-15].mean()
    return seg1 > seg2 > seg3

def check_daily_downtrend(df_daily: pd.DataFrame, lookback=30) -> bool:
    if len(df_daily) < lookback:
        return False
    close = df_daily.tail(lookback)['close'].values
    seg1 = close[-5:].mean()
    seg2 = close[-15:-5].mean()
    seg3 = close[-30:-15].mean()
    return seg1 < seg2 < seg3

def check_daily_zhongshu(df_daily: pd.DataFrame, lookback=30, min_bars=3) -> bool:
    if len(df_daily) < lookback:
        return False
    recent = df_daily.tail(lookback)
    highs = recent['high'].values
    lows = recent['low'].values
    for i in range(len(recent) - min_bars + 1):
        window_h = highs[i:i+min_bars]
        window_l = lows[i:i+min_bars]
        if min(window_h) > max(window_l):
            return True
    return False

def calculate_future_return(df: pd.DataFrame, buy_date: pd.Timestamp, buy_price: float, weeks: int = 10) -> dict:
    future = df[df['date'] > buy_date].copy()
    if len(future) < 5:
        return {"max_return": 0, "max_date": None, "weeks_to_max": 0, "status": "数据不足"}
    end_date = buy_date + timedelta(days=weeks * 7)
    future = future[future['date'] <= end_date]
    if future.empty:
        return {"max_return": 0, "max_date": None, "weeks_to_max": 0, "status": "无后续数据"}
    max_price = float(future['close'].max())
    max_return = (max_price - buy_price) / buy_price * 100
    max_idx = future['close'].idxmax()
    max_date = future.loc[max_idx, 'date']
    days = (max_date - buy_date).days
    return {
        "max_return": round(max_return, 2),
        "max_price": round(max_price, 2),
        "max_date": str(max_date)[:10],
        "days": days,
        "weeks_to_max": round(days / 7, 1),
        "status": "已实现"
    }

def backtest_stock(code: str, name: str, start_year: int = 2020) -> dict:
    try:
        df_daily = get_stock_history(code, start_date=f"{start_year}0101")
        if len(df_daily) < 200:
            return {"code": code, "name": name, "error": "数据不足", "buy_points": []}
            
        df_weekly = resample_weekly(df_daily)
        if len(df_weekly) < 80:
            return {"code": code, "name": name, "error": "周线数据不足", "buy_points": []}
            
        buy_points = []
        last_buy_date = None
        
        for i in range(80, len(df_weekly)):
            cw = df_weekly.iloc[:i+1].copy()
            cd = df_daily[df_daily['date'] <= cw.iloc[-1]['date']].copy()
            
            if len(cd) < 50:
                continue
                
            if not check_weekly_volume(cw):
                continue
            if not check_weekly_uptrend(cw):
                continue
            if not check_daily_downtrend(cd):
                continue
            if not check_daily_zhongshu(cd):
                continue
                
            buy_date = cw.iloc[-1]['date']
            buy_price = float(cd.iloc[-1]['close'])
            
            # 避免重复买点（30 天内不重复）
            if last_buy_date and (buy_date - last_buy_date).days < 30:
                continue
                
            future = calculate_future_return(df_daily, buy_date, buy_price, weeks=10)
            
            buy_points.append({
                "buy_date": str(buy_date)[:10],
                "buy_price": round(buy_price, 2),
                **future
            })
            last_buy_date = buy_date
            
        returns = [bp["max_return"] for bp in buy_points if bp["status"] == "已实现"]
        
        if buy_points:
            return {
                "code": code, "name": name,
                "total_buy_points": len(buy_points),
                "avg_return": round(np.mean(returns), 2) if returns else 0,
                "max_return": round(max(returns), 2) if returns else 0,
                "win_rate": round(sum(1 for r in returns if r > 10) / len(returns) * 100, 1) if returns else 0,
                "buy_points": buy_points
            }
        return {"code": code, "name": name, "total_buy_points": 0, "buy_points": []}
        
    except Exception as e:
        return {"code": code, "name": name, "error": str(e), "buy_points": []}

def main():
    random.seed(42)
    logger.info("=== 策略 v2 回测开始 ===")
    
    # 获取股票列表
    logger.info("获取股票列表...")
    df_list = ak.stock_zh_a_spot_em()
    all_stocks = [(row['代码'], row['名称']) for _, row in df_list.iterrows()]
    
    # 随机选 100 只
    sample_stocks = random.sample(all_stocks, min(100, len(all_stocks)))
    logger.info(f"随机选中 100 只股票")
    
    results = []
    start_time = time.time()
    
    for i, (code, name) in enumerate(sample_stocks):
        res = backtest_stock(code, name, start_year=2020)
        if res.get('total_buy_points', 0) > 0:
            results.append(res)
            bp = res['total_buy_points']
            wr = res.get('win_rate', 0)
            mr = res.get('max_return', 0)
            logger.info(f"✅ {code} {name} | 买点:{bp} | 平均涨幅:{res.get('avg_return',0)}% | 胜率:{wr}% | 最大:{mr}%")
        
        if (i+1) % 20 == 0:
            elapsed = time.time() - start_time
            logger.info(f"进度: {i+1}/100 耗时:{elapsed:.0f}s 有效:{len(results)}")
            
    # 统计报告
    total_bp = sum(r.get('total_buy_points', 0) for r in results)
    all_returns = []
    for r in results:
        for bp in r.get('buy_points', []):
            if bp.get('status') == '已实现':
                all_returns.append(bp['max_return'])
                
    report = {
        "sample_size": 100,
        "valid_stocks": len(results),
        "total_buy_points": total_bp,
        "total_signals": len(all_returns),
        "avg_return": round(np.mean(all_returns), 2) if all_returns else 0,
        "median_return": round(np.median(all_returns), 2) if all_returns else 0,
        "max_return": round(max(all_returns), 2) if all_returns else 0,
        "win_rate": round(sum(1 for r in all_returns if r > 10) / len(all_returns) * 100, 1) if all_returns else 0,
        "detailed": results
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = Path("backtest/results_v6") / f"backtest_v2_random100_{timestamp}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        
    logger.info(f"\n{'='*60}")
    logger.info(f"🎉 回测完成! 总耗时: {time.time()-start_time:.0f}s")
    logger.info(f"{'='*60}")
    logger.info(f"样本数量: {report['sample_size']}")
    logger.info(f"有效股票: {report['valid_stocks']}")
    logger.info(f"总买点数: {report['total_buy_points']}")
    logger.info(f"总信号数: {report['total_signals']}")
    logger.info(f"平均涨幅: {report['avg_return']}%")
    logger.info(f"中位数涨幅: {report['median_return']}%")
    logger.info(f"最大涨幅: {report['max_return']}%")
    logger.info(f"胜率(>10%): {report['win_rate']}%")
    logger.info(f"结果已保存: {out_file}")

if __name__ == "__main__":
    main()
