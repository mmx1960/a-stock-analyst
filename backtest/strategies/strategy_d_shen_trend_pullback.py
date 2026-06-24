"""D神风格：大周期趋势 + 板块资金 + 30 分钟回踩多空买点策略。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import (
    DEFAULT_STRUCTURE_PERIOD,
    INTRADAY_MA,
    MIN_DAILY_BARS,
    MIN_MINUTE_BARS,
    _normalize_daily,
    _normalize_minute,
    _safe_float,
)

DAILY_MA_FAST = 20
DAILY_MA_MID = 30
DAILY_MA_SLOW = 60
DAILY_MA_LONG = 120
MIN_DAILY_TREND_SCORE = 62.0
MIN_MONTHLY_SPACE_PCT = 8.0
MAX_DAILY_EXTENSION_FROM_MA20_PCT = 12.0
MIN_LATEST_DAILY_AMOUNT = 50_000_000.0
MIN_AVG20_DAILY_AMOUNT = 30_000_000.0
MAX_30M_CLOSE_ABOVE_MA13_PCT = 0.04
MAX_30M_PULLBACK_TO_MA13_PCT = 0.018
MIN_30M_MA_CONVERGENCE_SCORE = 45.0
DEFAULT_THEME_HEAT_SCORE = 62.0
ATTACK_DUOKONG_POSITIONS = {15, 30}
DEFENSE_DUOKONG_POSITIONS = {60, 120}


def classify_d_shen_technical_pool_position(position: Any) -> dict[str, Any]:
    """D神纯技术股池：按多空位置把标的分为进攻/防御观察。"""
    try:
        value = int(float(position))
    except Exception:
        value = 0
    if value in ATTACK_DUOKONG_POSITIONS:
        pool_type = "attack"
        label = "进攻标的"
        reason = "15/30 多空位置，偏短线进攻观察，等小周期回踩收回"
        preferred_periods = ["15", "30"]
        risk_note = "不追开盘直冲，隔日方向错就走"
    elif value in DEFENSE_DUOKONG_POSITIONS:
        pool_type = "defense"
        label = "防御标的"
        reason = "60/120 多空位置，偏大周期支撑/趋势防守，等支撑确认"
        preferred_periods = ["60", "120"]
        risk_note = "买点更慢，不能当进攻票追高"
    else:
        pool_type = "unknown"
        label = "观察标的"
        reason = "多空位置不在 D神纯技术池常用分层内"
        preferred_periods = []
        risk_note = "只观察，不作为直接买点"
    return {
        "d_shen_technical_pool_type": pool_type,
        "d_shen_technical_pool_label": label,
        "duokong_position": value,
        "duokong_position_reason": reason,
        "preferred_periods": preferred_periods,
        "d_shen_pool_risk_note": risk_note,
    }


def is_risky_stock_name(name: Any) -> bool:
    text = str(name or "").upper().strip()
    return "ST" in text or "退" in text or "退市" in text


def check_d_shen_daily_context(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """Check D神 style daily/weekly-like context using only daily bars.

    D神语义：先看大周期趋势，不在下坡趋势里硬做；主升/趋势票最好在
    MA20/MA30/MA60 上方，且不能离均线太远。
    """
    data = _normalize_daily(daily)
    if len(data) < MIN_DAILY_BARS:
        return False, {"d_shen_context_reason": "daily_data_insufficient", "daily_bars": len(data)}
    data["ma20"] = data["close"].rolling(DAILY_MA_FAST).mean()
    data["ma30"] = data["close"].rolling(DAILY_MA_MID).mean()
    data["ma60"] = data["close"].rolling(DAILY_MA_SLOW).mean()
    data["ma120"] = data["close"].rolling(DAILY_MA_LONG, min_periods=60).mean()
    latest = data.iloc[-1]
    prev5 = data.iloc[-6] if len(data) >= 6 else data.iloc[0]
    prev20 = data.iloc[-21] if len(data) >= 21 else data.iloc[0]
    close = _safe_float(latest.get("close"))
    ma20 = _safe_float(latest.get("ma20"))
    ma30 = _safe_float(latest.get("ma30"))
    ma60 = _safe_float(latest.get("ma60"))
    ma120 = _safe_float(latest.get("ma120"))
    prev_ma20 = _safe_float(prev5.get("ma20"))
    prev_ma60 = _safe_float(prev20.get("ma60"))
    if min(close, ma20, ma30, ma60) <= 0:
        return False, {"d_shen_context_reason": "daily_ma_unavailable", "daily_bars": len(data)}

    alignment_ok = close > ma20 > ma30 > ma60
    ma20_slope_ok = ma20 >= prev_ma20 if prev_ma20 > 0 else True
    ma60_slope_ok = ma60 >= prev_ma60 if prev_ma60 > 0 else True
    ma120_ok = close >= ma120 if ma120 > 0 else True
    recent_high_120 = _safe_float(data["high"].tail(120).max()) if "high" in data.columns else _safe_float(data["close"].tail(120).max())
    recent_low_60 = _safe_float(data["low"].tail(60).min()) if "low" in data.columns else _safe_float(data["close"].tail(60).min())
    monthly_space_pct = (recent_high_120 / close - 1.0) * 100 if close > 0 and recent_high_120 > 0 else 0.0
    drawdown_control_ok = close >= recent_low_60 * 1.08 if recent_low_60 > 0 else True
    extension_pct = (close / ma20 - 1.0) * 100 if ma20 > 0 else 999.0
    not_overextended = extension_pct <= MAX_DAILY_EXTENSION_FROM_MA20_PCT
    latest_amount = _safe_float(latest.get("amount")) if "amount" in data.columns else 0.0
    avg20_amount = _safe_float(data["amount"].tail(20).mean()) if "amount" in data.columns else 0.0
    # 旧数据/测试数据可能没有 amount；有 amount 时必须满足基本流动性，避免 D神趋势池混入无资金承接的小票。
    liquidity_ok = True
    if "amount" in data.columns and max(latest_amount, avg20_amount) > 0:
        liquidity_ok = latest_amount >= MIN_LATEST_DAILY_AMOUNT and avg20_amount >= MIN_AVG20_DAILY_AMOUNT

    score = 35.0
    score += 18.0 if alignment_ok else 0.0
    score += 12.0 if ma20_slope_ok else 0.0
    score += 10.0 if ma60_slope_ok else 0.0
    score += 8.0 if ma120_ok else 0.0
    score += min(10.0, max(0.0, monthly_space_pct / 2.0))
    score += 7.0 if drawdown_control_ok else 0.0
    score += 5.0 if not_overextended else -8.0
    score += 6.0 if liquidity_ok else -15.0
    final_score = round(max(0.0, min(100.0, score)), 2)
    ok = all([alignment_ok, ma20_slope_ok, ma60_slope_ok, ma120_ok, drawdown_control_ok, not_overextended, liquidity_ok]) and final_score >= MIN_DAILY_TREND_SCORE
    return ok, {
        "d_shen_context_reason": "d_shen_daily_context_ok" if ok else "d_shen_daily_context_failed",
        "d_shen_daily_context_score": final_score,
        "daily_close": round(close, 4),
        "daily_ma20": round(ma20, 4),
        "daily_ma30": round(ma30, 4),
        "daily_ma60": round(ma60, 4),
        "daily_ma120": round(ma120, 4) if ma120 > 0 else None,
        "daily_alignment_ok": bool(alignment_ok),
        "daily_ma20_slope_ok": bool(ma20_slope_ok),
        "daily_ma60_slope_ok": bool(ma60_slope_ok),
        "daily_above_ma120_ok": bool(ma120_ok),
        "daily_drawdown_control_ok": bool(drawdown_control_ok),
        "daily_extension_from_ma20_pct": round(extension_pct, 4),
        "daily_not_overextended_ok": bool(not_overextended),
        "daily_latest_amount": round(latest_amount, 2),
        "daily_avg20_amount": round(avg20_amount, 2),
        "daily_liquidity_ok": bool(liquidity_ok),
        "min_latest_daily_amount": MIN_LATEST_DAILY_AMOUNT,
        "min_avg20_daily_amount": MIN_AVG20_DAILY_AMOUNT,
        "monthly_space_pct": round(monthly_space_pct, 4),
        "monthly_space_ok": bool(monthly_space_pct >= MIN_MONTHLY_SPACE_PCT),
        "daily_bars": len(data),
    }


def detect_d_shen_30m_pullback(minute: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Detect 30m pullback near MA13 plus MA convergence.

    D神语义：小周期回踩多空附近，均线粘合/并排，收回 MA13 但不能追太远。
    """
    data = _normalize_minute(minute)
    if len(data) < MIN_MINUTE_BARS:
        return None
    data["ma5"] = data["close"].rolling(5).mean()
    data["ma13"] = data["close"].rolling(INTRADAY_MA).mean()
    data["ma30"] = data["close"].rolling(30).mean()
    latest = data.iloc[-1]
    prev = data.iloc[-2]
    close = _safe_float(latest.get("close"))
    open_price = _safe_float(latest.get("open"))
    low = _safe_float(latest.get("low"))
    high = _safe_float(latest.get("high"))
    ma5 = _safe_float(latest.get("ma5"))
    ma13 = _safe_float(latest.get("ma13"))
    ma30 = _safe_float(latest.get("ma30"))
    prev_ma13 = _safe_float(prev.get("ma13"))
    if min(close, ma13, ma30) <= 0:
        return None

    touched_ma13 = low <= ma13 * (1 + MAX_30M_PULLBACK_TO_MA13_PCT)
    recovered_ma13 = ma13 <= close <= ma13 * (1 + MAX_30M_CLOSE_ABOVE_MA13_PCT)
    ma13_slope_ok = ma13 >= prev_ma13 if prev_ma13 > 0 else True
    bullish_recover = close >= open_price and close >= (low + (high - low) * 0.55)
    ma_convergence_pct = (max(ma5, ma13, ma30) / min(ma5, ma13, ma30) - 1.0) * 100 if min(ma5, ma13, ma30) > 0 else 999.0
    convergence_score = max(0.0, 100.0 - ma_convergence_pct * 18.0)
    ma_convergence_ok = convergence_score >= MIN_30M_MA_CONVERGENCE_SCORE
    ma_order_ok = ma5 >= ma13 * 0.985 and ma13 >= ma30 * 0.985
    if not (touched_ma13 and recovered_ma13 and ma13_slope_ok and bullish_recover and ma_convergence_ok and ma_order_ok):
        return None

    trade_dt = pd.to_datetime(latest["trade_dt"])
    now = now or datetime.now()
    signal_score = 50.0
    signal_score += min(16.0, max(0.0, convergence_score / 100.0 * 16.0))
    signal_score += 12.0 if ma13_slope_ok else 0.0
    signal_score += 10.0 if bullish_recover else 0.0
    signal_score += 8.0 if ma_order_ok else 0.0
    signal_score += min(4.0, max(0.0, (close / ma13 - 1.0) * 200.0))
    return {
        "buy_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": round(min(100.0, signal_score), 2),
        "signal_reason": "d_shen_trend_30m_pullback",
        "structure_period": DEFAULT_STRUCTURE_PERIOD,
        "structure_freq": "30分钟",
        "ma5_30m": round(ma5, 4) if ma5 > 0 else None,
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "pullback_low": round(low, 4),
        "close_to_ma13_pct": round((close / ma13 - 1.0) * 100.0, 4),
        "ma13_slope_ok": bool(ma13_slope_ok),
        "ma_order_ok": bool(ma_order_ok),
        "ma_convergence_score": round(convergence_score, 2),
        "ma_convergence_pct": round(ma_convergence_pct, 4),
        "bullish_recover": bool(bullish_recover),
        "days_ago": max(0, (pd.to_datetime(now).normalize() - trade_dt.normalize()).days),
    }


def build_d_shen_30m_watch(minute: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    data = _normalize_minute(minute)
    if len(data) < MIN_MINUTE_BARS:
        return None
    data["ma5"] = data["close"].rolling(5).mean()
    data["ma13"] = data["close"].rolling(INTRADAY_MA).mean()
    data["ma30"] = data["close"].rolling(30).mean()
    latest = data.iloc[-1]
    close = _safe_float(latest.get("close"))
    low = _safe_float(latest.get("low"))
    ma5 = _safe_float(latest.get("ma5"))
    ma13 = _safe_float(latest.get("ma13"))
    ma30 = _safe_float(latest.get("ma30"))
    if min(close, ma5, ma13, ma30) <= 0:
        return None
    ma_convergence_pct = (max(ma5, ma13, ma30) / min(ma5, ma13, ma30) - 1.0) * 100
    convergence_score = max(0.0, 100.0 - ma_convergence_pct * 18.0)
    near_ma13 = low <= ma13 * (1 + MAX_30M_PULLBACK_TO_MA13_PCT * 1.5) or close <= ma13 * (1 + MAX_30M_CLOSE_ABOVE_MA13_PCT)
    ma_order_nearly_ok = ma5 >= ma13 * 0.975 and ma13 >= ma30 * 0.975
    if not (near_ma13 and convergence_score >= MIN_30M_MA_CONVERGENCE_SCORE * 0.8 and ma_order_nearly_ok):
        return None
    trade_dt = pd.to_datetime(latest["trade_dt"])
    now = now or datetime.now()
    return {
        "signal_status": "watch",
        "watch_reason": "waiting_next_30m_pullback_recover_confirmation",
        "watch_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": 45.0,
        "signal_reason": "d_shen_trend_30m_pullback_watch",
        "structure_period": DEFAULT_STRUCTURE_PERIOD,
        "structure_freq": "30分钟",
        "ma5_30m": round(ma5, 4),
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "pullback_low": round(low, 4),
        "ma_convergence_score": round(convergence_score, 2),
        "ma_convergence_pct": round(ma_convergence_pct, 4),
        "next_30m_trigger_conditions": [
            f"下一根30分钟最低价继续回踩 MA13 附近但不有效跌破：参考 MA13={ma13:.2f}",
            f"下一根30分钟收盘站回 MA13，且不高于 MA13 上方 {MAX_30M_CLOSE_ABOVE_MA13_PCT*100:.1f}%",
            "MA5/MA13/MA30 继续粘合并维持多头附近排列",
            "下一根30分钟收阳，收盘位于当根振幅上半区",
        ],
        "days_ago": max(0, (pd.to_datetime(now).normalize() - trade_dt.normalize()).days),
    }


def analyze_d_shen_trend_30m_pullback(
    daily: pd.DataFrame,
    minute: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    context_ok, context_meta = check_d_shen_daily_context(daily)
    if not context_ok:
        return None
    pullback = detect_d_shen_30m_pullback(minute, now=now)
    if pullback is None:
        return None
    signal = {**pullback, **context_meta}
    signal["signal_status"] = "triggered"
    signal["theme"] = "D神趋势回踩"
    signal["theme_heat_score"] = DEFAULT_THEME_HEAT_SCORE
    signal["strategy_version"] = "d-shen-trend-30m-pullback-v1"
    signal["d_shen_trade_plan"] = {
        "market_context": "先看大盘和板块资金，不在下坡趋势硬做。",
        "entry": "日线趋势向上，30分钟回踩 MA13/多空附近并收回，均线粘合不追高。",
        "exit": "隔日方向错就走；趋势票不破 5 日线可持有，滞涨或冲压力位回落兑现。",
        "risk": "非主线、板块无资金一致性、收盘离 MA13 太远时不要追。",
    }
    signal["strategy_metadata"] = {
        "daily_ma_fast": DAILY_MA_FAST,
        "daily_ma_mid": DAILY_MA_MID,
        "daily_ma_slow": DAILY_MA_SLOW,
        "daily_ma_long": DAILY_MA_LONG,
        "intraday_ma": INTRADAY_MA,
        "max_30m_close_above_ma13_pct": MAX_30M_CLOSE_ABOVE_MA13_PCT,
        "max_30m_pullback_to_ma13_pct": MAX_30M_PULLBACK_TO_MA13_PCT,
        "min_30m_ma_convergence_score": MIN_30M_MA_CONVERGENCE_SCORE,
        "max_daily_extension_from_ma20_pct": MAX_DAILY_EXTENSION_FROM_MA20_PCT,
        "min_latest_daily_amount": MIN_LATEST_DAILY_AMOUNT,
        "min_avg20_daily_amount": MIN_AVG20_DAILY_AMOUNT,
        "exclude_risky_stock_name": True,
        "d_shen_principles": ["看大周期做小周期", "题材消息龙头三重验证", "资金一致性不会假", "低吸优先不追高"],
    }
    return signal


def scan_d_shen_trend_30m_pullback(
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
        signal = analyze_d_shen_trend_30m_pullback(daily, minute, now=now)
        if not signal and include_watchlist:
            context_ok, context_meta = check_d_shen_daily_context(daily)
            watch = build_d_shen_30m_watch(minute, now=now) if context_ok else None
            if watch is not None:
                signal = {**watch, **context_meta}
                signal["theme"] = "D神趋势回踩"
                signal["theme_heat_score"] = DEFAULT_THEME_HEAT_SCORE
                signal["strategy_version"] = "d-shen-trend-30m-pullback-watch-v1"
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
    results.sort(
        key=lambda item: (
            -_safe_float(item.get("d_shen_daily_context_score")),
            -_safe_float(item.get("ma_convergence_score")),
            -_safe_float(item.get("signal_score")),
            str(item.get("code")),
        )
    )
    return results
