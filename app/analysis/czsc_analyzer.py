"""
缠中说禅（CZSC）分析模块
将 AKShare 数据转换为 CZSC 格式，进行缠论分析
"""
import logging
import numpy as np
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# CZSC 动态导入（支持 Rust/Python 混合架构）
try:
    from czsc import CZSC, Freq, format_standard_kline
    CZSC_AVAILABLE = True
except ImportError as e:
    logger.warning(f"CZSC 导入失败: {e}")
    CZSC_AVAILABLE = False


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _r(val, decimals=2):
    return round(_safe_float(val), decimals)


class CZSCAnalyzer:
    """缠中说禅技术分析器"""

    def analyze(self, df: pd.DataFrame, freq: str = "日线") -> Optional[dict]:
        if not CZSC_AVAILABLE:
            return None
        if df is None or df.empty or len(df) < 20:
            return None

        try:
            czsc_bars = self._to_czsc_format(df, freq)
            if czsc_bars is None or len(czsc_bars) == 0:
                return None

            czsc_obj = CZSC(czsc_bars)

            return {
                "available": True,
                "freq": freq,
                "bar_count": len(czsc_bars),
                "fx_analysis": self._analyze_fx(czsc_obj),
                "bi_analysis": self._analyze_bi(czsc_obj),
                "zs_analysis": self._analyze_zs(czsc_obj),
                "trend_judgment": self._judge_trend(czsc_obj),
                "buy_sell_points": self._detect_buy_sell_points(czsc_obj),
                "macd_divergence": self._detect_macd_divergence(df, czsc_obj),
                "signals": self._extract_signals(czsc_obj),
                "summary": self._generate_summary(czsc_obj),
            }
        except Exception as e:
            logger.error(f"CZSC 分析失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _to_czsc_format(self, df: pd.DataFrame, freq: str) -> Optional[list]:
        std_df = pd.DataFrame()
        if "date" in df.columns:
            std_df["dt"] = pd.to_datetime(df["date"])
        elif "datetime" in df.columns:
            std_df["dt"] = pd.to_datetime(df["datetime"])
        else:
            return None

        symbol_col = "股票代码" if "股票代码" in df.columns else None
        if symbol_col:
            symbol = str(df[symbol_col].iloc[0]) if len(df) > 0 else "UNKNOWN"
        else:
            symbol = "UNKNOWN"
        std_df["symbol"] = symbol
        std_df["open"] = pd.to_numeric(df["open"], errors="coerce")
        std_df["close"] = pd.to_numeric(df["close"], errors="coerce")
        std_df["high"] = pd.to_numeric(df["high"], errors="coerce")
        std_df["low"] = pd.to_numeric(df["low"], errors="coerce")
        std_df["vol"] = pd.to_numeric(df.get("volume", df.get("vol", 0)), errors="coerce")
        std_df["amount"] = pd.to_numeric(df.get("turnover", df.get("amount", 0)), errors="coerce")
        std_df = std_df.dropna()

        if std_df.empty:
            return None

        freq_map = {
            "日线": Freq.D, "D": Freq.D, "daily": Freq.D,
            "60分钟": Freq.F60, "30分钟": Freq.F30, "15分钟": Freq.F15,
            "5分钟": Freq.F5, "1分钟": Freq.F1,
            "周线": Freq.W, "月线": Freq.M,
        }
        czsc_freq = freq_map.get(freq, Freq.D)
        return format_standard_kline(std_df, freq=czsc_freq)

    def _analyze_fx(self, czsc_obj) -> dict:
        fx_list = czsc_obj.fx_list
        if not fx_list:
            return {"count": 0, "latest": None, "recent": []}

        recent_fx = []
        for fx in fx_list[-8:]:
            recent_fx.append({
                "type": str(fx.mark),  # 顶分型/底分型
                "price": _r(fx.fx),
                "date": str(fx.dt),
                "high": _r(fx.high),
                "low": _r(fx.low),
                "elements": len(fx.elements) if hasattr(fx, 'elements') else 0,
            })

        return {
            "count": len(fx_list),
            "latest": recent_fx[-1] if recent_fx else None,
            "recent": recent_fx,
        }

    def _analyze_bi(self, czsc_obj) -> dict:
        bi_list = czsc_obj.bi_list
        if not bi_list:
            return {"count": 0, "latest": None, "recent": [], "trend": "unknown"}

        recent_bis = []
        for bi in bi_list[-8:]:
            recent_bis.append(self._bi_to_dict(bi))

        last_bi = bi_list[-1]
        is_up = str(last_bi.direction) == "向上"

        return {
            "count": len(bi_list),
            "latest": self._bi_to_dict(last_bi),
            "recent": recent_bis,
            "trend": "向上笔" if is_up else "向下笔",
            "is_up": is_up,
        }

    def _bi_to_dict(self, bi) -> dict:
        """将 BI 对象转换为纯 Python dict"""
        fx_a_price = _safe_float(bi.fx_a.fx) if hasattr(bi, 'fx_a') and hasattr(bi.fx_a, 'fx') else 0
        fx_b_price = _safe_float(bi.fx_b.fx) if hasattr(bi, 'fx_b') and hasattr(bi.fx_b, 'fx') else 0
        sdt = str(bi.sdt) if hasattr(bi, 'sdt') else ""
        edt = str(bi.edt) if hasattr(bi, 'edt') else ""
        # Direction 是枚举类型，需要用 str() 比较
        direction_str = str(bi.direction)

        return {
            "direction": direction_str,
            "is_up": direction_str == "向上",
            "start_price": _r(fx_a_price),
            "end_price": _r(fx_b_price),
            "start_date": sdt,
            "end_date": edt,
            "change_pct": _r(bi.change * 100) if hasattr(bi, 'change') else 0,
            "power": _r(bi.power) if hasattr(bi, 'power') else 0,
            "length": int(bi.length) if hasattr(bi, 'length') else 0,
            "high": _r(bi.high) if hasattr(bi, 'high') else 0,
            "low": _r(bi.low) if hasattr(bi, 'low') else 0,
            "angle": _r(bi.angle) if hasattr(bi, 'angle') else 0,
            "rsq": _r(bi.rsq, 4) if hasattr(bi, 'rsq') else 0,
            "slope": _r(bi.slope, 4) if hasattr(bi, 'slope') else 0,
            "SNR": _r(bi.SNR, 4) if hasattr(bi, 'SNR') else 0,
        }

    def _analyze_zs(self, czsc_obj) -> dict:
        """中枢分析"""
        # 从最后几笔的中枢信息推断
        bi_list = czsc_obj.bi_list
        if len(bi_list) < 3:
            return {"count": 0, "latest": None}

        # 尝试获取中枢列表
        zs_list = getattr(czsc_obj, 'zs_list', None) or getattr(czsc_obj, 'last_zs_list', None)
        if zs_list:
            latest = zs_list[-1]
            return {
                "count": len(zs_list),
                "latest": {
                    "zg": _r(getattr(latest, 'zg', 0)),
                    "zd": _r(getattr(latest, 'zd', 0)),
                    "gg": _r(getattr(latest, 'gg', 0)),
                    "dd": _r(getattr(latest, 'dd', 0)),
                    "level": str(getattr(latest, 'level', '')),
                },
            }

        # 简化：从最近 3 笔估算重叠区域
        last_3 = bi_list[-3:]
        if len(last_3) >= 3:
            highs = [max(b.high, b.low) for b in last_3]
            lows = [min(b.high, b.low) for b in last_3]
            zg = _r(min(highs))
            zd = _r(max(lows))
            if zg > zd:
                return {
                    "count": 1,
                    "latest": {"zg": zg, "zd": zd, "gg": _r(max(highs)), "dd": _r(min(lows)), "level": "估算"},
                }

        return {"count": 0, "latest": None}

    def _judge_trend(self, czsc_obj) -> dict:
        """趋势判断（基于同方向笔端点的缠论高低点结构）"""
        bi_list = czsc_obj.bi_list
        if len(bi_list) < 3:
            return {"trend": "数据不足", "signal": "wait", "description": "笔数量不足，无法判断趋势"}

        # 取最近 4 根笔，比较相邻同方向笔的端点
        recent = bi_list[-4:] if len(bi_list) >= 4 else bi_list[-3:]

        # 提取每根笔的端点（起点和终点）
        pen_data = []
        for bi in recent:
            fx_a = _safe_float(bi.fx_a.fx) if hasattr(bi, 'fx_a') else 0
            fx_b = _safe_float(bi.fx_b.fx) if hasattr(bi, 'fx_b') else 0
            pen_data.append({
                "direction": str(bi.direction),
                "start": fx_a,
                "end": fx_b,
                "high": max(fx_a, fx_b),
                "low": min(fx_a, fx_b),
            })

        last_bi = bi_list[-1]
        last_is_up = str(last_bi.direction) == "向上"

        # 找到最近两根同方向笔（向上笔的终点 = 波段高点，向下笔的终点 = 波段低点）
        up_ends = [p["end"] for p in pen_data if p["direction"] == "向上"]
        down_ends = [p["end"] for p in pen_data if p["direction"] == "向下"]

        # 缠论趋势判断：
        # 上涨结构：最近向上笔端点 > 前一个向上笔端点（创新高）
        # 下跌结构：最近向下笔端点 < 前一个向下笔端点（创新低）
        higher_high = len(up_ends) >= 2 and up_ends[-1] > up_ends[-2]
        lower_low = len(down_ends) >= 2 and down_ends[-1] < down_ends[-2]

        if higher_high and not lower_low:
            trend = "上涨结构"
            signal = "bullish"
            desc = f"向上笔端点抬高，当前处于{'向上' if last_is_up else '回调'}阶段"
        elif lower_low and not higher_high:
            trend = "下跌结构"
            signal = "bearish"
            desc = f"向下笔端点降低，当前处于{'向下' if not last_is_up else '反弹'}阶段"
        elif higher_high and lower_low:
            trend = "趋势加速"
            signal = "volatile"
            desc = f"高低点同时扩展，波动加剧，当前处于{'{last_bi.direction}' if last_is_up else '回调'}阶段"
        else:
            # 高低点都在收缩或未创新高/低
            if len(up_ends) >= 2 and len(down_ends) >= 2:
                hh = up_ends[-1] < up_ends[-2]
                ll = down_ends[-1] > down_ends[-2]
                if hh and ll:
                    trend = "收敛震荡"
                    signal = "neutral"
                    desc = "高低点逐步收敛，等待方向选择"
                elif not hh and not ll:
                    trend = "横盘整理"
                    signal = "neutral"
                    desc = f"未创新高/新低，{'向上' if last_is_up else '向下'}笔延续中"
                else:
                    trend = "震荡"
                    signal = "neutral"
                    desc = f"高低点结构不明确，处于震荡区间"
            else:
                trend = "数据不足"
                signal = "wait"
                desc = "笔数量不足，无法判断趋势"

        # 第三类买卖点
        has_3rd_buy = False
        has_3rd_sell = False
        if len(pen_data) >= 4:
            # 第三类买点：向上笔回调不破前一个向下笔的低点
            down_lows = [p["low"] for p in pen_data if p["direction"] == "向下"]
            up_lows = [p["low"] for p in pen_data if p["direction"] == "向上"]
            if last_is_up and len(up_lows) >= 2 and len(down_lows) >= 2:
                if up_lows[-1] > down_lows[-2]:
                    has_3rd_buy = True
            elif not last_is_up:
                up_highs = [p["high"] for p in pen_data if p["direction"] == "向上"]
                down_highs = [p["high"] for p in pen_data if p["direction"] == "向下"]
                if len(down_highs) >= 2 and len(up_highs) >= 2:
                    if down_highs[-1] < up_highs[-2]:
                        has_3rd_sell = True

        return {
            "trend": trend,
            "signal": signal,
            "description": desc,
            "3rd_buy": has_3rd_buy,
            "3rd_sell": has_3rd_sell,
        }

    def _detect_buy_sell_points(self, czsc_obj) -> list:
        """缠论买卖点检测（1/2/3类买卖点）"""
        bi_list = czsc_obj.bi_list
        if len(bi_list) < 3:
            return []

        points = []
        recent_bis = bi_list[-6:] if len(bi_list) >= 6 else bi_list
        
        # 第一类买点
        if len(recent_bis) >= 3:
            for i in range(len(recent_bis) - 2, 0, -1):
                bi = recent_bis[i]
                prev_bi = recent_bis[i-1]
                next_bi = recent_bis[i+1] if i+1 < len(recent_bis) else None
                if str(bi.direction) == "向上" and str(prev_bi.direction) == "向下":
                    if next_bi and str(next_bi.direction) == "向下":
                        if next_bi.low > prev_bi.low:
                            points.append({"type": "第一类买点", "price": _r(bi.low), "strength": "strong"})
                            break
        
        # 第二类买点
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                prev_bi = recent_bis[i-1]
                prev2_bi = recent_bis[i-2]
                if str(bi.direction) == "向下" and str(prev_bi.direction) == "向上":
                    if str(prev2_bi.direction) == "向下" and bi.low > prev2_bi.low:
                        points.append({"type": "第二类买点", "price": _r(bi.low), "strength": "normal"})
                        break
        
        # 第三类买点
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                if str(bi.direction) == "向上" and str(recent_bis[i-1].direction) == "向下":
                    if i >= 2 and str(recent_bis[i-2].direction) == "向上" and bi.low > recent_bis[i-2].high:
                        points.append({"type": "第三类买点", "price": _r(bi.low), "strength": "strong"})
                        break
        
        # 卖点
        if len(recent_bis) >= 3:
            for i in range(len(recent_bis) - 2, 0, -1):
                bi = recent_bis[i]
                prev_bi = recent_bis[i-1]
                next_bi = recent_bis[i+1] if i+1 < len(recent_bis) else None
                if str(bi.direction) == "向下" and str(prev_bi.direction) == "向上":
                    if next_bi and str(next_bi.direction) == "向上":
                        if next_bi.high < prev_bi.high:
                            points.append({"type": "第一类卖点", "price": _r(bi.high), "strength": "strong"})
                            break
        
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                prev_bi = recent_bis[i-1]
                prev2_bi = recent_bis[i-2]
                if str(bi.direction) == "向上" and str(prev_bi.direction) == "向下":
                    if str(prev2_bi.direction) == "向上" and bi.high < prev2_bi.high:
                        points.append({"type": "第二类卖点", "price": _r(bi.high), "strength": "normal"})
                        break
        
        if len(recent_bis) >= 4:
            for i in range(len(recent_bis) - 2, 1, -1):
                bi = recent_bis[i]
                if str(bi.direction) == "向下" and str(recent_bis[i-1].direction) == "向上":
                    if i >= 2 and str(recent_bis[i-2].direction) == "向下" and bi.high < recent_bis[i-2].low:
                        points.append({"type": "第三类卖点", "price": _r(bi.high), "strength": "strong"})
                        break
        
        return points

    def _detect_macd_divergence(self, df: pd.DataFrame, czsc_obj) -> Optional[dict]:
        """MACD背离检测"""
        if df is None or len(df) < 30:
            return None
        try:
            close = df['close'].values
            fast_ema = self._ema(close, 12)
            slow_ema = self._ema(close, 26)
            dif = fast_ema - slow_ema
            n = min(len(df), 60)
            recent_dif = dif[-n:]
            bi_list = czsc_obj.bi_list
            if len(bi_list) < 3:
                return None
            recent_bis = bi_list[-3:] if len(bi_list) >= 3 else bi_list
            last_bi = recent_bis[-1]
            prev_bi = recent_bis[-2]
            
            # 底背离
            if str(last_bi.direction) == "向下" and str(prev_bi.direction) == "向上" and len(recent_bis) >= 3:
                prev2_bi = recent_bis[-3]
                if str(prev2_bi.direction) == "向下" and last_bi.low < prev2_bi.low:
                    last_dif_low = min(recent_dif[-20:])
                    prev_dif_low = min(recent_dif[:20])
                    if last_dif_low > prev_dif_low:
                        return {"type": "底背离", "strength": "strong", "description": f"价格创新低({_r(last_bi.low)})但MACD未创新低，看涨信号"}
            
            # 顶背离
            if str(last_bi.direction) == "向上" and str(prev_bi.direction) == "向下" and len(recent_bis) >= 3:
                prev2_bi = recent_bis[-3]
                if str(prev2_bi.direction) == "向上" and last_bi.high > prev2_bi.high:
                    last_dif_high = max(recent_dif[-20:])
                    prev_dif_high = max(recent_dif[:20])
                    if last_dif_high < prev_dif_high:
                        return {"type": "顶背离", "strength": "warning", "description": f"价格创新高({_r(last_bi.high)})但MACD未创新高，看跌信号"}
            return None
        except Exception as e:
            logger.error(f"MACD背离计算失败: {e}")
            return None
    
    def _ema(self, data, period: int):
        """计算指数移动平均线"""
        ema = np.zeros_like(data, dtype=float)
        multiplier = 2 / (period + 1)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
        return ema

    def _extract_signals(self, czsc_obj) -> list:
        signals = []
        bi_list = czsc_obj.bi_list
        if not bi_list:
            return signals

        last_bi = bi_list[-1]
        is_up = str(last_bi.direction) == "向上"
        change_pct = _safe_float(last_bi.change) * 100 if hasattr(last_bi, 'change') else 0

        if is_up and change_pct > 5:
            signals.append({"type": "strong_up", "msg": f"向上笔力度强 (+{change_pct:.1f}%)", "strength": "strong"})
        elif is_up and change_pct > 0:
            signals.append({"type": "up", "msg": f"向上笔 (+{change_pct:.1f}%)", "strength": "normal"})
        elif not is_up and change_pct < -5:
            signals.append({"type": "strong_down", "msg": f"向下笔力度强 ({change_pct:.1f}%)", "strength": "strong"})
        elif not is_up:
            signals.append({"type": "down", "msg": f"向下笔 ({change_pct:.1f}%)", "strength": "normal"})

        # 分型信号
        fx_list = czsc_obj.fx_list
        if fx_list:
            latest_fx = fx_list[-1]
            fx_mark = str(latest_fx.mark)
            if "顶" in fx_mark:
                signals.append({"type": "top_fx", "msg": f"顶分型形成（{_r(latest_fx.fx)}）", "strength": "warning"})
            elif "底" in fx_mark:
                signals.append({"type": "bottom_fx", "msg": f"底分型形成（{_r(latest_fx.fx)}）", "strength": "info"})

        # 笔的拟合度
        if hasattr(last_bi, 'rsq'):
            rsq = _safe_float(last_bi.rsq)
            if rsq > 0.9:
                signals.append({"type": "strong_trend", "msg": f"笔趋势明确（R²={rsq:.3f}）", "strength": "strong"})
            elif rsq < 0.5:
                signals.append({"type": "weak_trend", "msg": f"笔趋势不明显（R²={rsq:.3f}）", "strength": "weak"})

        # 未完成笔
        ubi = getattr(czsc_obj, 'ubi', None)
        if ubi:
            ubi_dir = str(getattr(ubi, 'direction', ''))
            if "向上" in ubi_dir:
                signals.append({"type": "unfinished_up", "msg": "未完成向上笔", "strength": "weak"})
            elif "向下" in ubi_dir:
                signals.append({"type": "unfinished_down", "msg": "未完成向下笔", "strength": "weak"})

        return signals

    def _generate_summary(self, czsc_obj) -> dict:
        bi_list = czsc_obj.bi_list
        if not bi_list:
            return {"bi_count": 0, "overall_trend": "neutral", "recommendation": "观望", "key_points": []}

        last_bi = bi_list[-1]
        is_up = str(last_bi.direction) == "向上"
        change_pct = _safe_float(last_bi.change) * 100 if hasattr(last_bi, 'change') else 0

        summary = {
            "bi_count": len(bi_list),
            "fx_count": len(czsc_obj.fx_list),
            "overall_trend": "neutral",
            "recommendation": "观望",
            "key_points": [],
        }

        # 总体趋势判断
        if len(bi_list) >= 4:
            recent = [str(b.direction) for b in bi_list[-4:]]
            up_count = sum(1 for d in recent if d == "向上")
            if up_count >= 3:
                summary["overall_trend"] = "bullish"
                summary["recommendation"] = "看多"
            elif up_count <= 1:
                summary["overall_trend"] = "bearish"
                summary["recommendation"] = "看空"

        # 关键要点
        summary["key_points"].append(f"当前{'向上' if is_up else '向下'}笔，幅度 {change_pct:+.1f}%")

        if hasattr(last_bi, 'angle'):
            angle = _safe_float(last_bi.angle)
            summary["key_points"].append(f"笔角度: {angle:.1f}°")

        if hasattr(last_bi, 'rsq'):
            rsq = _safe_float(last_bi.rsq)
            summary["key_points"].append(f"笔拟合度 R²={rsq:.3f}")

        if hasattr(last_bi, 'power'):
            power = _safe_float(last_bi.power)
            summary["key_points"].append(f"笔力度: {power:.1f}")

        # 第三类买卖点
        if len(bi_list) >= 4:
            summary["key_points"].append("关注第三类买卖点")

        return summary
