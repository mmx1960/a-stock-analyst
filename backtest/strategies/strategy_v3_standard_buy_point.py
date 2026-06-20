"""
缠论标准买点策略 v3
核心：周线向上背景 + 日线向下笔背驰 + 底分型确认
"""
import os, sys, json, time, logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import akshare as ak
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ====== 缠论基础工具 ======
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
    """找顶底分型 (简化版，不处理包含关系，用3根K线极值判断)"""
    highs, lows = df['high'].values, df['low'].values
    closes = df['close'].values
    n = len(df)
    fenxing = []  # (index, type, high, low)
    for i in range(2, n-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
            fenxing.append((i, 'D', highs[i], lows[i])) # 顶
        if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
            fenxing.append((i, 'B', highs[i], lows[i])) # 底
    return fenxing

def form_bis(fenxing):
    """分型成笔 (简化：相邻顶底交替)"""
    if not fenxing: return []
    bis = []
    # 找第一个有效的起始分型
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
            # 必须满足笔的基本长度 (至少3根K线间隔)
            if nxt[0] - current[0] >= 3:
                bis.append((current[0], nxt[0], current[1], nxt[1])) # start_idx, end_idx, start_type, end_type
                current = nxt
    return bis

def check_bi_bichi(df, bis):
    """判断笔背驰：比较相邻同向笔的MACD面积"""
    # 只检查最近的向下笔 (end_type == 'B' 的笔其实是向上笔? 定义: B->D是向上笔, D->B是向下笔)
    # 我们的 bis 记录的是 (start_fx, end_fx, start_type, end_type)
    # D->B 表示从顶分型到底分型，是向下笔。
    down_bis = [b for b in bis if b[2] == 'D' and b[3] == 'B']
    if len(down_bis) < 2:
        return False
        
    last_bi = down_bis[-1]
    prev_bi = down_bis[-2]
    
    # 计算 MACD 绿柱面积
    def calc_green_area(df, start, end):
        sub = df.iloc[start:end+1]
        green = sub[sub['macd'] < 0]['macd'].sum()
        return abs(green)
        
    area_last = calc_green_area(df, last_bi[0], last_bi[1])
    area_prev = calc_green_area(df, prev_bi[0], prev_bi[1])
    
    # 价格创新低但面积缩小 = 背驰
    price_last = df.iloc[last_bi[1]]['low']
    price_prev = df.iloc[prev_bi[1]]['low']
    
    return price_last <= price_prev and area_last < area_prev * 0.8

def check_dif_bichi(df, bis):
    """黄白线背驰辅助判断"""
    down_bis = [b for b in bis if b[2] == 'D' and b[3] == 'B']
    if len(down_bis) < 2: return False
    
    last_bi = down_bis[-1]
    prev_bi = down_bis[-2]
    
    dif_min_last = df.iloc[last_bi[0]:last_bi[1]+1]['dif'].min()
    dif_min_prev = df.iloc[prev_bi[0]:prev_bi[1]+1]['dif'].min()
    
    price_last = df.iloc[last_bi[1]]['low']
    price_prev = df.iloc[prev_bi[1]]['low']
    
    return price_last <= price_prev and dif_min_last > dif_min_prev

# ====== 策略逻辑 ======
def get_stock_history(code, start="20190101"):
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=datetime.now().strftime("%Y%m%d"), adjust="hfq")
        if df is None or len(df) < 100: return pd.DataFrame()
        df = df.rename(columns={"日期":"date", "开盘":"open", "收盘":"close", "最高":"high", "最低":"low", "成交量":"volume"})[['date','open','close','high','low','volume']]
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        for c in ['open','close','high','low','volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        return df
    except: return pd.DataFrame()

def resample_weekly(df):
    if len(df) < 50: return pd.DataFrame()
    w = df.set_index('date').resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['close']).reset_index()
    return w

def screen_buy_points(df_daily, df_weekly):
    """
    返回买点列表 [(buy_date, buy_price)]
    条件：
    1. 周线处于向上笔
    2. 日线向下笔结束
    3. 日线笔背驰 (MACD面积或黄白线)
    4. 底分型确认
    """
    if len(df_daily) < 150 or len(df_weekly) < 60: return []
    
    df_daily = calc_macd(df_daily)
    df_weekly = calc_macd(df_weekly)
    
    fx_w = find_fenxing(df_weekly)
    bis_w = form_bis(fx_w)
    fx_d = find_fenxing(df_daily)
    bis_d = form_bis(fx_d)
    
    if not bis_w or not bis_d: return []
    
    # 1. 周线条件：最后一笔为向上笔 (B->D) 或 整体趋势向上
    last_bi_w = bis_w[-1]
    if not (last_bi_w[2] == 'B' and last_bi_w[3] == 'D'):
        # 允许周线处于向上笔的延伸中，或者最近3笔重心抬高
        if len(bis_w) >= 3:
            lows = [df_weekly.iloc[b[1]]['low'] for b in bis_w[-3:] if b[3]=='B']
            if len(lows)>=2 and lows[-1] < lows[-2]: return [] # 重心下降，趋势走坏
        else: return []

    buy_points = []
    last_buy_date = None
    
    # 遍历日线笔，找向下笔结束点
    for i, bi in enumerate(bis_d):
        # D->B 是向下笔
        if bi[2] == 'D' and bi[3] == 'B':
            bi_end_idx = bi[1]
            bi_start_idx = bi[0]
            
            # 避免太近
            if last_buy_date and (df_daily.iloc[bi_end_idx]['date'] - last_buy_date).days < 20:
                continue
                
            # 检查背驰
            has_bichi = check_bi_bichi(df_daily, bis_d[:i+1]) or check_dif_bichi(df_daily, bis_d[:i+1])
            if not has_bichi:
                continue
                
            # 检查底分型确认 (笔结束点附近必须有底分型)
            # 笔的结束点本身就是底分型，但我们需要确认它没有被跌破，或者出现了更强的底分型
            # 简化：笔结束后，出现一根K线收盘高于笔内某高点，或者笔内出现标准底分型
            # 这里我们直接以笔的结束点（底分型）作为买点，但要求笔的幅度>3% 且长度>=4根K线
            bi_len = bi_end_idx - bi_start_idx
            bi_drop = (df_daily.iloc[bi_start_idx]['high'] - df_daily.iloc[bi_end_idx]['low']) / df_daily.iloc[bi_start_idx]['high']
            
            if bi_len >= 4 and bi_drop > 0.03:
                buy_date = df_daily.iloc[bi_end_idx]['date']
                buy_price = df_daily.iloc[bi_end_idx]['close']
                buy_points.append((buy_date, buy_price))
                last_buy_date = buy_date
                
    return buy_points

def calc_return(df, buy_date, buy_price, weeks=10):
    future = df[df['date'] > buy_date].copy()
    if len(future) < 5: return 0, "数据不足"
    end_date = buy_date + timedelta(days=weeks*7)
    future = future[future['date'] <= end_date]
    if future.empty: return 0, "无后续"
    max_ret = (future['close'].max() - buy_price) / buy_price * 100
    return round(max_ret, 2), "OK"

def backtest_random_100():
    logger.info("=== 缠论 v3 回测开始 ===")
    df_list = ak.stock_zh_a_spot_em()
    all_stocks = [(r['代码'], r['名称']) for _, r in df_list.iterrows()]
    sample = random.sample(all_stocks, 100)
    
    results = []
    start_time = time.time()
    for i, (code, name) in enumerate(sample):
        df_d = get_stock_history(code)
        if df_d.empty: continue
        df_w = resample_weekly(df_d)
        
        buys = screen_buy_points(df_d, df_w)
        if not buys: continue
        
        valid_returns = []
        details = []
        for bd, bp in buys:
            ret, st = calc_return(df_d, bd, bp, 10)
            if st == "OK":
                valid_returns.append(ret)
                details.append({"date": str(bd)[:10], "price": bp, "ret": ret})
                
        if valid_returns:
            avg = round(np.mean(valid_returns), 2)
            win_rate = round(sum(1 for r in valid_returns if r > 10) / len(valid_returns) * 100, 1)
            results.append({"code": code, "name": name, "bps": len(valid_returns), "avg": avg, "win_rate": win_rate, "details": details})
            logger.info(f"✅ {code} {name} | 买点:{len(valid_returns)} | 平均:{avg}% | 胜率:{win_rate}%")
            
        if (i+1)%20 == 0:
            logger.info(f"进度: {i+1}/100 耗时:{time.time()-start_time:.0f}s 有效:{len(results)}")
            
    # 统计
    all_rets = [d['ret'] for r in results for d in r['details']]
    report = {
        "sample": 100, "valid": len(results),
        "total_signals": len(all_rets),
        "avg": round(np.mean(all_rets), 2) if all_rets else 0,
        "median": round(np.median(all_rets), 2) if all_rets else 0,
        "max": round(max(all_rets), 2) if all_rets else 0,
        "win_rate": round(sum(1 for r in all_rets if r > 10) / len(all_rets) * 100, 1) if all_rets else 0,
        "details": results
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path("backtest/results_v6") / f"czsc_v3_backtest_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    
    logger.info(f"\n=== 报告 ===\n信号:{report['total_signals']} | 平均:{report['avg']}% | 胜率:{report['win_rate']}%")

if __name__ == "__main__":
    backtest_random_100()
