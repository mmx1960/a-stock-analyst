"""
实时选股：缠论 v3 策略
筛选标准：
1. 周线向上笔 (大级别多头)
2. 日线向下笔结束 (回调到位)
3. 日线笔背驰 (MACD 面积或 DIF)
4. 买点确认：发生在最近 7 天内
"""
import os, sys, json, time, logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import akshare as ak

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def calc_macd(df):
    close = df['close']
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = 2 * (dif - dea)
    df = df.copy()
    df['dif'], df['dea'], df['macd'] = dif, dea, macd
    return df

def find_fenxing(df):
    highs, lows = df['high'].values, df['low'].values
    n = len(df)
    fenxing = []
    for i in range(2, n-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
            fenxing.append((i, 'D', highs[i], lows[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
            fenxing.append((i, 'B', highs[i], lows[i]))
    return fenxing

def form_bis(fenxing):
    if not fenxing: return []
    bis = []
    start = None
    for i, fx in enumerate(fenxing):
        if fx[1] == 'B':
            start = i
            break
    if start is None: return []
    current = fenxing[start]
    for i in range(start+1, len(fenxing)):
        nxt = fenxing[i]
        if (current[1] == 'B' and nxt[1] == 'D') or (current[1] == 'D' and nxt[1] == 'B'):
            if nxt[0] - current[0] >= 3:
                bis.append((current[0], nxt[0], current[1], nxt[1]))
                current = nxt
    return bis

def calc_green_area(df, start, end):
    sub = df.iloc[start:end+1]
    green = sub[sub['macd'] < 0]['macd'].sum()
    return abs(green)

def check_current_signal(code):
    try:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="hfq")
        if df is None or len(df) < 150: return None
        
        df = df.rename(columns={"日期":"date", "开盘":"open", "收盘":"close", "最高":"high", "最低":"low", "成交量":"volume"})[['date','open','close','high','low','volume']]
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        for c in ['open','close','high','low','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        
        df_w = df.set_index('date').resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['close']).reset_index()
        
        df = calc_macd(df)
        df_w = calc_macd(df_w)
        
        fx_w = find_fenxing(df_w)
        bis_w = form_bis(fx_w)
        fx_d = find_fenxing(df)
        bis_d = form_bis(fx_d)
        
        if not bis_w or len(bis_d) < 2: return None
        
        # 1. Weekly Upward
        last_bi_w = bis_w[-1]
        # Check if last stroke is Up (B->D).
        # Allow case where weekly is just starting a Down stroke but daily is at bottom? 
        # Strict v3 says Weekly Up.
        if not (last_bi_w[2] == 'B' and last_bi_w[3] == 'D'):
            return None
            
        # 2. Daily Downward + Divergence
        down_bis = [b for b in bis_d if b[2] == 'D' and b[3] == 'B']
        if len(down_bis) < 2: return None
        
        last_bi = down_bis[-1]
        prev_bi = down_bis[-2]
        
        area_last = calc_green_area(df, last_bi[0], last_bi[1])
        area_prev = calc_green_area(df, prev_bi[0], prev_bi[1])
        price_last = df.iloc[last_bi[1]]['low']
        price_prev = df.iloc[prev_bi[1]]['low']
        
        cond1 = price_last <= price_prev and area_last < area_prev * 0.8
        
        dif_min_last = df.iloc[last_bi[0]:last_bi[1]+1]['dif'].min()
        dif_min_prev = df.iloc[prev_bi[0]:prev_bi[1]+1]['dif'].min()
        cond2 = price_last <= price_prev and dif_min_last > dif_min_prev
        
        if not (cond1 or cond2): return None
        
        # 3. Timing: Buy point within last 7 days
        buy_date = df.iloc[last_bi[1]]['date']
        days_ago = (datetime.now() - buy_date).days
        if 0 <= days_ago <= 7:
            buy_price = df.iloc[last_bi[1]]['close']
            return {
                'code': code,
                'buy_date': str(buy_date)[:10],
                'price': buy_price,
                'days_ago': days_ago,
            }
        return None
    except Exception:
        return None

def run_batch(stocks_batch):
    """Run screening on a batch of stocks"""
    local_results = []
    for code, name in stocks_batch:
        res = check_current_signal(code)
        if res:
            res['name'] = name
            local_results.append(res)
    return local_results

def main():
    logger.info("=== 缠论 v3 实时选股 ===")
    # 1. Fetch list
    df_list = ak.stock_zh_a_spot_em()
    stocks = [(r['代码'], r['名称']) for _, r in df_list.iterrows()]
    logger.info(f"Total stocks: {len(stocks)}")
    
    # 2. Split into batches
    batch_size = 200
    batches = [stocks[i:i + batch_size] for i in range(0, len(stocks), batch_size)]
    logger.info(f"Split into {len(batches)} batches")
    
    # 3. Run in parallel
    results = []
    start_time = time.time()
    
    # Use ThreadPoolExecutor for IO bound tasks
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(run_batch, batch): i for i, batch in enumerate(batches)}
        
        for future in as_completed(futures):
            idx = futures[future]
            try:
                res = future.result()
                results.extend(res)
                logger.info(f"✅ 批次 {idx+1}/{len(batches)} 完成 (命中: {len(res)})")
            except Exception as e:
                logger.error(f"批次 {idx} 失败: {e}")
            
            # Progress
            done = sum(1 for f in as_completed(futures) if f.done()) # This is tricky, let's just count results length roughly or use a counter
            # Actually as_completed yields as they finish.
            # We can just log after each batch.
            
    logger.info(f"\n完成! 总耗时 {time.time()-start_time:.0f}s, 共找到 {len(results)} 只")
    for r in sorted(results, key=lambda x: x['days_ago']):
        logger.info(f"  {r['code']} {r['name']} 现价:{r['price']} 买点日:{r['buy_date']} (距今{r['days_ago']}天)")
        
    with open('current_v3_selections.json', 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
