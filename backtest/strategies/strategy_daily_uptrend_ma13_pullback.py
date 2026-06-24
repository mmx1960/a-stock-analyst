"""日线上涨趋势 + 30 分钟回踩 MA13 买入策略。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider

DAILY_MA_FAST = 20
DAILY_MA_SLOW = 60
DAILY_LOOKBACK_BARS = 90
MIN_DAILY_BARS = 80
MIN_MINUTE_BARS = 40
INTRADAY_MA = 13
DEFAULT_STRUCTURE_PERIOD = "30"
MAX_PULLBACK_BELOW_MA13_PCT = 0.015
MAX_CLOSE_ABOVE_MA13_PCT = 0.035
MIN_DAILY_TREND_SCORE = 50.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    if "trade_date" not in data.columns:
        if "date" in data.columns:
            data["trade_date"] = data["date"]
        elif "datetime" in data.columns:
            data["trade_date"] = data["datetime"]
    if "trade_date" not in data.columns:
        return pd.DataFrame()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    for column in ["open", "high", "low", "close", "volume"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["trade_date", "close"]).sort_values("trade_date").reset_index(drop=True)
    return data


def _normalize_minute(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    if "trade_dt" not in data.columns:
        if "datetime" in data.columns:
            data["trade_dt"] = data["datetime"]
        elif "date" in data.columns:
            data["trade_dt"] = data["date"]
    if "trade_dt" not in data.columns:
        return pd.DataFrame()
    data["trade_dt"] = pd.to_datetime(data["trade_dt"])
    for column in ["open", "high", "low", "close", "volume"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["trade_dt", "open", "high", "low", "close"]).sort_values("trade_dt").reset_index(drop=True)
    return data


def check_daily_uptrend(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    data = _normalize_daily(daily)
    if len(data) < MIN_DAILY_BARS:
        return False, {"daily_trend_reason": "daily_data_insufficient", "daily_bars": len(data)}
    data["ma20"] = data["close"].rolling(DAILY_MA_FAST).mean()
    data["ma60"] = data["close"].rolling(DAILY_MA_SLOW).mean()
    latest = data.iloc[-1]
    prev = data.iloc[-6] if len(data) >= 6 else data.iloc[0]
    close = _safe_float(latest.get("close"))
    ma20 = _safe_float(latest.get("ma20"))
    ma60 = _safe_float(latest.get("ma60"))
    prev_ma20 = _safe_float(prev.get("ma20"))
    recent_low = _safe_float(data["low"].tail(20).min()) if "low" in data.columns else _safe_float(data["close"].tail(20).min())
    if ma20 <= 0 or ma60 <= 0:
        return False, {"daily_trend_reason": "daily_ma_unavailable", "daily_bars": len(data)}

    ma_alignment = close > ma20 > ma60
    ma20_slope_ok = ma20 >= prev_ma20 if prev_ma20 > 0 else True
    pullback_control_ok = recent_low >= ma60 * 0.97
    ok = ma_alignment and ma20_slope_ok and pullback_control_ok
    trend_score = 50.0
    trend_score += min(20.0, max(0.0, (close / ma20 - 1.0) * 400.0))
    trend_score += min(20.0, max(0.0, (ma20 / ma60 - 1.0) * 300.0))
    trend_score += 10.0 if ma20_slope_ok else 0.0
    return ok, {
        "daily_trend_reason": "daily_uptrend_ok" if ok else "daily_uptrend_failed",
        "daily_close": round(close, 4),
        "daily_ma20": round(ma20, 4),
        "daily_ma60": round(ma60, 4),
        "daily_ma20_slope_ok": bool(ma20_slope_ok),
        "daily_ma_alignment_ok": bool(ma_alignment),
        "daily_pullback_control_ok": bool(pullback_control_ok),
        "daily_trend_score": round(min(100.0, trend_score), 2),
        "daily_bars": len(data),
    }


def detect_30m_ma13_pullback(minute: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    data = _normalize_minute(minute)
    if len(data) < MIN_MINUTE_BARS:
        return None
    data["ma13"] = data["close"].rolling(INTRADAY_MA).mean()
    latest = data.iloc[-1]
    prev = data.iloc[-2]
    ma13 = _safe_float(latest.get("ma13"))
    prev_ma13 = _safe_float(prev.get("ma13"))
    close = _safe_float(latest.get("close"))
    low = _safe_float(latest.get("low"))
    open_price = _safe_float(latest.get("open"))
    if ma13 <= 0 or close <= 0:
        return None

    touched_ma = low <= ma13 * (1 + MAX_PULLBACK_BELOW_MA13_PCT)
    recovered_ma = close >= ma13 and close <= ma13 * (1 + MAX_CLOSE_ABOVE_MA13_PCT)
    ma_slope_ok = ma13 >= prev_ma13 if prev_ma13 > 0 else True
    bullish_close = close >= open_price
    if not (touched_ma and recovered_ma and ma_slope_ok and bullish_close):
        return None

    trade_dt = pd.to_datetime(latest["trade_dt"])
    now = now or datetime.now()
    signal_score = 55.0
    signal_score += min(20.0, max(0.0, (close / ma13 - 1.0) * 700.0))
    signal_score += 15.0 if ma_slope_ok else 0.0
    signal_score += 10.0 if bullish_close else 0.0
    return {
        "buy_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": round(min(100.0, signal_score), 2),
        "signal_reason": "daily_uptrend_30m_ma13_pullback",
        "structure_period": DEFAULT_STRUCTURE_PERIOD,
        "structure_freq": "30分钟",
        "ma13": round(ma13, 4),
        "pullback_low": round(low, 4),
        "close_to_ma13_pct": round((close / ma13 - 1.0) * 100.0, 4),
        "ma13_slope_ok": bool(ma_slope_ok),
        "bullish_close": bool(bullish_close),
        "days_ago": max(0, (pd.to_datetime(now).normalize() - trade_dt.normalize()).days),
    }


def analyze_daily_uptrend_30m_ma13_pullback(
    daily: pd.DataFrame,
    minute: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    trend_ok, trend_meta = check_daily_uptrend(daily)
    if not trend_ok:
        return None
    pullback = detect_30m_ma13_pullback(minute, now=now)
    if pullback is None:
        return None
    signal = {**pullback, **trend_meta}
    signal["theme"] = "日线上涨趋势回踩"
    signal["theme_heat_score"] = 50.0
    signal["strategy_version"] = "daily-uptrend-30m-ma13-pullback-v1"
    signal["strategy_metadata"] = {
        "daily_ma_fast": DAILY_MA_FAST,
        "daily_ma_slow": DAILY_MA_SLOW,
        "intraday_ma": INTRADAY_MA,
        "max_pullback_below_ma13_pct": MAX_PULLBACK_BELOW_MA13_PCT,
        "max_close_above_ma13_pct": MAX_CLOSE_ABOVE_MA13_PCT,
    }
    return signal


def scan_daily_uptrend_30m_ma13_pullback(
    *,
    provider: MarketDataProvider | None = None,
    max_stocks: int = 80,
    signal_window_days: int = 10,
    throttle_seconds: float = 0.0,
    min_heat_score: float = 0.0,
    as_of_date: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    del throttle_seconds, min_heat_score
    market_data = provider or DuckDBMarketDataProvider()
    stock_list = market_data.get_stock_list(limit=max_stocks)
    if stock_list is None or stock_list.empty:
        return []
    results: list[dict[str, Any]] = []
    now = pd.to_datetime(as_of_date).to_pydatetime() if as_of_date else datetime.now()
    end_date = now.strftime("%Y-%m-%d")
    for _, stock in stock_list.head(max_stocks).iterrows():
        code = str(stock.get("code") or "").zfill(6)
        if not code:
            continue
        name = stock.get("name") or stock.get("名称") or code
        daily = market_data.get_daily_bars(
            code=code,
            start_date=(now - timedelta(days=460)).strftime("%Y-%m-%d"),
            end_date=end_date,
            adjust="hfq",
        )
        minute = market_data.get_minute_bars(
            code=code,
            period=DEFAULT_STRUCTURE_PERIOD,
            start_date=(now - timedelta(days=240)).strftime("%Y-%m-%d"),
            end_date=f"{end_date} 23:59:59",
        )
        signal = analyze_daily_uptrend_30m_ma13_pullback(daily, minute, now=now)
        if not signal:
            continue
        if signal.get("days_ago", 999) > signal_window_days:
            continue
        quote = market_data.get_realtime_quote(code) or {}
        signal.update(
            {
                "code": code,
                "name": quote.get("name") or name,
                "current_price": quote.get("price") or signal.get("signal_price"),
                "change_pct": quote.get("change_pct"),
                "amount": quote.get("amount") or quote.get("turnover"),
            }
        )
        results.append(signal)
    results.sort(key=lambda item: (-_safe_float(item.get("signal_score")), str(item.get("code"))))
    return results
