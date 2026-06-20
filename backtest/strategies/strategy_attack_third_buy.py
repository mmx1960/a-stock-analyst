"""
进攻型三买策略 v1：热点板块 + 主升趋势 + 工程化缠论三买。
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.data_provider import data_provider
from app.core.providers.bigamap_provider import bigamap_provider
from app.core.providers.kaipanla_provider import kaipanla_provider
from backtest.strategies.strategy_v3_1_realtime import (
    THROTTLE_SECONDS,
    calc_macd,
    normalize_history_dataframe,
)

logger = logging.getLogger(__name__)

MIN_DAILY_BARS = 160
MIN_INTRADAY_BARS = 160
DEFAULT_STRUCTURE_PERIOD = "30"
MINUTE_STRUCTURE_PERIODS = {"5", "15", "30", "60"}
PLATFORM_LOOKBACK_BARS = 35
PLATFORM_MIN_BARS = 18
PRE_UPTREND_LOOKBACK_BARS = 90
MIN_PRE_UPTREND_PCT = 0.25
MAX_PLATFORM_RANGE_PCT = 0.35
MIN_BREAKOUT_PCT = 0.03
MAX_PULLBACK_BELOW_PLATFORM_PCT = 0.02
MIN_RESTART_VOLUME_RATIO = 1.1
MIN_CURRENT_TO_120D_HIGH_RATIO = 0.75
MIN_AMOUNT = 80_000_000
SIGNAL_WINDOW_DAYS = 10
DEFAULT_OUTPUT = Path("current_attack_third_buy.json")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def build_theme_heat_map(limit_up_payload: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """基于 BigAmap 涨停复盘构建行业热度图。"""
    stats = bigamap_provider.extract_limit_up_theme_stats(limit_up_payload)
    heat_map: dict[str, dict[str, Any]] = {}
    for item in stats:
        limit_up_count = _safe_int(item.get("limit_up_count"))
        max_limit_up_days = _safe_int(item.get("max_limit_up_days"))
        one_word_count = _safe_int(item.get("one_word_count"))
        total_sealed_amount = _safe_float(item.get("total_sealed_amount"))
        score = min(30.0, limit_up_count * 6.0)
        score += min(25.0, max_limit_up_days * 8.0)
        score += min(20.0, math.log10(total_sealed_amount / 1e7 + 1.0) * 8.0)
        score += min(10.0, one_word_count * 5.0)
        score = round(min(100.0, score), 2)
        enriched = dict(item)
        enriched["theme_heat_score"] = score
        heat_map[str(item.get("theme"))] = enriched
    return heat_map


def get_hot_theme_codes(limit_up_payload: dict[str, Any] | None = None, *, min_heat_score: float = 45.0) -> dict[str, dict[str, Any]]:
    """返回热点板块股票代码到主题信息的映射。"""
    heat_map = build_theme_heat_map(limit_up_payload)
    code_map: dict[str, dict[str, Any]] = {}
    for theme, theme_info in heat_map.items():
        hot_enough = theme_info.get("theme_heat_score", 0) >= min_heat_score
        hot_enough = hot_enough or (
            _safe_int(theme_info.get("max_limit_up_days")) >= 2
            and _safe_int(theme_info.get("limit_up_count")) >= 2
        )
        if not hot_enough:
            continue
        for stock in theme_info.get("stocks", []):
            code = str(stock.get("code") or "")
            if not code:
                continue
            current = code_map.get(code)
            candidate = {
                "theme": theme,
                "theme_heat_score": theme_info.get("theme_heat_score", 0),
                "theme_limit_up_count": theme_info.get("limit_up_count", 0),
                "theme_max_limit_up_days": theme_info.get("max_limit_up_days", 0),
                "theme_total_sealed_amount": theme_info.get("total_sealed_amount", 0),
                "theme_sample_stocks": [s.get("name") for s in theme_info.get("stocks", [])[:8]],
                "stock_name": stock.get("name"),
                "stock_limit_up_days": stock.get("limit_up_days"),
                "stock_first_limit_up_time": stock.get("first_limit_up_time"),
                "stock_sealed_amount": stock.get("sealed_amount"),
                "stock_break_board_count": stock.get("break_board_count"),
            }
            if current is None or candidate["theme_heat_score"] > current["theme_heat_score"]:
                code_map[code] = candidate
    return code_map


def _prepare_kline(df: pd.DataFrame, *, min_bars: int = MIN_DAILY_BARS) -> pd.DataFrame | None:
    normalized = normalize_history_dataframe(df, min_bars=min_bars)
    if normalized is None or normalized.empty:
        return None
    normalized = calc_macd(normalized)
    normalized["ma5"] = normalized["close"].rolling(5).mean()
    normalized["ma10"] = normalized["close"].rolling(10).mean()
    normalized["ma20"] = normalized["close"].rolling(20).mean()
    normalized["ma60"] = normalized["close"].rolling(60).mean()
    normalized["ma120"] = normalized["close"].rolling(120).mean()
    normalized["ma250"] = normalized["close"].rolling(250, min_periods=200).mean()
    normalized["volume_ma5"] = normalized["volume"].rolling(5).mean()
    return normalized


def _prepare_daily(df: pd.DataFrame) -> pd.DataFrame | None:
    return _prepare_kline(df, min_bars=MIN_DAILY_BARS)


def _structure_freq_label(structure_period: str) -> str:
    return "日线" if structure_period == "daily" else f"{structure_period}分钟"


def detect_attack_third_buy_structure(
    df_daily: pd.DataFrame,
    now: datetime | None = None,
    *,
    structure_period: str = DEFAULT_STRUCTURE_PERIOD,
) -> dict[str, Any] | None:
    """检测工程化三买：平台突破 -> 回踩不破 -> 再启动。"""
    min_bars = MIN_DAILY_BARS if structure_period == "daily" else MIN_INTRADAY_BARS
    df = _prepare_kline(df_daily, min_bars=min_bars)
    if df is None or len(df) < min_bars:
        return None

    now = now or pd.to_datetime(df.iloc[-1]["date"]).to_pydatetime()
    latest = df.iloc[-1]
    close = _safe_float(latest["close"])
    if close <= 0:
        return None

    ma20 = _safe_float(latest.get("ma20"))
    ma60 = _safe_float(latest.get("ma60"))
    ma120 = _safe_float(latest.get("ma120"))
    ma250 = _safe_float(latest.get("ma250"))
    long_ma_ok = close > ma250 if ma250 > 0 else close > ma120
    trend_ma_ok = close > ma20 > ma60 and long_ma_ok
    if not trend_ma_ok:
        return None

    high_120 = _safe_float(df["high"].tail(120).max())
    current_to_120d_high = close / high_120 if high_120 else 0.0
    if current_to_120d_high < MIN_CURRENT_TO_120D_HIGH_RATIO:
        return None

    recent = df.tail(PLATFORM_LOOKBACK_BARS).copy()
    platform = recent.iloc[:-5].tail(max(PLATFORM_MIN_BARS, PLATFORM_LOOKBACK_BARS - 8)).copy()
    if len(platform) < PLATFORM_MIN_BARS:
        return None

    platform_high = _safe_float(platform["high"].quantile(0.85))
    platform_low = _safe_float(platform["low"].quantile(0.15))
    platform_mid = (platform_high + platform_low) / 2 if platform_high and platform_low else 0.0
    platform_range_pct = (platform_high - platform_low) / platform_mid if platform_mid else 1.0
    if platform_range_pct > MAX_PLATFORM_RANGE_PCT:
        return None

    pre_window = df.iloc[max(0, len(df) - PRE_UPTREND_LOOKBACK_BARS - PLATFORM_LOOKBACK_BARS): len(df) - PLATFORM_LOOKBACK_BARS]
    if pre_window.empty:
        return None
    pre_low = _safe_float(pre_window["low"].min())
    pre_uptrend_pct = (platform_high - pre_low) / pre_low if pre_low else 0.0
    if pre_uptrend_pct < MIN_PRE_UPTREND_PCT:
        return None

    breakout_slice = recent.iloc[-8:-2]
    if breakout_slice.empty:
        return None
    breakout_close = _safe_float(breakout_slice["close"].max())
    breakout_ok = breakout_close >= platform_high * (1 + MIN_BREAKOUT_PCT)
    if not breakout_ok:
        return None

    pullback_slice = recent.iloc[-5:-1]
    pullback_low = _safe_float(pullback_slice["low"].min()) if not pullback_slice.empty else _safe_float(latest["low"])
    pullback_ok = pullback_low >= platform_high * (1 - MAX_PULLBACK_BELOW_PLATFORM_PCT)
    if not pullback_ok:
        return None

    volume_ma5 = _safe_float(latest.get("volume_ma5"))
    restart_volume_ratio = _safe_float(latest.get("volume")) / volume_ma5 if volume_ma5 else 0.0
    restart_price_ok = close > _safe_float(latest["open"]) and close >= max(_safe_float(latest.get("ma5")), platform_high)
    macd_restart_ok = _safe_float(latest.get("macd")) >= 0 or _safe_float(latest.get("dif")) > _safe_float(latest.get("dea"))
    restart_ok = restart_price_ok and (restart_volume_ratio >= MIN_RESTART_VOLUME_RATIO or macd_restart_ok)
    if not restart_ok:
        return None

    buy_date = pd.to_datetime(latest["date"])
    days_ago = max(0, (pd.to_datetime(now).normalize() - buy_date.normalize()).days)
    return {
        "third_buy_ok": True,
        "third_buy_reason": "platform_breakout_pullback_restart",
        "structure_period": structure_period,
        "structure_freq": _structure_freq_label(structure_period),
        "buy_date": str(buy_date.date()),
        "price": round(close, 2),
        "days_ago": days_ago,
        "platform_high": round(platform_high, 2),
        "platform_low": round(platform_low, 2),
        "platform_range_pct": round(platform_range_pct * 100, 2),
        "pre_uptrend_pct": round(pre_uptrend_pct * 100, 2),
        "breakout_close": round(breakout_close, 2),
        "pullback_low": round(pullback_low, 2),
        "restart_volume_ratio": round(restart_volume_ratio, 3),
        "current_to_120d_high": round(current_to_120d_high, 3),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "ma120": round(ma120, 2),
        "ma250": round(ma250, 2) if ma250 else None,
    }


def score_attack_signal(signal: dict[str, Any], theme_info: dict[str, Any]) -> dict[str, Any]:
    theme_heat_score = _safe_float(theme_info.get("theme_heat_score"))
    leader_score = 0.0
    limit_up_days = _safe_int(theme_info.get("stock_limit_up_days"))
    if limit_up_days >= 2:
        leader_score += 16
    elif limit_up_days == 1:
        leader_score += 8
    sealed = _safe_float(theme_info.get("stock_sealed_amount"))
    if sealed > 0:
        leader_score += min(12.0, math.log10(sealed / 1e7 + 1.0) * 4.0)
    if theme_info.get("stock_first_limit_up_time") == "09:25":
        leader_score += 4

    third_buy_score = 50.0
    third_buy_score += min(15.0, _safe_float(signal.get("pre_uptrend_pct")) / 4.0)
    third_buy_score += max(0.0, 10.0 - _safe_float(signal.get("platform_range_pct")) / 5.0)
    third_buy_score += min(10.0, _safe_float(signal.get("restart_volume_ratio")) * 3.0)

    trend_strength_score = 0.0
    current_to_high = _safe_float(signal.get("current_to_120d_high"))
    trend_strength_score += min(20.0, current_to_high * 20.0)

    volume_attack_score = min(20.0, _safe_float(signal.get("restart_volume_ratio")) * 8.0)
    risk_penalty = 0.0
    if theme_info.get("stock_break_board_count") not in (None, 0):
        risk_penalty += min(10.0, _safe_int(theme_info.get("stock_break_board_count")) * 3.0)
    if _safe_float(signal.get("platform_range_pct")) > 28:
        risk_penalty += 5.0

    attack_score = (
        theme_heat_score * 0.30
        + leader_score * 0.20
        + third_buy_score * 0.25
        + trend_strength_score * 0.15
        + volume_attack_score * 0.10
        - risk_penalty
    )
    scored = dict(signal)
    scored.update(theme_info)
    scored.update({
        "strategy_version": "attack-third-buy-v1",
        "attack_score": round(attack_score, 2),
        "score_breakdown": {
            "theme_heat_score": round(theme_heat_score, 2),
            "leader_score": round(leader_score, 2),
            "third_buy_score": round(third_buy_score, 2),
            "trend_strength_score": round(trend_strength_score, 2),
            "volume_attack_score": round(volume_attack_score, 2),
            "risk_penalty": round(risk_penalty, 2),
        },
    })
    return scored


def analyze_attack_third_buy_signal(
    df_daily: pd.DataFrame,
    theme_info: dict[str, Any],
    now: datetime | None = None,
    *,
    structure_period: str = DEFAULT_STRUCTURE_PERIOD,
) -> dict[str, Any] | None:
    signal = detect_attack_third_buy_structure(df_daily=df_daily, now=now, structure_period=structure_period)
    if not signal:
        return None
    return score_attack_signal(signal, theme_info)


def scan_attack_third_buy(
    *,
    max_stocks: int = 80,
    min_heat_score: float = 45.0,
    signal_window_days: int = SIGNAL_WINDOW_DAYS,
    pool_mode: str = "combined",
    board_lookback_days: int = 10,
    board_top_n: int = 10,
    board_min_appearances: int = 3,
    structure_period: str = DEFAULT_STRUCTURE_PERIOD,
    throttle_seconds: float = THROTTLE_SECONDS,
) -> list[dict[str, Any]]:
    logger.info("=== 进攻型三买选股 v1 ===")
    code_theme_map: dict[str, dict[str, Any]] = {}
    if pool_mode in {"kaipanla", "kaipanla_cache", "combined"}:
        try:
            code_theme_map.update(kaipanla_provider.get_cached_hot_stock_map())
        except Exception as exc:
            logger.warning("开盘啦本地热点池读取失败: %s", exc)
    if pool_mode in {"limit_up", "combined"}:
        limit_up_payload = bigamap_provider.get_limit_up_review()
        code_theme_map.update(get_hot_theme_codes(limit_up_payload, min_heat_score=min_heat_score))
    if pool_mode in {"repeated_boards", "combined"}:
        repeated_board_map = bigamap_provider.get_repeated_hot_board_stock_map(
            lookback_days=board_lookback_days,
            top_n=board_top_n,
            min_appearances=board_min_appearances,
        )
        for code, theme_info in repeated_board_map.items():
            current = code_theme_map.get(code)
            if current is None or float(theme_info.get("theme_heat_score") or 0) > float(current.get("theme_heat_score") or 0):
                code_theme_map[code] = theme_info
    logger.info("热点候选股票数: %s (pool_mode=%s)", len(code_theme_map), pool_mode)

    results: list[dict[str, Any]] = []
    for i, (code, theme_info) in enumerate(list(code_theme_map.items())[:max_stocks]):
        quote = data_provider.get_realtime_quote(code) or {}
        amount = _safe_float(quote.get("amount") or quote.get("turnover") or quote.get("成交额"))
        if amount and amount < MIN_AMOUNT:
            continue
        if structure_period in MINUTE_STRUCTURE_PERIODS:
            df = data_provider.get_kline_minute(code, period=structure_period)
        else:
            df = data_provider.get_kline_daily(code, start_date="")
        if df is None or df.empty:
            continue
        signal = analyze_attack_third_buy_signal(df, theme_info, now=datetime.now(), structure_period=structure_period)
        if signal and signal.get("days_ago", 999) > signal_window_days:
            continue
        if signal:
            stock_name = quote.get("name") or theme_info.get("stock_name") or code
            signal.update({
                "code": code,
                "name": stock_name,
                "current_price": quote.get("price") or quote.get("latest_price"),
                "change_pct": quote.get("change_pct"),
                "amount": amount,
            })
            results.append(signal)
            logger.info("🎯 %s %s 分数:%s 主题:%s", code, signal.get("name"), signal["attack_score"], signal.get("theme"))
        if (i + 1) % 20 == 0:
            logger.info("进度: %s/%s 命中:%s", i + 1, min(len(code_theme_map), max_stocks), len(results))
        time.sleep(throttle_seconds)

    results.sort(key=lambda x: (-x["attack_score"], -_safe_float(x.get("theme_heat_score")), x.get("days_ago", 99)))
    return results


def save_results(results: list[dict[str, Any]], output_path: str | Path = DEFAULT_OUTPUT) -> Path:
    path = Path(output_path)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    results = scan_attack_third_buy()
    path = save_results(results)
    logger.info("已保存: %s; 命中: %s", path, len(results))


if __name__ == "__main__":
    main()
