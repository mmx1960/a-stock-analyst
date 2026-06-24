from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from app.core.data.market_data_provider import MarketDataProvider


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_date(value: Any) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return pd.to_datetime(value).normalize()
    except Exception:
        return None


def _normalize_daily_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    if "trade_date" not in data.columns:
        if "date" in data.columns:
            data["trade_date"] = data["date"]
        elif "datetime" in data.columns:
            data["trade_date"] = data["datetime"]
    if "trade_date" not in data.columns or "close" not in data.columns:
        return pd.DataFrame()
    data["trade_date"] = pd.to_datetime(data["trade_date"]).dt.normalize()
    for column in ["open", "high", "low", "close"]:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    return data.reset_index(drop=True)


def evaluate_signal_hold_return(
    signal: dict[str, Any],
    *,
    provider: MarketDataProvider,
    hold_days: int = 10,
    adjust: str = "hfq",
) -> dict[str, Any]:
    code = str(signal.get("code") or "").zfill(6)
    buy_date = _normalize_date(signal.get("buy_date"))
    if not code or buy_date is None:
        return {
            "evaluation_status": "data_missing",
            "data_missing_reasons": ["invalid_code_or_buy_date"],
        }

    end_date = buy_date + timedelta(days=max(hold_days * 3, hold_days + 10))
    bars = _normalize_daily_bars(
        provider.get_daily_bars(
            code=code,
            start_date=buy_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            adjust=adjust,
        )
    )
    if bars.empty:
        return {
            "evaluation_status": "data_missing",
            "data_missing_reasons": ["daily_bars_missing"],
        }

    future = bars[bars["trade_date"] >= buy_date].head(hold_days + 1)
    if len(future) < 2:
        return {
            "evaluation_status": "data_missing",
            "data_missing_reasons": ["insufficient_future_bars"],
            "available_future_bars": int(len(future)),
        }

    entry = future.iloc[0]
    hold_window = future.iloc[1:]
    entry_price = _safe_float(signal.get("signal_price"), default=0.0)
    if entry_price <= 0:
        entry_price = _safe_float(entry.get("close"))
    if entry_price <= 0:
        return {
            "evaluation_status": "data_missing",
            "data_missing_reasons": ["invalid_entry_price"],
        }

    high_series = hold_window["high"] if "high" in hold_window.columns else hold_window["close"]
    low_series = hold_window["low"] if "low" in hold_window.columns else hold_window["close"]
    close_series = hold_window["close"]
    max_return_pct = (_safe_float(high_series.max()) / entry_price - 1.0) * 100.0
    min_return_pct = (_safe_float(low_series.min()) / entry_price - 1.0) * 100.0
    close_return_pct = (_safe_float(close_series.iloc[-1]) / entry_price - 1.0) * 100.0
    avg_loss = abs(min(0.0, min_return_pct))
    risk_reward_ratio = round(max(0.0, max_return_pct) / avg_loss, 4) if avg_loss > 0 else None

    return {
        "evaluation_status": "evaluated",
        "hold_days": int(hold_days),
        "actual_hold_bars": int(len(hold_window)),
        "entry_trade_date": str(entry.get("trade_date"))[:10],
        "exit_trade_date": str(hold_window.iloc[-1].get("trade_date"))[:10],
        "entry_price": round(entry_price, 4),
        "max_return_pct": round(max_return_pct, 4),
        "min_return_pct": round(min_return_pct, 4),
        "close_return_pct": round(close_return_pct, 4),
        "win": bool(max_return_pct > 0),
        "hit_target": bool(max_return_pct >= 10.0),
        "hit_stop_loss": bool(min_return_pct <= -8.0),
        "risk_reward_ratio": risk_reward_ratio,
        "data_missing_reasons": [],
    }


def evaluate_signal_dynamic_exit(
    signal: dict[str, Any],
    *,
    provider: MarketDataProvider,
    hold_days: int = 10,
    take_profit_pct: float = 6.0,
    stop_loss_pct: float = -4.0,
    adjust: str = "hfq",
) -> dict[str, Any]:
    code = str(signal.get("code") or "").zfill(6)
    buy_date = _normalize_date(signal.get("buy_date"))
    if not code or buy_date is None:
        return {"dynamic_evaluation_status": "data_missing", "dynamic_exit_reason": "invalid_code_or_buy_date"}
    end_date = buy_date + timedelta(days=max(hold_days * 3, hold_days + 10))
    bars = _normalize_daily_bars(provider.get_daily_bars(code=code, start_date=buy_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"), adjust=adjust))
    if bars.empty:
        return {"dynamic_evaluation_status": "data_missing", "dynamic_exit_reason": "daily_bars_missing"}
    future = bars[bars["trade_date"] >= buy_date].head(hold_days + 1)
    if len(future) < 2:
        return {"dynamic_evaluation_status": "data_missing", "dynamic_exit_reason": "insufficient_future_bars"}
    entry = future.iloc[0]
    hold_window = future.iloc[1:]
    entry_price = _safe_float(signal.get("signal_price"), default=0.0)
    if entry_price <= 0:
        entry_price = _safe_float(entry.get("close"))
    if entry_price <= 0:
        return {"dynamic_evaluation_status": "data_missing", "dynamic_exit_reason": "invalid_entry_price"}
    exit_row = hold_window.iloc[-1]
    exit_price = _safe_float(exit_row.get("close"))
    exit_reason = "time_exit"
    for _, row in hold_window.iterrows():
        high_return = (_safe_float(row.get("high"), _safe_float(row.get("close"))) / entry_price - 1.0) * 100.0
        low_return = (_safe_float(row.get("low"), _safe_float(row.get("close"))) / entry_price - 1.0) * 100.0
        close_return = (_safe_float(row.get("close")) / entry_price - 1.0) * 100.0
        if high_return >= take_profit_pct:
            exit_row = row
            exit_price = entry_price * (1.0 + take_profit_pct / 100.0)
            exit_reason = "take_profit"
            break
        if low_return <= stop_loss_pct or close_return <= stop_loss_pct:
            exit_row = row
            exit_price = entry_price * (1.0 + stop_loss_pct / 100.0)
            exit_reason = "stop_loss"
            break
    dynamic_return_pct = (exit_price / entry_price - 1.0) * 100.0
    return {
        "dynamic_evaluation_status": "evaluated",
        "dynamic_exit_reason": exit_reason,
        "dynamic_exit_trade_date": str(exit_row.get("trade_date"))[:10],
        "dynamic_entry_price": round(entry_price, 4),
        "dynamic_exit_price": round(exit_price, 4),
        "dynamic_return_pct": round(dynamic_return_pct, 4),
        "dynamic_win": bool(dynamic_return_pct > 0),
        "dynamic_take_profit_pct": float(take_profit_pct),
        "dynamic_stop_loss_pct": float(stop_loss_pct),
    }


def summarize_evaluated_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [trade for trade in trades if trade.get("evaluation_status") == "evaluated"]
    missing = [trade for trade in trades if trade.get("evaluation_status") != "evaluated"]
    if not evaluated:
        return {
            "trades": len(trades),
            "evaluated": 0,
            "data_missing": len(missing),
            "win_rate": 0.0,
            "hit_target_rate": 0.0,
            "avg_max_return_pct": 0.0,
            "median_max_return_pct": 0.0,
            "avg_close_return_pct": 0.0,
            "avg_min_return_pct": 0.0,
        }
    max_returns = [_safe_float(trade.get("max_return_pct")) for trade in evaluated]
    close_returns = [_safe_float(trade.get("close_return_pct")) for trade in evaluated]
    min_returns = [_safe_float(trade.get("min_return_pct")) for trade in evaluated]
    wins = [1.0 if trade.get("win") else 0.0 for trade in evaluated]
    targets = [1.0 if trade.get("hit_target") else 0.0 for trade in evaluated]
    summary = {
        "trades": len(trades),
        "evaluated": len(evaluated),
        "data_missing": len(missing),
        "win_rate": round(sum(wins) / len(wins) * 100.0, 2),
        "hit_target_rate": round(sum(targets) / len(targets) * 100.0, 2),
        "avg_max_return_pct": round(sum(max_returns) / len(max_returns), 4),
        "median_max_return_pct": round(float(pd.Series(max_returns).median()), 4),
        "avg_close_return_pct": round(sum(close_returns) / len(close_returns), 4),
        "avg_min_return_pct": round(sum(min_returns) / len(min_returns), 4),
    }
    dynamic = [trade for trade in trades if trade.get("dynamic_evaluation_status") == "evaluated"]
    if dynamic:
        dynamic_returns = [_safe_float(trade.get("dynamic_return_pct")) for trade in dynamic]
        dynamic_wins = [1.0 if trade.get("dynamic_win") else 0.0 for trade in dynamic]
        summary.update({
            "dynamic_evaluated": len(dynamic),
            "dynamic_win_rate": round(sum(dynamic_wins) / len(dynamic_wins) * 100.0, 2),
            "avg_dynamic_return_pct": round(sum(dynamic_returns) / len(dynamic_returns), 4),
        })
    return summary
