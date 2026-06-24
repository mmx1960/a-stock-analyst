from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data.market_data_provider import DuckDBMarketDataProvider
from backtest.evaluation.hold_return import evaluate_signal_hold_return, summarize_evaluated_trades
from backtest.strategies.kaipanla_sector_strength_score import score_sector_strength_safe
from backtest.strategies.strategy_d_shen_trend_pullback import (
    DAILY_MA_FAST,
    DAILY_MA_LONG,
    DAILY_MA_MID,
    DAILY_MA_SLOW,
    DEFAULT_THEME_HEAT_SCORE,
    MAX_30M_CLOSE_ABOVE_MA13_PCT,
    MAX_30M_PULLBACK_TO_MA13_PCT,
    MAX_DAILY_EXTENSION_FROM_MA20_PCT,
    MIN_30M_MA_CONVERGENCE_SCORE,
    MIN_AVG20_DAILY_AMOUNT,
    MIN_DAILY_BARS,
    MIN_DAILY_TREND_SCORE,
    MIN_LATEST_DAILY_AMOUNT,
    is_risky_stock_name,
)
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import INTRADAY_MA, MIN_MINUTE_BARS
from backtest.workflows.selection_workflow import workflow_final_score


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _norm_date(value: str) -> str:
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def load_trade_dates(con: duckdb.DuckDBPyConnection, start: str, end: str) -> list[pd.Timestamp]:
    frame = con.execute(
        """
        select distinct trade_date
        from daily_kline
        where adjust='hfq' and trade_date between ? and ?
        order by trade_date
        """,
        [start, end],
    ).df()
    return [pd.to_datetime(v) for v in frame["trade_date"].tolist()]


def load_stock_list(con: duckdb.DuckDBPyConnection, limit: int) -> pd.DataFrame:
    sql = "select code, name from stock_basic order by code"
    if limit > 0:
        sql += f" limit {int(limit)}"
    return con.execute(sql).df()


def load_daily(con: duckdb.DuckDBPyConnection, code: str, start: str, end: str) -> pd.DataFrame:
    return con.execute(
        """
        select trade_date, open, high, low, close, volume, amount
        from daily_kline
        where code=? and adjust='hfq' and trade_date between ? and ?
        order by trade_date
        """,
        [code, start, end],
    ).df()


def load_minute(con: duckdb.DuckDBPyConnection, code: str, start: str, end: str) -> pd.DataFrame:
    return con.execute(
        """
        select trade_dt, open, high, low, close, volume, amount
        from minute_kline
        where code=? and period='30' and trade_dt between ? and ?
        order by trade_dt
        """,
        [code, f"{start} 00:00:00", f"{end} 23:59:59"],
    ).df()


def prepare_daily(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily
    frame = daily.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["ma20"] = frame["close"].rolling(DAILY_MA_FAST).mean()
    frame["ma30"] = frame["close"].rolling(DAILY_MA_MID).mean()
    frame["ma60"] = frame["close"].rolling(DAILY_MA_SLOW).mean()
    frame["ma120"] = frame["close"].rolling(DAILY_MA_LONG, min_periods=60).mean()
    frame["prev_ma20_5"] = frame["ma20"].shift(5)
    frame["prev_ma60_20"] = frame["ma60"].shift(20)
    frame["recent_high_120"] = frame["high"].rolling(120, min_periods=1).max()
    frame["recent_low_60"] = frame["low"].rolling(60, min_periods=1).min()
    frame["avg20_amount"] = frame["amount"].rolling(20, min_periods=1).mean()
    frame["daily_bars"] = range(1, len(frame) + 1)
    return frame


def daily_context(row: pd.Series) -> tuple[bool, dict[str, Any]]:
    if int(row.get("daily_bars", 0)) < MIN_DAILY_BARS:
        return False, {"d_shen_context_reason": "daily_data_insufficient", "daily_bars": int(row.get("daily_bars", 0))}
    close = _safe_float(row.get("close"))
    ma20 = _safe_float(row.get("ma20"))
    ma30 = _safe_float(row.get("ma30"))
    ma60 = _safe_float(row.get("ma60"))
    ma120 = _safe_float(row.get("ma120"))
    prev_ma20 = _safe_float(row.get("prev_ma20_5"))
    prev_ma60 = _safe_float(row.get("prev_ma60_20"))
    if min(close, ma20, ma30, ma60) <= 0:
        return False, {"d_shen_context_reason": "daily_ma_unavailable", "daily_bars": int(row.get("daily_bars", 0))}
    alignment_ok = close > ma20 > ma30 > ma60
    ma20_slope_ok = ma20 >= prev_ma20 if prev_ma20 > 0 else True
    ma60_slope_ok = ma60 >= prev_ma60 if prev_ma60 > 0 else True
    ma120_ok = close >= ma120 if ma120 > 0 else True
    recent_high_120 = _safe_float(row.get("recent_high_120"))
    recent_low_60 = _safe_float(row.get("recent_low_60"))
    monthly_space_pct = (recent_high_120 / close - 1.0) * 100 if close > 0 and recent_high_120 > 0 else 0.0
    drawdown_control_ok = close >= recent_low_60 * 1.08 if recent_low_60 > 0 else True
    extension_pct = (close / ma20 - 1.0) * 100 if ma20 > 0 else 999.0
    not_overextended = extension_pct <= MAX_DAILY_EXTENSION_FROM_MA20_PCT
    latest_amount = _safe_float(row.get("amount"))
    avg20_amount = _safe_float(row.get("avg20_amount"))
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
        "daily_extension_from_ma20_pct": round(extension_pct, 4),
        "daily_latest_amount": round(latest_amount, 2),
        "daily_avg20_amount": round(avg20_amount, 2),
        "daily_liquidity_ok": bool(liquidity_ok),
        "daily_bars": int(row.get("daily_bars", 0)),
    }


def prepare_minute(minute: pd.DataFrame) -> pd.DataFrame:
    if minute.empty:
        return minute
    frame = minute.copy()
    frame["trade_dt"] = pd.to_datetime(frame["trade_dt"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["ma5"] = frame["close"].rolling(5).mean()
    frame["ma13"] = frame["close"].rolling(INTRADAY_MA).mean()
    frame["ma30"] = frame["close"].rolling(30).mean()
    frame["prev_ma13"] = frame["ma13"].shift(1)
    min_ma = frame[["ma5", "ma13", "ma30"]].min(axis=1)
    max_ma = frame[["ma5", "ma13", "ma30"]].max(axis=1)
    frame["ma_convergence_pct"] = (max_ma / min_ma - 1.0) * 100
    frame["ma_convergence_score"] = (100.0 - frame["ma_convergence_pct"] * 18.0).clip(lower=0)
    frame["touched_ma13"] = frame["low"] <= frame["ma13"] * (1 + MAX_30M_PULLBACK_TO_MA13_PCT)
    frame["recovered_ma13"] = (frame["close"] >= frame["ma13"]) & (frame["close"] <= frame["ma13"] * (1 + MAX_30M_CLOSE_ABOVE_MA13_PCT))
    frame["ma13_slope_ok"] = frame["ma13"] >= frame["prev_ma13"]
    frame["bullish_recover"] = (frame["close"] >= frame["open"]) & (frame["close"] >= (frame["low"] + (frame["high"] - frame["low"]) * 0.55))
    frame["ma_convergence_ok"] = frame["ma_convergence_score"] >= MIN_30M_MA_CONVERGENCE_SCORE
    frame["ma_order_ok"] = (frame["ma5"] >= frame["ma13"] * 0.985) & (frame["ma13"] >= frame["ma30"] * 0.985)
    frame["signal_ok"] = frame["touched_ma13"] & frame["recovered_ma13"] & frame["ma13_slope_ok"] & frame["bullish_recover"] & frame["ma_convergence_ok"] & frame["ma_order_ok"]
    frame.loc[range(min(MIN_MINUTE_BARS - 1, len(frame))), "signal_ok"] = False
    return frame


def signal_from_minute_row(row: pd.Series, trade_date: pd.Timestamp) -> dict[str, Any]:
    trade_dt = pd.to_datetime(row["trade_dt"])
    close = _safe_float(row.get("close"))
    ma13 = _safe_float(row.get("ma13"))
    ma5 = _safe_float(row.get("ma5"))
    ma30 = _safe_float(row.get("ma30"))
    low = _safe_float(row.get("low"))
    signal_score = 50.0
    signal_score += min(16.0, max(0.0, _safe_float(row.get("ma_convergence_score")) / 100.0 * 16.0))
    signal_score += 12.0 if bool(row.get("ma13_slope_ok")) else 0.0
    signal_score += 10.0 if bool(row.get("bullish_recover")) else 0.0
    signal_score += 8.0 if bool(row.get("ma_order_ok")) else 0.0
    signal_score += min(4.0, max(0.0, (close / ma13 - 1.0) * 200.0)) if ma13 > 0 else 0.0
    return {
        "buy_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": round(min(100.0, signal_score), 2),
        "signal_reason": "d_shen_trend_30m_pullback",
        "structure_period": "30",
        "structure_freq": "30分钟",
        "ma5_30m": round(ma5, 4) if ma5 > 0 else None,
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "pullback_low": round(low, 4),
        "close_to_ma13_pct": round((close / ma13 - 1.0) * 100.0, 4) if ma13 > 0 else None,
        "ma13_slope_ok": bool(row.get("ma13_slope_ok")),
        "ma_order_ok": bool(row.get("ma_order_ok")),
        "ma_convergence_score": round(_safe_float(row.get("ma_convergence_score")), 2),
        "ma_convergence_pct": round(_safe_float(row.get("ma_convergence_pct")), 4),
        "bullish_recover": bool(row.get("bullish_recover")),
        "days_ago": max(0, (trade_date.normalize() - trade_dt.normalize()).days),
    }


def enrich_signal(signal: dict[str, Any], *, code: str, name: str, trade_date: str, min_sector_score: float, min_final_score: float) -> dict[str, Any] | None:
    scored = score_sector_strength_safe(code=code, buy_date=trade_date, sector_name=signal.get("theme"), lookback_trade_days=10)
    enriched = {
        **signal,
        **scored,
        "strategy_id": "d_shen_trend_30m_pullback",
        "strategy_name": "D神趋势 + 板块资金 + 30分钟回踩",
        "code": code,
        "name": name,
    }
    final_score, breakdown = workflow_final_score(enriched)
    enriched["workflow_final_score"] = final_score
    enriched["workflow_score_breakdown"] = breakdown
    reject_reasons = []
    if float(enriched.get("kaipanla_strength_score") or 0) < min_sector_score:
        reject_reasons.append("sector_score_below_threshold")
    if final_score < min_final_score:
        reject_reasons.append("final_score_below_threshold")
    enriched["workflow_reject_reasons"] = reject_reasons
    return None if reject_reasons else enriched


def run(args: argparse.Namespace) -> dict[str, Any]:
    start = _norm_date(args.start_date)
    end = _norm_date(args.end_date)
    preload_start = str((pd.to_datetime(start) - pd.Timedelta(days=args.signal_window_days + 520)).date())
    provider = DuckDBMarketDataProvider()
    trades: list[dict[str, Any]] = []
    raw_counts = defaultdict(int)
    selected_counts = defaultdict(int)
    rejected_counts = defaultdict(int)
    with duckdb.connect(args.db_path, read_only=True) as con:
        trade_dates = load_trade_dates(con, start, end)
        stocks = load_stock_list(con, args.max_stocks)
        total = len(stocks)
        for idx, (_, stock) in enumerate(stocks.iterrows(), start=1):
            code = str(stock.get("code") or "").zfill(6)
            name = str(stock.get("name") or code)
            if is_risky_stock_name(name):
                continue
            daily = prepare_daily(load_daily(con, code, preload_start, end))
            minute = prepare_minute(load_minute(con, code, preload_start, end))
            if daily.empty or minute.empty:
                continue
            signal_rows = minute[minute["signal_ok"]].copy()
            if signal_rows.empty:
                continue
            for trade_date in trade_dates:
                daily_rows = daily[daily["trade_date"] <= trade_date]
                if daily_rows.empty:
                    continue
                context_ok, context_meta = daily_context(daily_rows.iloc[-1])
                if not context_ok:
                    continue
                min_signal_dt = trade_date - pd.Timedelta(days=args.signal_window_days)
                eligible = signal_rows[(signal_rows["trade_dt"] <= trade_date + pd.Timedelta(hours=23, minutes=59, seconds=59)) & (signal_rows["trade_dt"] >= min_signal_dt)]
                if eligible.empty:
                    continue
                signal = {**signal_from_minute_row(eligible.iloc[-1], trade_date), **context_meta}
                signal["theme"] = "D神趋势回踩"
                signal["theme_heat_score"] = DEFAULT_THEME_HEAT_SCORE
                signal["strategy_version"] = "d-shen-trend-30m-pullback-v1"
                raw_counts[str(trade_date.date())] += 1
                enriched = enrich_signal(signal, code=code, name=name, trade_date=str(trade_date.date()), min_sector_score=args.min_sector_score, min_final_score=args.min_final_score)
                if not enriched:
                    rejected_counts[str(trade_date.date())] += 1
                    continue
                selected_counts[str(trade_date.date())] += 1
                evaluation = evaluate_signal_hold_return(enriched, provider=provider, hold_days=args.hold_days, adjust="hfq")
                trades.append({
                    "strategy_id": enriched.get("strategy_id"),
                    "strategy_name": enriched.get("strategy_name"),
                    "code": code,
                    "name": name,
                    "buy_date": enriched.get("buy_date") or str(trade_date.date()),
                    "backtest_trade_date": str(trade_date.date()),
                    "signal_price": enriched.get("signal_price"),
                    "signal_score": enriched.get("signal_score"),
                    "theme": enriched.get("theme"),
                    "structure_period": enriched.get("structure_period"),
                    "workflow_final_score": enriched.get("workflow_final_score"),
                    "score_snapshot": {
                        "kaipanla_strength_score": enriched.get("kaipanla_strength_score"),
                        "kaipanla_strength_grade": enriched.get("kaipanla_strength_grade"),
                        "kaipanla_candidate_sectors": enriched.get("kaipanla_candidate_sectors"),
                        "stock_sector_membership_count": enriched.get("stock_sector_membership_count"),
                    },
                    **evaluation,
                })
            if idx % args.progress_every == 0:
                print(f"[{idx}/{total}] trades={len(trades)}")
    daily_results = []
    for i, trade_date in enumerate(trade_dates, start=1):
        key = str(trade_date.date())
        day_trades = [t for t in trades if t["backtest_trade_date"] == key]
        daily_results.append({
            "trade_date": key,
            "index": i,
            "workflow_counts": {"raw_signals": raw_counts[key], "selected": selected_counts[key], "rejected": rejected_counts[key]},
            "selected_count": selected_counts[key],
            "rejected_count": rejected_counts[key],
            "summary": summarize_evaluated_trades(day_trades),
        })
    summary = summarize_evaluated_trades(trades)
    return {
        "backtest": "d-shen-vector-h1-backtest-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": vars(args),
        "trade_dates": [str(d.date()) for d in trade_dates],
        "counts": {"trade_days": len(trade_dates), "trades": len(trades), "evaluated": summary.get("evaluated", 0), "data_missing": summary.get("data_missing", 0)},
        "summary": summary,
        "daily_results": daily_results,
        "trades": trades,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vectorized D神 H1 backtest using DB bars")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2025-06-30")
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-stocks", type=int, default=5200)
    parser.add_argument("--signal-window-days", type=int, default=10)
    parser.add_argument("--min-sector-score", type=float, default=40.0)
    parser.add_argument("--min-final-score", type=float, default=0.0)
    parser.add_argument("--db-path", default="data/ashare.duckdb")
    parser.add_argument("--output", default="/tmp/d_shen_vector_h1_backtest.json")
    parser.add_argument("--progress-every", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = run(args)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(path)
    print(json.dumps({"counts": output["counts"], "summary": output["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
