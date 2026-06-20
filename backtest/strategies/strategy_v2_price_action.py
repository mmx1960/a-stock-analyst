"""
缠论共振选股策略 v2
条件：
1. 周线放量：往前30根K线里，存在至少1根K线成交量 > 前5根均量的1.5倍
2. 周线上涨：往前30根K线，价格趋势向上（分段均价抬高）
3. 日线下跌：往前30根日线，价格趋势向下
4. 日线中枢：日线下跌过程中存在价格重叠区间（缠论中枢雏形）
"""
import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

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
    """条件1：周线放量"""
    if len(df_weekly) < lookback + 5:
        return False
    recent = df_weekly.tail(lookback)
    for i in range(5, len(recent)):
        vol_ma5 = recent['volume'].iloc[i-5:i].mean()
        if vol_ma5 > 0 and recent['volume'].iloc[i] > 1.5 * vol_ma5:
            return True
    return False

def check_weekly_uptrend(df_weekly: pd.DataFrame, lookback=30) -> bool:
    """条件2：周线上涨走势（分段均价抬高）"""
    if len(df_weekly) < lookback:
        return False
    close = df_weekly.tail(lookback)['close'].values
    # 分三段比较均价：近5 vs 中10 vs 远15
    seg1 = close[-5:].mean()
    seg2 = close[-15:-5].mean()
    seg3 = close[-30:-15].mean()
    return seg1 > seg2 > seg3

def check_daily_downtrend(df_daily: pd.DataFrame, lookback=30) -> bool:
    """条件3：日线下跌走势"""
    if len(df_daily) < lookback:
        return False
    close = df_daily.tail(lookback)['close'].values
    seg1 = close[-5:].mean()
    seg2 = close[-15:-5].mean()
    seg3 = close[-30:-15].mean()
    return seg1 < seg2 < seg3

def check_daily_zhongshu(df_daily: pd.DataFrame, lookback=30, min_bars=3) -> bool:
    """条件4：日线中枢（价格重叠区间）"""
    if len(df_daily) < lookback:
        return False
    recent = df_daily.tail(lookback)
    highs = recent['high'].values
    lows = recent['low'].values
    
    # 滑动窗口找至少3根K线价格重叠
    for i in range(len(recent) - min_bars + 1):
        window_h = highs[i:i+min_bars]
        window_l = lows[i:i+min_bars]
        # 中枢条件：最高价的最低值 > 最低价的最高值
        if min(window_h) > max(window_l):
            return True
    return False

def screen_stock(code: str, name: str) -> dict:
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - pd.Timedelta(days=365)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="hfq")
        if df is None or len(df) < 100:
            return None
            
        col_map = {"日期":"date", "开盘":"open", "收盘":"close", "最高":"high", "最低":"low", "成交量":"volume"}
        df = df.rename(columns=col_map)[['date','open','close','high','low','volume']]
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        for c in ['open','close','high','low','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
            
        df_weekly = resample_weekly(df)
        
        c1 = check_weekly_volume(df_weekly)
        c2 = check_weekly_uptrend(df_weekly)
        c3 = check_daily_downtrend(df)
        c4 = check_daily_zhongshu(df)
        
        if c1 and c2 and c3 and c4:
            return {
                "code": code, "name": name,
                "current_price": round(df.iloc[-1]['close'], 2),
                "weekly_vol_spike": True,
                "weekly_uptrend": True,
                "daily_downtrend": True,
                "daily_zhongshu": True,
                "days_data": len(df)
            }
        return None
    except Exception:
        return None

def main():
    logger.info("=== 开始全量选股 (策略 v2) ===")
    out_dir = Path("backtest/screening_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("获取股票列表...")
    df_list = ak.stock_zh_a_spot_em()
    stocks = [(row['代码'], row['名称']) for _, row in df_list.iterrows()]
    logger.info(f"共 {len(stocks)} 只股票")
    
    results = []
    start_time = time.time()
    
    for i, (code, name) in enumerate(stocks):
        res = screen_stock(code, name)
        if res:
            results.append(res)
            logger.info(f"✅ 命中: {code} {name} (现价:{res['current_price']})")
        
        if (i+1) % 100 == 0:
            elapsed = time.time() - start_time
            logger.info(f"进度: {i+1}/{len(stocks)} 耗时:{elapsed:.0f}s 命中:{len(results)}")
            
    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"screening_v2_{timestamp}.json"
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    logger.info(f"\n🎉 筛选完成! 共命中 {len(results)} 只")
    logger.info(f"结果已保存: {out_file}")
    
    for r in results:
        logger.info(f"  {r['code']} {r['name']} 现价:{r['current_price']}")

if __name__ == "__main__":
    main()
