"""
技术分析模块
计算各类技术指标，生成技术面评分
所有返回值均为原生 Python 类型（int/float/str），可直接 JSON 序列化
"""
import pandas as pd
from typing import Optional


def _py(val):
    """转换为原生 Python float"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _r(val, decimals=2):
    """安全四舍五入，返回原生 Python float"""
    return round(_py(val), decimals)


class TechnicalAnalyzer:
    """技术分析器"""

    def analyze(self, df: pd.DataFrame) -> Optional[dict]:
        if df is None or df.empty or len(df) < 30:
            return None

        result = {}
        result["ma"] = self.calc_ma(df)
        result["ema"] = self.calc_ema(df)
        result["macd"] = self.calc_macd(df)
        result["rsi"] = self.calc_rsi(df)
        result["boll"] = self.calc_boll(df)
        result["kdj"] = self.calc_kdj(df)
        result["atr"] = self.calc_atr(df)
        result["volume_analysis"] = self.analyze_volume(df)
        result["support_resistance"] = self.find_support_resistance(df)
        result["trend"] = self.analyze_trend(df, result)
        result["score"] = self.calc_score(df, result)
        return result

    def calc_ma(self, df: pd.DataFrame, periods=(5, 10, 20, 60, 120)) -> dict:
        result = {}
        for p in periods:
            if len(df) >= p:
                result[f"ma{p}"] = _r(df["close"].rolling(p).mean().iloc[-1])
            else:
                result[f"ma{p}"] = None
        return result

    def calc_ema(self, df: pd.DataFrame, periods=(12, 26)) -> dict:
        result = {}
        for p in periods:
            result[f"ema{p}"] = _r(df["close"].ewm(span=p, adjust=False).mean().iloc[-1])
        return result

    def calc_macd(self, df: pd.DataFrame, fast=12, slow=26, signal=9) -> dict:
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        macd_hist = 2 * (dif - dea)

        return {
            "dif": _r(dif.iloc[-1], 4),
            "dea": _r(dea.iloc[-1], 4),
            "hist": _r(macd_hist.iloc[-1], 4),
            "trend": "bullish" if dif.iloc[-1] > dea.iloc[-1] else "bearish",
        }

    def calc_rsi(self, df: pd.DataFrame, period=14) -> dict:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        rsi_val = _r(rsi.iloc[-1])

        if rsi_val > 70:
            signal = "overbought"
        elif rsi_val < 30:
            signal = "oversold"
        else:
            signal = "neutral"

        return {"value": rsi_val, "signal": signal, "period": period}

    def calc_boll(self, df: pd.DataFrame, period=20, std_dev=2) -> dict:
        ma = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        upper = ma + std_dev * std
        lower = ma - std_dev * std
        price = _py(df["close"].iloc[-1])
        bandwidth = _r((upper.iloc[-1] - lower.iloc[-1]) / ma.iloc[-1] * 100)

        if price > _py(upper.iloc[-1]):
            position = "above_upper"
        elif price < _py(lower.iloc[-1]):
            position = "below_lower"
        elif price > _py(ma.iloc[-1]):
            position = "above_middle"
        else:
            position = "below_middle"

        return {
            "upper": _r(upper.iloc[-1]),
            "middle": _r(ma.iloc[-1]),
            "lower": _r(lower.iloc[-1]),
            "bandwidth": bandwidth,
            "position": position,
        }

    def calc_kdj(self, df: pd.DataFrame, period=9) -> dict:
        low_min = df["low"].rolling(period).min()
        high_max = df["high"].rolling(period).max()
        rsv = (df["close"] - low_min) / (high_max - low_min).replace(0, float("nan")) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        j = 3 * k - 2 * d
        j_val = _r(j.iloc[-1])

        return {
            "k": _r(k.iloc[-1]),
            "d": _r(d.iloc[-1]),
            "j": j_val,
            "signal": "overbought" if j_val > 80 else ("oversold" if j_val < 20 else "neutral"),
        }

    def calc_atr(self, df: pd.DataFrame, period=14) -> dict:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        price = _py(df["close"].iloc[-1])
        atr_pct = _r(atr.iloc[-1] / price * 100) if price > 0 else 0.0
        return {"atr": _r(atr.iloc[-1], 4), "atr_pct": atr_pct}

    def analyze_volume(self, df: pd.DataFrame) -> dict:
        vol = df["volume"]
        avg_vol_5 = vol.tail(5).mean()
        avg_vol_20 = vol.tail(20).mean()
        current_vol = vol.iloc[-1]
        vol_ratio = _r(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0.0
        return {
            "current": int(current_vol),
            "avg_5": int(avg_vol_5),
            "avg_20": int(avg_vol_20),
            "volume_ratio": vol_ratio,
            "trend": "increasing" if avg_vol_5 > avg_vol_20 else "decreasing",
        }

    def find_support_resistance(self, df: pd.DataFrame) -> dict:
        recent = df.tail(60)
        price = _py(df["close"].iloc[-1])
        highs_above = recent[recent["high"] > price]["high"]
        lows_below = recent[recent["low"] < price]["low"]
        resistance = _r(highs_above.min()) if not highs_above.empty else _r(price * 1.05)
        support = _r(lows_below.max()) if not lows_below.empty else _r(price * 0.95)
        return {"resistance": resistance, "support": support}

    def analyze_trend(self, df: pd.DataFrame, indicators: dict) -> dict:
        price = _py(df["close"].iloc[-1])
        ma = indicators["ma"]
        bullish_alignment = (
            ma.get("ma5") and ma.get("ma20") and ma.get("ma60")
            and ma["ma5"] > ma["ma20"] > ma["ma60"]
        )
        above_ma60 = bool(ma.get("ma60") and price > ma["ma60"])
        macd_bullish = indicators["macd"]["trend"] == "bullish"

        if bullish_alignment and above_ma60 and macd_bullish:
            trend = "strong_bullish"
        elif bullish_alignment or (above_ma60 and macd_bullish):
            trend = "bullish"
        elif not above_ma60 and not macd_bullish:
            trend = "strong_bearish"
        else:
            trend = "bearish"

        return {
            "trend": trend,
            "bullish_alignment": bool(bullish_alignment),
            "above_ma60": above_ma60,
        }

    def calc_score(self, df: pd.DataFrame, indicators: dict) -> dict:
        # 1. 趋势面（30 分）
        trend_scores = {"strong_bullish": 30, "bullish": 22, "bearish": 10, "strong_bearish": 3}
        trend_score = trend_scores.get(indicators["trend"]["trend"], 15)

        # 2. 技术指标（25 分）
        tech_score = 12.5
        macd = indicators["macd"]
        if macd["trend"] == "bullish":
            tech_score += 4
        if macd["hist"] > 0:
            tech_score += 2
        rsi = indicators["rsi"]["value"]
        if 40 <= rsi <= 65:
            tech_score += 3
        elif 30 <= rsi < 40:
            tech_score += 1
        elif 65 < rsi <= 75:
            tech_score += 1
        elif rsi > 80:
            tech_score -= 3
        kdj = indicators["kdj"]
        if kdj["j"] < 20:
            tech_score += 2
        elif kdj["j"] > 80:
            tech_score -= 1
        tech_score = max(0, min(25, tech_score))

        # 3. 成交量（20 分）
        vol = indicators["volume_analysis"]
        vol_score = 10
        if vol["volume_ratio"] > 1.5:
            vol_score += 4
        if vol["trend"] == "increasing":
            vol_score += 2
        if vol["volume_ratio"] > 3:
            vol_score -= 3
        vol_score = max(0, min(20, vol_score))

        # 4. 波动率（15 分）
        atr_pct = indicators["atr"]["atr_pct"]
        if 1 <= atr_pct <= 4:
            vol_risk_score = 15
        elif atr_pct < 1:
            vol_risk_score = 10
        elif atr_pct <= 6:
            vol_risk_score = 10
        else:
            vol_risk_score = 5

        # 5. 动量（10 分）
        if len(df) >= 10:
            roc_10 = (_py(df["close"].iloc[-1]) / _py(df["close"].iloc[-10]) - 1) * 100
        else:
            roc_10 = 0
        if 0 < roc_10 <= 10:
            momentum_score = 10
        elif roc_10 > 10:
            momentum_score = 7
        elif -5 <= roc_10 <= 0:
            momentum_score = 5
        else:
            momentum_score = 2

        total = int(round(trend_score + tech_score + vol_score + vol_risk_score + momentum_score))

        return {
            "total": total,
            "breakdown": {
                "trend": int(trend_score),
                "technical": int(round(tech_score)),
                "volume": int(vol_score),
                "volatility": int(vol_risk_score),
                "momentum": int(momentum_score),
            },
            "max": 100,
        }
