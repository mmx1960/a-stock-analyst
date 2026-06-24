"""杰哥风格：短线情绪龙头/核心人气股回调低吸策略。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import (
    DEFAULT_STRUCTURE_PERIOD,
    INTRADAY_MA,
    MIN_MINUTE_BARS,
    _normalize_daily,
    _normalize_minute,
    _safe_float,
)
from backtest.strategies.strategy_d_shen_trend_pullback import is_risky_stock_name

MIN_DAILY_BARS = 80
MOMENTUM_LOOKBACK = 20
CORE_HIGH_LOOKBACK = 60
PULLBACK_LOOKBACK = 10
MIN_RECENT_SURGE_PCT = 18.0
MIN_CORE_POSITION_RATIO = 0.72
MAX_PULLBACK_FROM_HIGH_PCT = 24.0
MIN_LATEST_DAILY_AMOUNT = 80_000_000.0
MIN_AVG10_DAILY_AMOUNT = 60_000_000.0
MIN_VOLUME_RATIO = 0.8
MAX_CLOSE_ABOVE_MA13_PCT = 0.055
MAX_PULLBACK_TO_MA13_PCT = 0.025
MIN_INTRADAY_REBOUND_SCORE = 52.0
DEFAULT_THEME_HEAT_SCORE = 70.0
MAX_PRE_PULLBACK_SURGE_PCT = 85.0
MAX_PRE_PULLBACK_PRICE_TO_MA20_RATIO = 1.45
MAX_PRE_PULLBACK_PRICE_TO_MA30_RATIO = 1.55


def check_jie_ge_high_position_second_wave_exclusion(data: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """排除博云新材式高位强势二波：前面已暴力主升，后面再反抽是小概率极端样本。"""
    if data is None or data.empty:
        return True, {"jie_ge_second_wave_filter_ok": True, "jie_ge_second_wave_reason": "no_daily_data"}
    frame = data.copy()
    for column in ["high", "low", "close"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if len(frame) < 35:
        return True, {"jie_ge_second_wave_filter_ok": True, "jie_ge_second_wave_reason": "insufficient_bars"}
    pre_pullback = frame.iloc[:-PULLBACK_LOOKBACK] if len(frame) > PULLBACK_LOOKBACK else frame.iloc[:0]
    if len(pre_pullback) < 30:
        return True, {"jie_ge_second_wave_filter_ok": True, "jie_ge_second_wave_reason": "pre_pullback_window_insufficient"}
    recent_pre = pre_pullback.tail(30).copy()
    pre_low = _safe_float(recent_pre["low"].min())
    pre_high = _safe_float(recent_pre["high"].max())
    pre_surge_pct = (pre_high / pre_low - 1.0) * 100 if pre_low > 0 else 0.0
    ma20 = _safe_float(pre_pullback["close"].rolling(20, min_periods=20).mean().iloc[-1])
    ma30 = _safe_float(pre_pullback["close"].rolling(30, min_periods=30).mean().iloc[-1])
    price_to_ma20 = pre_high / ma20 if ma20 > 0 else 1.0
    price_to_ma30 = pre_high / ma30 if ma30 > 0 else 1.0
    extreme = (
        pre_surge_pct >= MAX_PRE_PULLBACK_SURGE_PCT
        and (price_to_ma20 >= MAX_PRE_PULLBACK_PRICE_TO_MA20_RATIO or price_to_ma30 >= MAX_PRE_PULLBACK_PRICE_TO_MA30_RATIO)
    )
    return not extreme, {
        "jie_ge_second_wave_filter_ok": not extreme,
        "jie_ge_second_wave_reason": "high_position_second_wave_excluded" if extreme else "passed_translation_test",
        "pre_pullback_surge_pct": round(pre_surge_pct, 4),
        "pre_pullback_price_to_ma20": round(price_to_ma20, 4),
        "pre_pullback_price_to_ma30": round(price_to_ma30, 4),
        "max_pre_pullback_surge_pct": MAX_PRE_PULLBACK_SURGE_PCT,
        "max_pre_pullback_price_to_ma20_ratio": MAX_PRE_PULLBACK_PRICE_TO_MA20_RATIO,
        "max_pre_pullback_price_to_ma30_ratio": MAX_PRE_PULLBACK_PRICE_TO_MA30_RATIO,
    }


def check_jie_ge_dragon_context(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """杰哥语义：先看是不是核心强势/有人气，不能是跟风狗或没资金的弱票。"""
    data = _normalize_daily(daily)
    if len(data) < MIN_DAILY_BARS:
        return False, {"jie_ge_context_reason": "daily_data_insufficient", "daily_bars": len(data)}
    for column in ["amount", "volume", "high", "low", "open", "close"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    latest = data.iloc[-1]
    close = _safe_float(latest.get("close"))
    if close <= 0:
        return False, {"jie_ge_context_reason": "invalid_close", "daily_bars": len(data)}

    recent = data.tail(MOMENTUM_LOOKBACK)
    high60 = _safe_float(data["high"].tail(CORE_HIGH_LOOKBACK).max())
    low20 = _safe_float(recent["low"].min())
    high20 = _safe_float(recent["high"].max())
    surge_pct = (high20 / low20 - 1.0) * 100 if low20 > 0 else 0.0
    core_position_ratio = close / high60 if high60 > 0 else 0.0
    pullback_pct = (high20 / close - 1.0) * 100 if close > 0 and high20 > 0 else 999.0
    latest_amount = _safe_float(latest.get("amount"))
    avg10_amount = _safe_float(data["amount"].tail(10).mean()) if "amount" in data.columns else 0.0
    avg5_volume = _safe_float(data["volume"].tail(5).mean()) if "volume" in data.columns else 0.0
    avg20_volume = _safe_float(data["volume"].tail(20).mean()) if "volume" in data.columns else 0.0
    volume_ratio = avg5_volume / avg20_volume if avg20_volume > 0 else 1.0
    last10_low = _safe_float(data["low"].tail(PULLBACK_LOOKBACK).min())
    pullback_control_ok = close >= last10_low * 1.03 if last10_low > 0 else True

    surge_ok = surge_pct >= MIN_RECENT_SURGE_PCT
    core_position_ok = core_position_ratio >= MIN_CORE_POSITION_RATIO
    pullback_ok = 1.0 <= pullback_pct <= MAX_PULLBACK_FROM_HIGH_PCT
    liquidity_ok = latest_amount >= MIN_LATEST_DAILY_AMOUNT and avg10_amount >= MIN_AVG10_DAILY_AMOUNT
    volume_ok = volume_ratio >= MIN_VOLUME_RATIO
    second_wave_ok, second_wave_meta = check_jie_ge_high_position_second_wave_exclusion(data)

    score = 35.0
    score += min(22.0, max(0.0, surge_pct / 1.5))
    score += min(18.0, max(0.0, (core_position_ratio - 0.6) * 90.0))
    score += 12.0 if pullback_ok else -8.0
    score += 10.0 if liquidity_ok else -15.0
    score += 8.0 if volume_ok else -6.0
    score += 5.0 if pullback_control_ok else -8.0
    score += 0.0 if second_wave_ok else -25.0
    final_score = round(max(0.0, min(100.0, score)), 2)
    ok = all([surge_ok, core_position_ok, pullback_ok, liquidity_ok, volume_ok, pullback_control_ok, second_wave_ok])
    return ok, {
        "jie_ge_context_reason": "jie_ge_dragon_context_ok" if ok else "jie_ge_dragon_context_failed",
        "jie_ge_core_score": final_score,
        "recent_surge_pct": round(surge_pct, 4),
        "core_position_ratio": round(core_position_ratio, 4),
        "pullback_from_recent_high_pct": round(pullback_pct, 4),
        "daily_latest_amount": round(latest_amount, 2),
        "daily_avg10_amount": round(avg10_amount, 2),
        "daily_volume_ratio_5_20": round(volume_ratio, 4),
        "daily_pullback_control_ok": bool(pullback_control_ok),
        **second_wave_meta,
        "daily_bars": len(data),
        "jie_ge_tags": ["资金态度", "核心人气", "第一波回调", "平移测试", "不做高位极端二波", "不做跟风狗"],
    }


def detect_jie_ge_30m_rebound(minute: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    """30分钟企稳反抽：回踩 MA13/MA30 附近后重新收回，弱反抽不做。"""
    data = _normalize_minute(minute)
    if len(data) < MIN_MINUTE_BARS:
        return None
    data["ma13"] = data["close"].rolling(INTRADAY_MA).mean()
    data["ma30"] = data["close"].rolling(30).mean()
    data["vol_ma20"] = data["volume"].rolling(20, min_periods=5).mean() if "volume" in data.columns else 0.0
    latest = data.iloc[-1]
    prev = data.iloc[-2]
    close = _safe_float(latest.get("close"))
    open_price = _safe_float(latest.get("open"))
    high = _safe_float(latest.get("high"))
    low = _safe_float(latest.get("low"))
    ma13 = _safe_float(latest.get("ma13"))
    ma30 = _safe_float(latest.get("ma30"))
    prev_ma13 = _safe_float(prev.get("ma13"))
    volume = _safe_float(latest.get("volume"))
    vol_ma20 = _safe_float(latest.get("vol_ma20"))
    if min(close, ma13, ma30) <= 0:
        return None

    touched_support = low <= max(ma13, ma30) * (1 + MAX_PULLBACK_TO_MA13_PCT)
    recovered = ma13 <= close <= ma13 * (1 + MAX_CLOSE_ABOVE_MA13_PCT)
    ma13_slope_ok = ma13 >= prev_ma13 if prev_ma13 > 0 else True
    bullish_body = close >= open_price and close >= low + (high - low) * 0.6
    volume_recover = volume >= vol_ma20 * 0.75 if vol_ma20 > 0 else True
    rebound_score = 45.0
    rebound_score += 15.0 if touched_support else 0.0
    rebound_score += 15.0 if recovered else 0.0
    rebound_score += 10.0 if ma13_slope_ok else 0.0
    rebound_score += 10.0 if bullish_body else 0.0
    rebound_score += 5.0 if volume_recover else -5.0
    if not (touched_support and recovered and bullish_body and volume_recover and rebound_score >= MIN_INTRADAY_REBOUND_SCORE):
        return None

    trade_dt = pd.to_datetime(latest["trade_dt"])
    now = now or datetime.now()
    return {
        "buy_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": round(min(100.0, rebound_score), 2),
        "signal_reason": "jie_ge_emotion_dragon_pullback",
        "structure_period": DEFAULT_STRUCTURE_PERIOD,
        "structure_freq": "30分钟",
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "pullback_low": round(low, 4),
        "close_to_ma13_pct": round((close / ma13 - 1.0) * 100.0, 4),
        "ma13_slope_ok": bool(ma13_slope_ok),
        "bullish_rebound": bool(bullish_body),
        "volume_recover": bool(volume_recover),
        "days_ago": max(0, (pd.to_datetime(now).normalize() - trade_dt.normalize()).days),
    }


def build_jie_ge_30m_watch(minute: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    data = _normalize_minute(minute)
    if len(data) < MIN_MINUTE_BARS:
        return None
    data["ma13"] = data["close"].rolling(INTRADAY_MA).mean()
    data["ma30"] = data["close"].rolling(30).mean()
    data["vol_ma20"] = data["volume"].rolling(20, min_periods=5).mean() if "volume" in data.columns else 0.0
    latest = data.iloc[-1]
    close = _safe_float(latest.get("close"))
    low = _safe_float(latest.get("low"))
    ma13 = _safe_float(latest.get("ma13"))
    ma30 = _safe_float(latest.get("ma30"))
    vol_ma20 = _safe_float(latest.get("vol_ma20"))
    if min(close, ma13, ma30) <= 0:
        return None
    near_support = low <= max(ma13, ma30) * (1 + MAX_PULLBACK_TO_MA13_PCT * 1.5) or close <= ma13 * (1 + MAX_CLOSE_ABOVE_MA13_PCT)
    if not near_support:
        return None
    trade_dt = pd.to_datetime(latest["trade_dt"])
    now = now or datetime.now()
    return {
        "signal_status": "watch",
        "watch_reason": "waiting_next_30m_rebound_confirmation",
        "watch_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": 45.0,
        "signal_reason": "jie_ge_emotion_dragon_pullback_watch",
        "structure_period": DEFAULT_STRUCTURE_PERIOD,
        "structure_freq": "30分钟",
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "pullback_low": round(low, 4),
        "next_30m_trigger_conditions": [
            f"下一根30分钟最低价继续不明显跌破 MA13/MA30 支撑区：参考 MA13={ma13:.2f}, MA30={ma30:.2f}",
            f"下一根30分钟收盘重新站上 MA13，且不高于 MA13 上方 {MAX_CLOSE_ABOVE_MA13_PCT*100:.1f}%（避免追高）",
            "下一根30分钟K线收阳，且收盘位于当根振幅上半区",
            f"成交量不塌陷：至少接近20根均量的75%，当前vol_ma20={vol_ma20:.0f}",
        ],
        "days_ago": max(0, (pd.to_datetime(now).normalize() - trade_dt.normalize()).days),
    }


def analyze_jie_ge_emotion_dragon_pullback(
    daily: pd.DataFrame,
    minute: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    context_ok, context_meta = check_jie_ge_dragon_context(daily)
    if not context_ok:
        return None
    rebound = detect_jie_ge_30m_rebound(minute, now=now)
    if rebound is None:
        return None
    signal = {**rebound, **context_meta}
    signal["signal_status"] = "triggered"
    signal["theme"] = "杰哥龙头低吸"
    signal["theme_heat_score"] = DEFAULT_THEME_HEAT_SCORE
    signal["strategy_version"] = "jie-ge-emotion-dragon-pullback-v1"
    signal["jie_ge_trade_plan"] = {
        "market_context": "只做核心人气和资金态度，不碰跟风狗。",
        "entry": "第一波主升后的回调低吸，30分钟企稳反抽才上。",
        "exit": "冲高不强分批走；破位毫不犹豫，不加仓硬抗。",
        "risk": "高位连续加速后不接，核心趴下跟风小弟更不能碰。",
    }
    signal["strategy_metadata"] = {
        "momentum_lookback": MOMENTUM_LOOKBACK,
        "min_recent_surge_pct": MIN_RECENT_SURGE_PCT,
        "min_core_position_ratio": MIN_CORE_POSITION_RATIO,
        "max_pullback_from_high_pct": MAX_PULLBACK_FROM_HIGH_PCT,
        "min_latest_daily_amount": MIN_LATEST_DAILY_AMOUNT,
        "min_avg10_daily_amount": MIN_AVG10_DAILY_AMOUNT,
        "style": "jie_ge_fund_emotion_dragon_pullback",
    }
    return signal


def scan_jie_ge_emotion_dragon_pullback(
    *,
    provider: MarketDataProvider | None = None,
    max_stocks: int = 80,
    signal_window_days: int = 10,
    throttle_seconds: float = 0.0,
    min_heat_score: float = 0.0,
    as_of_date: str | None = None,
    include_watchlist: bool = False,
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
        if is_risky_stock_name(name):
            continue
        daily = market_data.get_daily_bars(
            code=code,
            start_date=(now - timedelta(days=520)).strftime("%Y-%m-%d"),
            end_date=end_date,
            adjust="hfq",
        )
        minute = market_data.get_minute_bars(
            code=code,
            period=DEFAULT_STRUCTURE_PERIOD,
            start_date=(now - timedelta(days=240)).strftime("%Y-%m-%d"),
            end_date=f"{end_date} 23:59:59",
        )
        signal = analyze_jie_ge_emotion_dragon_pullback(daily, minute, now=now)
        if not signal and include_watchlist:
            context_ok, context_meta = check_jie_ge_dragon_context(daily)
            watch = build_jie_ge_30m_watch(minute, now=now) if context_ok else None
            if watch is not None:
                signal = {**watch, **context_meta}
                signal["theme"] = "杰哥龙头低吸"
                signal["theme_heat_score"] = DEFAULT_THEME_HEAT_SCORE
                signal["strategy_version"] = "jie-ge-emotion-dragon-pullback-watch-v1"
        if not signal or signal.get("days_ago", 999) > signal_window_days:
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
    results.sort(
        key=lambda item: (
            -_safe_float(item.get("jie_ge_core_score")),
            -_safe_float(item.get("signal_score")),
            -_safe_float(item.get("recent_surge_pct")),
            str(item.get("code")),
        )
    )
    return results
