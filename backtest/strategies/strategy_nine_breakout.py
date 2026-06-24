"""九号策略：近 5 日强势板块 + 半年上升趋势前高突破/临突破。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import _normalize_daily, _safe_float

HALF_YEAR_BARS = 120
MIN_DAILY_BARS = 90
FAST_MA = 20
SLOW_MA = 60
PRIOR_HIGH_LOOKBACK_BARS = 120
PRIOR_HIGH_EXCLUDE_RECENT_BARS = 1
MAX_BELOW_PRIOR_HIGH_PCT = 0.05
MAX_ABOVE_PRIOR_HIGH_PCT = 0.08
MIN_UPTREND_RETURN_PCT = 18.0
MIN_CLOSE_TO_MA60_RATIO = 1.0
DEFAULT_THEME_HEAT_SCORE = 60.0


def check_half_year_uptrend_high_breakout(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    data = _normalize_daily(daily)
    if len(data) < MIN_DAILY_BARS:
        return False, {"nine_reason": "daily_data_insufficient", "daily_bars": len(data)}
    data = data.tail(max(HALF_YEAR_BARS + PRIOR_HIGH_EXCLUDE_RECENT_BARS, MIN_DAILY_BARS)).reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data["ma20"] = data["close"].rolling(FAST_MA).mean()
    data["ma60"] = data["close"].rolling(SLOW_MA).mean()
    latest = data.iloc[-1]
    close = _safe_float(latest.get("close"))
    high = _safe_float(latest.get("high"))
    low = _safe_float(latest.get("low"))
    ma20 = _safe_float(latest.get("ma20"))
    ma60 = _safe_float(latest.get("ma60"))
    if min(close, high, low, ma20, ma60) <= 0:
        return False, {"nine_reason": "daily_ma_unavailable", "daily_bars": len(data)}

    prev_ma60_row = data.iloc[-21] if len(data) >= 21 else data.iloc[0]
    prev_ma60 = _safe_float(prev_ma60_row.get("ma60"))
    half_window = data.tail(HALF_YEAR_BARS)
    half_low = _safe_float(half_window["low"].min())
    half_return_pct = (close / half_low - 1.0) * 100.0 if half_low > 0 else 0.0
    ma_alignment_ok = close >= ma20 >= ma60
    ma60_slope_ok = ma60 >= prev_ma60 if prev_ma60 > 0 else True
    close_above_ma60_ok = close / ma60 >= MIN_CLOSE_TO_MA60_RATIO
    uptrend_ok = ma_alignment_ok and ma60_slope_ok and close_above_ma60_ok and half_return_pct >= MIN_UPTREND_RETURN_PCT

    prior_window_end = max(0, len(data) - PRIOR_HIGH_EXCLUDE_RECENT_BARS)
    prior_window_start = max(0, prior_window_end - PRIOR_HIGH_LOOKBACK_BARS)
    prior_window = data.iloc[prior_window_start:prior_window_end]
    if prior_window.empty:
        return False, {"nine_reason": "prior_high_window_empty", "daily_bars": len(data)}
    prior_high_idx = int(prior_window["high"].idxmax())
    prior_high_row = data.loc[prior_high_idx]
    prior_high = _safe_float(prior_high_row.get("high"))
    if prior_high <= 0:
        return False, {"nine_reason": "prior_high_unavailable", "daily_bars": len(data)}

    distance_to_prior_high_pct = (close / prior_high - 1.0) * 100.0
    intraday_breakout = high >= prior_high
    close_breakout = close >= prior_high
    near_breakout = -MAX_BELOW_PRIOR_HIGH_PCT * 100.0 <= distance_to_prior_high_pct < 0
    fresh_breakout = 0 <= distance_to_prior_high_pct <= MAX_ABOVE_PRIOR_HIGH_PCT * 100.0
    breakout_ok = near_breakout or fresh_breakout

    signal_score = 45.0
    signal_score += min(18.0, max(0.0, half_return_pct / 2.5))
    signal_score += 12.0 if ma_alignment_ok else -8.0
    signal_score += 8.0 if ma60_slope_ok else -8.0
    signal_score += 15.0 if fresh_breakout else (10.0 if near_breakout else 6.0 if intraday_breakout else -10.0)
    signal_score += min(7.0, max(0.0, (close / ma60 - 1.0) * 100.0))

    ok = uptrend_ok and breakout_ok
    return ok, {
        "nine_reason": "nine_breakout_ok" if ok else "nine_breakout_failed",
        "daily_bars": len(data),
        "daily_close": round(close, 4),
        "daily_high": round(high, 4),
        "daily_low": round(low, 4),
        "daily_ma20": round(ma20, 4),
        "daily_ma60": round(ma60, 4),
        "daily_ma_alignment_ok": bool(ma_alignment_ok),
        "daily_ma60_slope_ok": bool(ma60_slope_ok),
        "half_year_low": round(half_low, 4),
        "half_year_return_pct": round(half_return_pct, 4),
        "prior_high_price": round(prior_high, 4),
        "prior_high_date": str(pd.to_datetime(prior_high_row["trade_date"]).date()),
        "distance_to_prior_high_pct": round(distance_to_prior_high_pct, 4),
        "near_prior_high_breakout": bool(near_breakout),
        "fresh_prior_high_breakout": bool(fresh_breakout),
        "intraday_breakout_prior_high": bool(intraday_breakout),
        "close_breakout_prior_high": bool(close_breakout),
        "signal_score": round(max(0.0, min(100.0, signal_score)), 2),
        "strategy_metadata": {
            "half_year_bars": HALF_YEAR_BARS,
            "prior_high_lookback_bars": PRIOR_HIGH_LOOKBACK_BARS,
            "max_below_prior_high_pct": MAX_BELOW_PRIOR_HIGH_PCT,
            "max_above_prior_high_pct": MAX_ABOVE_PRIOR_HIGH_PCT,
            "min_uptrend_return_pct": MIN_UPTREND_RETURN_PCT,
        },
    }


def analyze_strategy_nine_breakout(daily: pd.DataFrame, *, now: datetime | None = None) -> dict[str, Any] | None:
    ok, meta = check_half_year_uptrend_high_breakout(daily)
    if not ok:
        return None
    now = now or datetime.now()
    signal_time = pd.to_datetime(_normalize_daily(daily).iloc[-1]["trade_date"])
    signal = dict(meta)
    signal.update(
        {
            "buy_date": str(signal_time.date()),
            "signal_time": signal_time.isoformat(),
            "signal_price": meta["daily_close"],
            "signal_reason": "strategy_nine_strong_sector_prior_high_breakout",
            "structure_period": "daily",
            "structure_freq": "日线",
            "theme": "九号策略强势板块突破",
            "theme_heat_score": DEFAULT_THEME_HEAT_SCORE,
            "strategy_version": "strategy-nine-strong-sector-prior-high-breakout-v1",
            "days_ago": max(0, (pd.to_datetime(now).normalize() - signal_time.normalize()).days),
        }
    )
    return signal


def scan_strategy_nine_breakout(
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
            start_date=(now - timedelta(days=360)).strftime("%Y-%m-%d"),
            end_date=end_date,
            adjust="hfq",
        )
        signal = analyze_strategy_nine_breakout(daily, now=now)
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
    results.sort(key=lambda item: (-_safe_float(item.get("signal_score")), _safe_float(item.get("distance_to_prior_high_pct", 999)), str(item.get("code"))))
    return results
