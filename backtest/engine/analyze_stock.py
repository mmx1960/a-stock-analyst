"""
单只股票缠论分析脚本（供子进程调用）
输出JSON结果到stdout
"""
import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from czsc import CZSC, Freq, format_standard_kline
    CZSC_AVAILABLE = True
except Exception:
    CZSC_AVAILABLE = False


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _r(val, decimals=2):
    return round(_safe_float(val), decimals)


def detect_buy_sell_points(bi_list: list) -> list:
    """检测买点"""
    if len(bi_list) < 3:
        return []
    
    points = []
    recent_bis = bi_list[-6:] if len(bi_list) >= 6 else bi_list
    
    # 1买
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
    
    # 2买
    if len(recent_bis) >= 4:
        for i in range(len(recent_bis) - 2, 1, -1):
            bi = recent_bis[i]
            prev_bi = recent_bis[i-1]
            prev2_bi = recent_bis[i-2]
            if str(bi.direction) == "向下" and str(prev_bi.direction) == "向上":
                if str(prev2_bi.direction) == "向下" and bi.low > prev2_bi.low:
                    points.append({"type": "2买", "price": _r(bi.low), "date": str(bi.sdt), "strength": "normal"})
                    break
    
    # 3买
    if len(recent_bis) >= 4:
        for i in range(len(recent_bis) - 2, 1, -1):
            bi = recent_bis[i]
            if str(bi.direction) == "向上" and str(recent_bis[i-1].direction) == "向下":
                if i >= 2 and str(recent_bis[i-2].direction) == "向上" and bi.low > recent_bis[i-2].high:
                    points.append({"type": "3买", "price": _r(bi.low), "date": str(bi.sdt), "strength": "strong"})
                    break
    
    return points


def get_trend_structure(bi_list: list) -> dict:
    """趋势结构"""
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


def calculate_future_performance(df: pd.DataFrame, buy_date: str, buy_price: float, weeks: int = 30) -> dict:
    """计算未来表现"""
    try:
        from datetime import timedelta
        buy_dt = pd.to_datetime(buy_date)
        future_df = df[df['date'] >= buy_dt].copy()
        
        if future_df.empty or len(future_df) < 5:
            return {"max_return": 0, "status": "数据不足"}
        
        max_days = weeks * 7
        end_date = buy_dt + timedelta(days=max_days)
        future_df = future_df[future_df['date'] <= end_date]
        
        if future_df.empty:
            return {"max_return": 0, "status": "无后续数据"}
        
        max_price = float(future_df['close'].max())
        max_return = (max_price - buy_price) / buy_price * 100
        
        max_idx = future_df['close'].idxmax()
        max_date = future_df.loc[max_idx, 'date']
        days = (max_date - buy_dt).days
        
        last_close = float(future_df.iloc[-1]['close'])
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
    except Exception:
        return {"max_return": 0, "status": "计算失败"}


def analyze_stock(code: str, start_year: int = 2000):
    """分析单只股票"""
    if not CZSC_AVAILABLE:
        print(json.dumps({"error": "CZSC不可用"}))
        return
    
    try:
        # 获取历史数据
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=f"{start_year}0101", end_date=end_date, adjust="qfq"
        )
        
        if df is None or df.empty or len(df) < 200:
            print(json.dumps({"code": code, "error": "数据不足"}))
            return
        
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
        
        # 转换为CZSC格式
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
            print(json.dumps({"code": code, "error": "数据为空"}))
            return
        
        czsc_bars = format_standard_kline(std_df, freq=Freq.D)
        if not czsc_bars:
            print(json.dumps({"code": code, "error": "CZSC转换失败"}))
            return
        
        # CZSC分析
        czsc_obj = CZSC(czsc_bars)
        bi_list = czsc_obj.bi_list
        
        if len(bi_list) < 3:
            print(json.dumps({"code": code, "error": "笔数不足"}))
            return
        
        # 检测买点
        buy_points = detect_buy_sell_points(bi_list)
        buy_only = [bp for bp in buy_points if '买' in bp['type']]
        
        if not buy_only:
            print(json.dumps({"code": code, "total_buy_points": 0, "buy_points": []}))
            return
        
        # 分析每个买点
        buy_reports = []
        for bp in buy_only:
            perf = calculate_future_performance(df, bp['date'], bp['price'], weeks=30)
            trend = get_trend_structure(bi_list)
            
            buy_reports.append({
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
        
        # 统计
        buy_returns = [br['max_return'] for br in buy_reports if br['max_return'] > 0]
        
        result = {
            "code": code,
            "total_buy_points": len(buy_only),
            "buy_points": buy_reports,
            "avg_max_return": _r(np.mean(buy_returns)) if buy_returns else 0,
            "median_max_return": _r(np.median(buy_returns)) if buy_returns else 0,
            "max_return": _r(max(buy_returns)) if buy_returns else 0,
            "win_rate": _r(sum(1 for r in buy_returns if r > 10) / len(buy_returns) * 100) if buy_returns else 0,
            "trend_structure": get_trend_structure(bi_list),
        }
        
        print(json.dumps(result, ensure_ascii=False))
        
    except Exception as e:
        print(json.dumps({"code": code, "error": str(e)}))


if __name__ == "__main__":
    from datetime import datetime
    if len(sys.argv) >= 3:
        code = sys.argv[1]
        start_year = int(sys.argv[2])
        analyze_stock(code, start_year)
    else:
        print(json.dumps({"error": "参数不足"}))
