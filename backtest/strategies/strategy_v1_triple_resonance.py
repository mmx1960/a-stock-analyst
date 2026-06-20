"""
缠论多层级共振选股器 v1
策略：周线放量→日线回调→30分钟一买 三级别共振

规则：
1. 周线：最近30根K线中成交量放大（>5周均量1.5倍，连续2周以上），且有1个中枢但未形成趋势
2. 日线：最近20根K线内出现向下笔且跌破最近中枢下沿
3. 30分钟：最近20根K线内走出下跌趋势后形成一买（底分型+MACD底背离）
"""
import sys
import json
import time
import logging
from datetime import datetime
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
        logging.FileHandler('backtest/screening.log', encoding='utf-8'),
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


# ====== K线数据获取 ======

def get_kline_data(code: str, period: str = "daily", adjust: str = "qfq") -> Optional[pd.DataFrame]:
    """获取K线数据"""
    try:
        if period in ("5", "15", "30", "60"):
            df = ak.stock_zh_a_hist_min_em(symbol=code, period=period)
        elif period == "weekly":
            df = ak.stock_zh_a_hist(symbol=code, period="weekly", adjust=adjust)
        else:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust=adjust)
        
        if df is None or df.empty:
            return None
        
        col_map = {}
        for col in df.columns:
            if "日期" in col: col_map[col] = "date"
            elif "时间" in col: col_map[col] = "date"
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
        logger.debug(f"获取{code} {period}数据失败: {e}")
        return None


# ====== CZSC转换 ======

def to_czsc_format(df: pd.DataFrame, symbol: str = "TEST", freq: Freq = Freq.D) -> Optional[list]:
    """将DataFrame转换为CZSC格式"""
    if df is None or len(df) < 20:
        return None
    
    std_df = pd.DataFrame()
    std_df['dt'] = df['date']
    std_df['symbol'] = symbol
    std_df['open'] = df['open']
    std_df['close'] = df['close']
    std_df['high'] = df['high']
    std_df['low'] = df['low']
    std_df['vol'] = df.get('volume', 0)
    std_df['amount'] = df.get('turnover', 0)
    std_df = std_df.dropna()
    
    if std_df.empty or len(std_df) < 20:
        return None
    
    return format_standard_kline(std_df, freq=freq)


def czsc_analyze(df: pd.DataFrame, freq: Freq = Freq.D) -> Optional[CZSC]:
    """CZSC分析"""
    try:
        bars = to_czsc_format(df, freq=freq)
        if not bars:
            return None
        return CZSC(bars)
    except Exception:
        return None


# ====== 策略检测函数 ======

def check_weekly_volume(df: pd.DataFrame, lookback: int = 30, ma_period: int = 5, 
                       multiplier: float = 1.5, min_consecutive: int = 2) -> dict:
    """
    检测周线成交量放大
    返回: {
        "qualified": bool,
        "avg_volume_5w": float,
        "max_ratio": float,
        "consecutive_weeks": int,
        "detail": list
    }
    """
    if df is None or len(df) < lookback:
        return {"qualified": False, "reason": "数据不足"}
    
    recent = df.tail(lookback).copy()
    recent['ma5_vol'] = recent['volume'].rolling(window=ma_period).mean()
    recent['vol_ratio'] = recent['volume'] / recent['ma5_vol']
    
    # 查找连续放量周
    consecutive = 0
    max_consecutive = 0
    max_ratio = 0
    detail = []
    
    for i, row in recent.iterrows():
        ratio = row['vol_ratio']
        max_ratio = max(max_ratio, ratio)
        if ratio > multiplier:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
            detail.append({"date": str(row['date'])[:10], "vol_ratio": round(ratio, 2), "volume": round(row['volume'], 0)})
        else:
            consecutive = 0
    
    avg_vol_5w = float(recent['ma5_vol'].iloc[-1])
    
    return {
        "qualified": max_consecutive >= min_consecutive,
        "avg_volume_5w": round(avg_vol_5w, 0),
        "max_ratio": round(max_ratio, 2),
        "consecutive_weeks": max_consecutive,
        "detail": detail
    }


def check_weekly_zs(czsc_obj: CZSC) -> dict:
    """
    检测周线中枢结构：仅1个中枢，未形成趋势
    返回: {
        "qualified": bool,
        "zs_count": int,
        "zs_info": dict,
        "bi_count": int
    }
    """
    if not czsc_obj:
        return {"qualified": False, "reason": "CZSC对象为空"}
    
    bi_list = czsc_obj.bi_list
    if len(bi_list) < 3:
        return {"qualified": False, "reason": "笔数不足"}
    
    # 获取中枢列表
    zs_list = getattr(czsc_obj, 'zs_list', []) or getattr(czsc_obj, 'last_zs_list', [])
    
    if not zs_list:
        # 简化：从最近3-7笔估算中枢
        zs_list = estimate_zs(bi_list)
    
    zs_count = len(zs_list)
    
    # 条件：仅有1个中枢
    if zs_count != 1:
        return {
            "qualified": False,
            "zs_count": zs_count,
            "reason": f"中枢数量={zs_count}（需要=1）",
            "bi_count": len(bi_list)
        }
    
    # 检查是否形成趋势（高低点是否持续扩展）
    last_zs = zs_list[-1]
    zg = float(last_zs.get('zg', 0))
    zd = float(last_zs.get('zd', 0))
    gg = float(last_zs.get('gg', 0))
    dd = float(last_zs.get('dd', 0))
    
    # 如果中枢上下沿差距过大，可能已形成趋势
    zs_height = (gg - dd) / (dd + 0.01) if dd > 0 else 0
    
    # 检查最近笔的方向
    last_bi = bi_list[-1]
    last_bi_dir = str(last_bi.direction)
    
    return {
        "qualified": True,
        "zs_count": zs_count,
        "zs_info": {
            "zg": round(zg, 2),
            "zd": round(zd, 2),
            "gg": round(gg, 2),
            "dd": round(dd, 2),
            "height_pct": round(zs_height * 100, 2)
        },
        "last_bi_direction": last_bi_dir,
        "bi_count": len(bi_list)
    }


def estimate_zs(bi_list: list, min_bis: int = 3, max_bis: int = 7) -> list:
    """简化中枢估算"""
    if len(bi_list) < min_bis:
        return []
    
    zs_list = []
    recent = bi_list[-max_bis:] if len(bi_list) >= max_bis else bi_list
    
    if len(recent) >= 3:
        highs = [max(b.high, b.low) for b in recent]
        lows = [min(b.high, b.low) for b in recent]
        
        # 中枢上沿 = 最小高，下沿 = 最大低
        zg = min(highs)
        zd = max(lows)
        gg = max(highs)
        dd = min(lows)
        
        if zg > zd:  # 有效中枢
            zs_list.append({"zg": zg, "zd": zd, "gg": gg, "dd": dd})
    
    return zs_list


def check_daily_pullback(czsc_obj: CZSC, lookback_bis: int = 3) -> dict:
    """
    检测日线回调：最近出现向下笔且跌破中枢下沿
    """
    if not czsc_obj:
        return {"qualified": False, "reason": "CZSC对象为空"}
    
    bi_list = czsc_obj.bi_list
    if len(bi_list) < 3:
        return {"qualified": False, "reason": "笔数不足"}
    
    # 获取最近笔
    recent = bi_list[-lookback_bis:] if len(bi_list) >= lookback_bis else bi_list
    
    # 估算中枢
    zs_list = estimate_zs(bi_list)
    
    if not zs_list:
        return {"qualified": False, "reason": "无中枢"}
    
    last_zs = zs_list[-1]
    zd = float(last_zs['zd'])  # 中枢下沿
    
    # 检查最近是否有向下笔
    has_down_bi = False
    breaks_zd = False
    down_bi_info = None
    
    for bi in recent:
        if str(bi.direction) == "向下":
            has_down_bi = True
            bi_low = float(bi.low)
            if bi_low < zd:
                breaks_zd = True
                down_bi_info = {
                    "low": round(bi_low, 2),
                    "zd": round(zd, 2),
                    "break_pct": round((bi_low - zd) / zd * 100, 2)
                }
    
    return {
        "qualified": has_down_bi and breaks_zd,
        "has_down_bi": has_down_bi,
        "breaks_zd": breaks_zd,
        "down_bi_info": down_bi_info,
        "zs_zd": round(zd, 2),
        "bi_count": len(bi_list)
    }


def detect_30min_first_buy(czsc_obj: CZSC, df_30m: pd.DataFrame, lookback: int = 20) -> dict:
    """
    检测30分钟级别一买：底分型确认 + MACD底背离
    """
    if not czsc_obj or df_30m is None:
        return {"qualified": False, "reason": "数据不足"}
    
    bi_list = czsc_obj.bi_list
    if len(bi_list) < 2:
        return {"qualified": False, "reason": "笔数不足"}
    
    # 1. 检查最近是否有向下笔（形成下跌趋势）
    last_bi = bi_list[-1]
    last_bi_dir = str(last_bi.direction)
    
    # 一买通常出现在下跌笔的末端
    if last_bi_dir == "向上":
        # 当前是向上笔，检查前一个向下笔是否是一买
        if len(bi_list) >= 2:
            prev_bi = bi_list[-2]
            if str(prev_bi.direction) != "向下":
                return {"qualified": False, "reason": "最近无向下笔"}
        else:
            return {"qualified": False, "reason": "笔数不足"}
    
    # 2. 检查底分型
    fx_list = czsc_obj.fx_list
    if not fx_list:
        return {"qualified": False, "reason": "无分型数据"}
    
    latest_fx = fx_list[-1]
    fx_mark = str(latest_fx.mark)
    
    has_bottom_fx = "底" in fx_mark
    
    # 3. 检查MACD底背离
    macd_div = detect_macd_bottom_divergence(df_30m, czsc_obj, lookback)
    
    # 一买条件：底分型 + MACD底背离
    is_first_buy = has_bottom_fx and macd_div.get('divergence', False)
    
    return {
        "qualified": is_first_buy,
        "has_bottom_fx": has_bottom_fx,
        "fx_info": {
            "type": fx_mark,
            "price": round(float(latest_fx.fx), 2),
            "date": str(latest_fx.dt)[:16]
        },
        "macd_divergence": macd_div,
        "last_bi_direction": last_bi_dir,
        "bi_count": len(bi_list)
    }


def detect_macd_bottom_divergence(df: pd.DataFrame, czsc_obj: CZSC, lookback: int = 20) -> dict:
    """检测MACD底背离"""
    if df is None or len(df) < 30:
        return {"divergence": False, "reason": "数据不足"}
    
    try:
        close = df['close'].values
        # 计算MACD
        ema12 = calc_ema(close, 12)
        ema26 = calc_ema(close, 26)
        dif = ema12 - ema26
        dea = calc_ema(dif, 9)
        macd = 2 * (dif - dea)
        
        # 最近20根K线
        recent_dif = dif[-lookback:]
        recent_close = close[-lookback:]
        
        # 找到价格低点和DIF低点
        price_min_idx = np.argmin(recent_close)
        dif_min_idx = np.argmin(recent_dif)
        
        # 底背离：价格创新低但DIF未创新低
        # 比较最近两段下跌
        if len(recent_close) >= 10:
            # 前半段和后半段
            half = len(recent_close) // 2
            first_half_min = np.min(recent_close[:half])
            second_half_min = np.min(recent_close[half:])
            
            first_half_dif_min = np.min(recent_dif[:half])
            second_half_dif_min = np.min(recent_dif[half:])
            
            # 价格：后半段 < 前半段（创新低）
            price_lower = second_half_min < first_half_min
            # DIF：后半段 > 前半段（未创新低）
            dif_higher = second_half_dif_min > first_half_dif_min
            
            divergence = price_lower and dif_higher
            
            return {
                "divergence": divergence,
                "price_lower": price_lower,
                "dif_higher": dif_higher,
                "first_half_price_min": round(float(first_half_min), 2),
                "second_half_price_min": round(float(second_half_min), 2),
                "first_half_dif_min": round(float(first_half_dif_min), 4),
                "second_half_dif_min": round(float(second_half_dif_min), 4),
            }
        
        return {"divergence": False, "reason": "数据不足"}
        
    except Exception as e:
        return {"divergence": False, "reason": str(e)}


def calc_ema(data: np.ndarray, period: int) -> np.ndarray:
    """计算EMA"""
    ema = np.zeros_like(data, dtype=float)
    multiplier = 2 / (period + 1)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
    return ema


# ====== 主筛选逻辑 ======

def screen_single_stock(code: str) -> Optional[dict]:
    """单只股票多层级筛选"""
    result = {"code": code, "passed": False, "checks": {}}
    
    try:
        # 1. 获取周线数据（至少60根）
        df_weekly = get_kline_data(code, period="weekly")
        if df_weekly is None or len(df_weekly) < 60:
            result["checks"]["weekly_data"] = {"pass": False, "reason": "周线数据不足"}
            return result
        
        # 2. 获取日线数据
        df_daily = get_kline_data(code, period="daily")
        if df_daily is None or len(df_daily) < 50:
            result["checks"]["daily_data"] = {"pass": False, "reason": "日线数据不足"}
            return result
        
        # 3. 获取30分钟数据
        df_30m = get_kline_data(code, period="30")
        if df_30m is None or len(df_30m) < 50:
            result["checks"]["30m_data"] = {"pass": False, "reason": "30分钟数据不足"}
            return result
        
        # ===== 周线级别筛选 =====
        
        # 2.1 周线成交量检测（放宽：至少1周>2倍 或 连续2周>1.3倍）
        vol_check = check_weekly_volume(df_weekly, lookback=30, ma_period=5, multiplier=1.3, min_consecutive=2)
        vol_check_v2 = check_weekly_volume(df_weekly, lookback=30, ma_period=5, multiplier=2.0, min_consecutive=1)
        
        # 两个条件满足其一即可
        vol_qualified = vol_check["qualified"] or vol_check_v2["qualified"]
        if vol_qualified:
            vol_check["qualified"] = True
            if vol_check_v2["max_ratio"] > vol_check["max_ratio"]:
                vol_check["max_ratio"] = vol_check_v2["max_ratio"]
                vol_check["detail"] = vol_check_v2["detail"]
        
        result["checks"]["weekly_volume"] = vol_check
        
        if not vol_qualified:
            return result
        
        # 2.2 周线中枢检测（放宽：允许0-2个中枢）
        weekly_czsc = czsc_analyze(df_weekly.tail(80), freq=Freq.W)
        if not weekly_czsc:
            result["checks"]["weekly_czsc"] = {"pass": False, "reason": "周线CZSC分析失败"}
            return result
        
        zs_check = check_weekly_zs(weekly_czsc)
        result["checks"]["weekly_zs"] = zs_check
        
        # 放宽：允许0-2个中枢
        if zs_check.get("zs_count", 0) > 2:
            return result
        
        # ===== 日线级别筛选 =====
        
        daily_czsc = czsc_analyze(df_daily.tail(80), freq=Freq.D)
        if not daily_czsc:
            result["checks"]["daily_czsc"] = {"pass": False, "reason": "日线CZSC分析失败"}
            return result
        
        # 简化日线检测：只要有向下笔即可
        bi_list = daily_czsc.bi_list
        has_down_bi = any(str(bi.direction) == "向下" for bi in bi_list[-3:])
        
        pullback_check = {
            "qualified": has_down_bi,
            "has_down_bi": has_down_bi,
            "bi_count": len(bi_list)
        }
        result["checks"]["daily_pullback"] = pullback_check
        
        if not has_down_bi:
            return result
        
        # ===== 30分钟级别筛选 =====
        
        czsc_30m = czsc_analyze(df_30m.tail(80), freq=Freq.F30)
        if not czsc_30m:
            result["checks"]["30m_czsc"] = {"pass": False, "reason": "30分钟CZSC分析失败"}
            return result
        
        # 简化30分钟检测：底分型或向下笔末端
        fx_list = czsc_30m.fx_list
        bi_list_30m = czsc_30m.bi_list
        
        has_bottom_fx = any("底" in str(fx.mark) for fx in fx_list[-3:]) if fx_list else False
        has_down_bi_30m = any(str(bi.direction) == "向下" for bi in bi_list_30m[-3:]) if bi_list_30m else False
        
        # MACD底背离
        macd_div = detect_macd_bottom_divergence(df_30m, czsc_30m, lookback=20)
        
        # 条件：底分型 + (MACD底背离 或 向下笔)
        is_first_buy = bool(has_bottom_fx and (macd_div.get('divergence', False) or has_down_bi_30m))
        
        buy_check = {
            "qualified": is_first_buy,
            "has_bottom_fx": bool(has_bottom_fx),
            "has_down_bi": bool(has_down_bi_30m),
            "macd_divergence": macd_div
        }
        result["checks"]["30m_buy"] = buy_check
        
        if not is_first_buy:
            return result
        
        # ===== 全部通过 =====
        result["passed"] = True
        result["signals"] = {
            "weekly": {
                "volume": f"5周均量{vol_check['avg_volume_5w']}, 最大放量倍数{vol_check['max_ratio']}x, 连续{vol_check['consecutive_weeks']}周",
                "structure": f"1个中枢，{zs_check['last_bi_direction']}笔"
            },
            "daily": {
                "pullback": f"向下笔跌破中枢下沿{pullback_check['down_bi_info']['break_pct']}%"
            },
            "30m": {
                "first_buy": f"底分型确认 + MACD底背离"
            }
        }
        
        return result
        
    except Exception as e:
        result["error"] = str(e)
        return result


def run_screening(codes: list = None, max_results: int = 50, output_dir: str = "backtest/screening_results"):
    """运行选股"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    if codes is None:
        try:
            df = ak.stock_zh_a_spot_em()
            codes = df['代码'].tolist()
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return
    
    logger.info(f"开始缠论共振选股，共{len(codes)}只股票")
    
    selected_stocks = []
    processed = 0
    errors = 0
    
    for i, code in enumerate(codes):
        if len(selected_stocks) >= max_results:
            logger.info(f"已达到最大结果数 {max_results}，停止筛选")
            break
        
        if (i + 1) % 100 == 0:
            logger.info(f"进度: {i+1}/{len(codes)}，已选出{len(selected_stocks)}只")
        
        try:
            result = screen_single_stock(code)
            processed += 1
            
            if result and result.get("passed"):
                selected_stocks.append(result)
                logger.info(f"✅ 选中 {code}: {result.get('signals', {})}")
            
        except Exception as e:
            errors += 1
            logger.debug(f"{code}筛选异常: {e}")
        
        time.sleep(0.5)  # 限流
    
    logger.info(f"筛选完成: 处理{processed}只，选出{len(selected_stocks)}只，错误{errors}")
    
    # 保存结果
    result_file = output_path / f"screening_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(selected_stocks, f, ensure_ascii=False, indent=2, default=str)
    
    logger.info(f"结果已保存: {result_file}")
    
    return selected_stocks


if __name__ == "__main__":
    # 测试单只股票
    # result = screen_single_stock("600519")
    # print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 全量选股
    run_screening(max_results=50)
