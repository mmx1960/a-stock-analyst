"""
缠论类买点筛选 V4 (Robust Version)
核心逻辑：
1. 周线趋势向上 (周线收盘价 > 20 周均线)
2. 日线发生回调 (近 20 天内有 >5% 的下跌)
3. 企稳确认 (当前价格在 20 天低点附近 3% 以内)
4. MACD 动能衰竭 (MACD 绿柱缩短 或 DIF 拐头)
"""
import json, time, logging, warnings
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import akshare as ak

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def get_data(code):
    start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="hfq")
    if df is None or len(df) < 100: return None
    df = df.rename(columns={"日期":"date", "收盘":"close", "最高":"high", "最低":"low", "成交量":"volume"})[['date','close','high','low','volume']]
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df

def calc_macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = 2 * (dif - dea)
    return macd, dif

def check_v4(df):
    close = df['close']
    high = df['high']
    low = df['low']
    
    # 1. Weekly Trend (approximated by Weekly Close > MA20 Weekly)
    # Calculate weekly close
    weekly = df.set_index('date').resample('W-FRI')['close'].last().dropna()
    if len(weekly) < 30: return False
    ma20_w = weekly.rolling(20).mean().iloc[-1]
    if weekly.iloc[-1] < ma20_w: return False
    
    # 2. Pullback (Lowest in last 20 days is > 5% below Highest in last 40 days)
    high_40 = high.tail(40).max()
    low_20 = low.tail(20).min()
    pullback = (high_40 - low_20) / high_40
    if pullback < 0.05: return False
    
    # 3. Stabilization (Current close is near the 20-day low, within 5%)
    current = close.iloc[-1]
    if current > low_20 * 1.05: return False # Already rebounded too much
    if current < low_20: return False # Still falling
    
    # 4. MACD Check (Green bars shrinking OR MACD is just crossing/turning)
    macd, dif = calc_macd(close)
    # Relaxed: allow if MACD is flat or shrinking, don't strictly require shrinking if price is at bottom
    if macd.iloc[-1] < 0 and macd.iloc[-1] < macd.iloc[-3]: return False # Accelerating down
    
    return True

def main():
    logger.info("=== 缠论类买点筛选 V4 (Robust) ===")
    df_list = ak.stock_zh_a_spot_em()
    df_list = df_list.sort_values('成交额', ascending=False).head(500)
    stocks = [(r['代码'], r['名称'], r['最新价'], r['涨跌幅']) for _, r in df_list.iterrows()]
    logger.info(f"扫描 {len(stocks)} 只活跃股...")
    
    results = []
    start_time = time.time()
    
    for i, (code, name, price, chg) in enumerate(stocks):
        df = get_data(code)
        if df is None: continue
        if check_v4(df):
            # Find the low point
            low_20 = df['low'].tail(20).min()
            low_idx = df['low'].tail(20).idxmin()
            low_date = df.loc[low_idx, 'date']
            days_ago = (datetime.now() - low_date).days
            
            results.append({
                'code': code, 'name': name, 'price': price, 'chg': chg,
                'low_date': str(low_date)[:10], 'days_ago': days_ago,
                'drop_from_high': round((df['high'].tail(40).max() - low_20)/df['high'].tail(40).max()*100, 1)
            })
            logger.info(f"🎯 {code} {name} 现价:{price} 跌幅:{results[-1]['drop_from_high']}% 企稳日:{results[-1]['low_date']}")
            
        if (i+1) % 50 == 0:
            logger.info(f"进度: {i+1}/{len(stocks)} 耗时:{time.time()-start_time:.0f}s 命中:{len(results)}")
        time.sleep(0.2)
        
    logger.info(f"\n完成! 耗时 {time.time()-start_time:.0f}s, 共 {len(results)} 只")
    with open('current_v3_selections.json', 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
