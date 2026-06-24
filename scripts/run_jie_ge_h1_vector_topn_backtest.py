from __future__ import annotations

import argparse, json, sys
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
from backtest.evaluation.hold_return import evaluate_signal_dynamic_exit, evaluate_signal_hold_return, summarize_evaluated_trades
from backtest.filters.no_touch_filters import check_no_touch_filters
from backtest.strategies.kaipanla_sector_strength_score import score_sector_strength_safe
from backtest.strategies.strategy_d_shen_trend_pullback import is_risky_stock_name
from backtest.strategies.strategy_jie_ge_emotion_dragon_pullback import (
    CORE_HIGH_LOOKBACK,
    DEFAULT_THEME_HEAT_SCORE,
    MAX_CLOSE_ABOVE_MA13_PCT,
    MAX_PULLBACK_FROM_HIGH_PCT,
    MAX_PULLBACK_TO_MA13_PCT,
    MIN_AVG10_DAILY_AMOUNT,
    MIN_DAILY_BARS,
    MIN_INTRADAY_REBOUND_SCORE,
    MIN_LATEST_DAILY_AMOUNT,
    MIN_RECENT_SURGE_PCT,
    MIN_VOLUME_RATIO,
    MOMENTUM_LOOKBACK,
    PULLBACK_LOOKBACK,
    check_jie_ge_high_position_second_wave_exclusion,
)
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import INTRADAY_MA, MIN_MINUTE_BARS
from backtest.workflows.selection_workflow import check_sector_strength_top_n, workflow_final_score


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _norm_date(value: str) -> str:
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text[:10]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def prepare_daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["high60"] = frame["high"].rolling(CORE_HIGH_LOOKBACK, min_periods=1).max()
    frame["low20"] = frame["low"].rolling(MOMENTUM_LOOKBACK, min_periods=1).min()
    frame["high20"] = frame["high"].rolling(MOMENTUM_LOOKBACK, min_periods=1).max()
    frame["avg10_amount"] = frame["amount"].rolling(10, min_periods=1).mean()
    frame["avg5_volume"] = frame["volume"].rolling(5, min_periods=1).mean()
    frame["avg20_volume"] = frame["volume"].rolling(20, min_periods=1).mean()
    frame["last10_low"] = frame["low"].rolling(PULLBACK_LOOKBACK, min_periods=1).min()
    frame["daily_bars"] = range(1, len(frame) + 1)
    return frame


def daily_context(row: pd.Series) -> dict[str, Any] | None:
    if int(row.get("daily_bars", 0)) < MIN_DAILY_BARS:
        return None
    close = _safe_float(row.get("close"))
    if close <= 0:
        return None
    high60 = _safe_float(row.get("high60"))
    low20 = _safe_float(row.get("low20"))
    high20 = _safe_float(row.get("high20"))
    surge_pct = (high20 / low20 - 1.0) * 100 if low20 > 0 else 0.0
    core_position_ratio = close / high60 if high60 > 0 else 0.0
    pullback_pct = (high20 / close - 1.0) * 100 if high20 > 0 else 999.0
    latest_amount = _safe_float(row.get("amount"))
    avg10_amount = _safe_float(row.get("avg10_amount"))
    volume_ratio = _safe_float(row.get("avg5_volume")) / _safe_float(row.get("avg20_volume"), 1.0)
    last10_low = _safe_float(row.get("last10_low"))
    pullback_control_ok = close >= last10_low * 1.03 if last10_low > 0 else True
    surge_ok = surge_pct >= MIN_RECENT_SURGE_PCT
    core_position_ok = core_position_ratio >= 0.72
    pullback_ok = 1.0 <= pullback_pct <= MAX_PULLBACK_FROM_HIGH_PCT
    liquidity_ok = latest_amount >= MIN_LATEST_DAILY_AMOUNT and avg10_amount >= MIN_AVG10_DAILY_AMOUNT
    volume_ok = volume_ratio >= MIN_VOLUME_RATIO
    second_wave_ok, second_wave_meta = check_jie_ge_high_position_second_wave_exclusion(row.attrs.get("daily_rows", pd.DataFrame()))
    score = 35.0 + min(22.0, max(0.0, surge_pct / 1.5)) + min(18.0, max(0.0, (core_position_ratio - 0.6) * 90.0))
    score += 12.0 if pullback_ok else -8.0
    score += 10.0 if liquidity_ok else -15.0
    score += 8.0 if volume_ok else -6.0
    score += 5.0 if pullback_control_ok else -8.0
    score += 0.0 if second_wave_ok else -25.0
    final_score = round(max(0.0, min(100.0, score)), 2)
    if not all([surge_ok, core_position_ok, pullback_ok, liquidity_ok, volume_ok, pullback_control_ok, second_wave_ok]):
        return None
    return {
        "jie_ge_context_reason": "jie_ge_dragon_context_ok",
        "jie_ge_core_score": final_score,
        "recent_surge_pct": round(surge_pct, 4),
        "core_position_ratio": round(core_position_ratio, 4),
        "pullback_from_recent_high_pct": round(pullback_pct, 4),
        "daily_latest_amount": round(latest_amount, 2),
        "daily_avg10_amount": round(avg10_amount, 2),
        "daily_volume_ratio_5_20": round(volume_ratio, 4),
        **second_wave_meta,
    }


def prepare_minute(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["trade_dt"] = pd.to_datetime(frame["trade_dt"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["ma13"] = frame["close"].rolling(INTRADAY_MA).mean()
    frame["ma30"] = frame["close"].rolling(30).mean()
    frame["prev_ma13"] = frame["ma13"].shift(1)
    frame["vol_ma20"] = frame["volume"].rolling(20, min_periods=5).mean()
    frame["touched_support"] = frame["low"] <= frame[["ma13", "ma30"]].max(axis=1) * (1 + MAX_PULLBACK_TO_MA13_PCT)
    frame["recovered"] = (frame["close"] >= frame["ma13"]) & (frame["close"] <= frame["ma13"] * (1 + MAX_CLOSE_ABOVE_MA13_PCT))
    frame["ma13_slope_ok"] = frame["ma13"] >= frame["prev_ma13"]
    frame["bullish_body"] = (frame["close"] >= frame["open"]) & (frame["close"] >= frame["low"] + (frame["high"] - frame["low"]) * 0.6)
    frame["volume_recover"] = frame["volume"] >= frame["vol_ma20"] * 0.75
    frame["rebound_score"] = 45.0 + frame["touched_support"].astype(float) * 15 + frame["recovered"].astype(float) * 15 + frame["ma13_slope_ok"].astype(float) * 10 + frame["bullish_body"].astype(float) * 10 + frame["volume_recover"].astype(float) * 5
    frame["signal_ok"] = frame["touched_support"] & frame["recovered"] & frame["bullish_body"] & frame["volume_recover"] & (frame["rebound_score"] >= MIN_INTRADAY_REBOUND_SCORE)
    frame.loc[range(min(MIN_MINUTE_BARS - 1, len(frame))), "signal_ok"] = False
    return frame


def signal_from_row(row: pd.Series, trade_date: pd.Timestamp, meta: dict[str, Any]) -> dict[str, Any]:
    trade_dt = pd.to_datetime(row["trade_dt"])
    close = _safe_float(row.get("close"))
    ma13 = _safe_float(row.get("ma13"))
    ma30 = _safe_float(row.get("ma30"))
    return {
        **meta,
        "buy_date": str(trade_dt.date()),
        "signal_time": trade_dt.isoformat(),
        "signal_price": round(close, 4),
        "signal_score": round(min(100.0, _safe_float(row.get("rebound_score"))), 2),
        "signal_reason": "jie_ge_emotion_dragon_pullback",
        "structure_period": "30",
        "structure_freq": "30分钟",
        "ma13_30m": round(ma13, 4),
        "ma30_30m": round(ma30, 4),
        "theme": "杰哥龙头低吸",
        "theme_heat_score": DEFAULT_THEME_HEAT_SCORE,
        "strategy_version": "jie-ge-emotion-dragon-pullback-v1",
        "days_ago": max(0, (trade_date.normalize() - trade_dt.normalize()).days),
    }


def check_recent_distribution_risk(daily_rows: pd.DataFrame, *, lookback_days: int = 3) -> tuple[bool, dict[str, Any]]:
    if daily_rows is None or daily_rows.empty:
        return True, {"jie_ge_distribution_risk_ok": True}
    data = daily_rows.copy()
    data["prev_close"] = data["close"].shift(1)
    data["vol_ma5"] = data["volume"].rolling(5, min_periods=1).mean()
    recent = data.tail(lookback_days)
    for _, row in recent.iterrows():
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        open_price = _safe_float(row.get("open"))
        volume = _safe_float(row.get("volume"))
        vol_ma5 = _safe_float(row.get("vol_ma5"))
        prev_close = _safe_float(row.get("prev_close"))
        day_range = high - low
        upper_shadow_ratio = (high - max(open_price, close)) / day_range if day_range > 0 else 0.0
        volume_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0
        ret_pct = (close / prev_close - 1.0) * 100 if prev_close > 0 else 0.0
        if volume_ratio >= 2.0 and upper_shadow_ratio >= 0.45 and close < high * 0.97:
            return False, {
                "jie_ge_distribution_risk_ok": False,
                "jie_ge_distribution_risk_reason": "high_volume_upper_shadow",
                "jie_ge_distribution_risk_date": str(pd.to_datetime(row.get("trade_date") or "1970-01-01").date()),
                "jie_ge_distribution_volume_ratio": round(volume_ratio, 4),
                "jie_ge_distribution_upper_shadow_ratio": round(upper_shadow_ratio, 4),
            }
        if ret_pct <= -4.0 and volume_ratio >= 0.9:
            return False, {
                "jie_ge_distribution_risk_ok": False,
                "jie_ge_distribution_risk_reason": "heavy_bearish_breakdown",
                "jie_ge_distribution_risk_date": str(pd.to_datetime(row.get("trade_date") or "1970-01-01").date()),
                "jie_ge_distribution_ret_pct": round(ret_pct, 4),
                "jie_ge_distribution_volume_ratio": round(volume_ratio, 4),
            }
    return True, {"jie_ge_distribution_risk_ok": True}


def enrich(signal: dict[str, Any], code: str, name: str, trade_date: str, min_sector: float, min_final: float, daily: pd.DataFrame | None = None, require_sector_strength_top_n: int = 10) -> dict[str, Any] | None:
    scored = score_sector_strength_safe(code=code, buy_date=trade_date, sector_name=signal.get("theme"), lookback_trade_days=10)
    enriched = {**signal, **scored, "strategy_id": "jie_ge_emotion_dragon_pullback", "strategy_name": "杰哥情绪龙头低吸", "code": code, "name": name}
    top_n_ok, top_n_meta = check_sector_strength_top_n(candidate_sectors=enriched.get("kaipanla_candidate_sectors") or [], trade_date=str(enriched.get("buy_date") or trade_date), top_n=require_sector_strength_top_n)
    enriched.update(top_n_meta)
    if not top_n_ok:
        return None
    no_touch_ok, no_touch_meta = check_no_touch_filters(
        code=code,
        buy_date=str(enriched.get("buy_date") or trade_date),
        daily=daily,
        enforce_sector_top3=False,
    )
    enriched.update(no_touch_meta)
    if not no_touch_ok:
        return None
    final_score, breakdown = workflow_final_score(enriched)
    enriched["workflow_final_score"] = final_score
    enriched["workflow_score_breakdown"] = breakdown
    if _safe_float(enriched.get("kaipanla_strength_score")) < min_sector or final_score < min_final:
        return None
    return enriched


def run(args: argparse.Namespace) -> dict[str, Any]:
    start = _norm_date(args.start_date)
    end = _norm_date(args.end_date)
    daily_preload = str((pd.to_datetime(start) - pd.Timedelta(days=args.daily_preload_days)).date())
    minute_preload = str((pd.to_datetime(start) - pd.Timedelta(days=args.minute_preload_days)).date())
    provider = DuckDBMarketDataProvider()
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_counts: dict[str, int] = defaultdict(int)
    rejected_counts: dict[str, int] = defaultdict(int)
    total_candidates = 0
    total_raw = 0
    total_rejected = 0
    last_selected_by_code: dict[str, pd.Timestamp] = {}
    with duckdb.connect(args.db_path, read_only=True) as con:
        trade_dates = [pd.to_datetime(x) for x in con.execute("select distinct trade_date from daily_kline where adjust='hfq' and trade_date between ? and ? order by trade_date", [start, end]).df()["trade_date"].tolist()]
        stocks = con.execute("select code, name from stock_basic order by code" + (f" limit {args.max_stocks}" if args.max_stocks > 0 else "")).df()
        total = len(stocks)
        for idx, (_, stock) in enumerate(stocks.iterrows(), start=1):
            code = str(stock.get("code") or "").zfill(6)
            name = str(stock.get("name") or code)
            before_candidates = total_candidates
            status = "ok"
            if is_risky_stock_name(name):
                status = "skip_risky_name"
                print(f"[{idx}/{total}] {code} {name} status={status} candidates={before_candidates}", flush=True)
                continue
            daily = prepare_daily(con.execute("select trade_date,open,high,low,close,volume,amount,turnover_rate,change_pct from daily_kline where code=? and adjust='hfq' and trade_date between ? and ? order by trade_date", [code, daily_preload, end]).df())
            minute = prepare_minute(con.execute("select trade_dt,open,high,low,close,volume,amount from minute_kline where code=? and period='30' and trade_dt between ? and ? order by trade_dt", [code, f"{minute_preload} 00:00:00", f"{end} 23:59:59"]).df())
            if daily.empty or minute.empty:
                status = "skip_missing_kline"
                print(f"[{idx}/{total}] {code} {name} status={status} daily_rows={len(daily)} minute_rows={len(minute)} candidates={before_candidates}", flush=True)
                continue
            signal_rows = minute[minute["signal_ok"]]
            if signal_rows.empty:
                status = "skip_no_30m_rebound"
                print(f"[{idx}/{total}] {code} {name} status={status} daily_rows={len(daily)} minute_rows={len(minute)} candidates={before_candidates}", flush=True)
                continue
            for trade_date in trade_dates:
                daily_rows = daily[daily["trade_date"] <= trade_date]
                if daily_rows.empty:
                    continue
                context_row = daily_rows.iloc[-1].copy()
                context_row.attrs["daily_rows"] = daily_rows
                meta = daily_context(context_row)
                if not meta:
                    continue
                cooldown_date = last_selected_by_code.get(code)
                if cooldown_date is not None and (trade_date - cooldown_date).days < args.stock_cooldown_days:
                    continue
                risk_ok, risk_meta = check_recent_distribution_risk(daily_rows)
                if not risk_ok:
                    key = str(trade_date.date())
                    raw_counts[key] += 1
                    rejected_counts[key] += 1
                    total_raw += 1
                    total_rejected += 1
                    continue
                eligible = signal_rows[(signal_rows["trade_dt"] <= trade_date + pd.Timedelta(hours=23, minutes=59, seconds=59)) & (signal_rows["trade_dt"] >= trade_date - pd.Timedelta(days=args.signal_window_days))]
                if eligible.empty:
                    continue
                key = str(trade_date.date())
                raw_counts[key] += 1
                total_raw += 1
                candidate = enrich(signal_from_row(eligible.iloc[-1], trade_date, meta), code, name, key, args.min_sector_score, args.min_final_score, daily=daily, require_sector_strength_top_n=args.require_sector_strength_top_n)
                if candidate:
                    candidate.update(risk_meta)
                    candidates[key].append(candidate)
                    total_candidates += 1
                    last_selected_by_code[code] = trade_date
                else:
                    rejected_counts[key] += 1
                    total_rejected += 1
            after_candidates = total_candidates
            added = after_candidates - before_candidates
            print(f"[{idx}/{total}] {code} {name} status={status} added={added} candidates={after_candidates} raw={total_raw} rejected={total_rejected}", flush=True)
    trades = []
    daily_results = []
    for index, trade_date in enumerate([str(d.date()) for d in trade_dates], start=1):
        selected = sorted(candidates[trade_date], key=lambda x: (-_safe_float(x.get("workflow_final_score")), -_safe_float(x.get("kaipanla_strength_score")), -_safe_float(x.get("signal_score")), x.get("code")))[: args.top_n]
        day_trades = []
        for signal in selected:
            evaluation = evaluate_signal_hold_return(signal, provider=provider, hold_days=args.hold_days, adjust="hfq")
            dynamic_evaluation = evaluate_signal_dynamic_exit(signal, provider=provider, hold_days=args.hold_days, take_profit_pct=args.take_profit_pct, stop_loss_pct=args.stop_loss_pct, adjust="hfq")
            trade = {
                "strategy_id": signal.get("strategy_id"),
                "strategy_name": signal.get("strategy_name"),
                "code": signal.get("code"),
                "name": signal.get("name"),
                "buy_date": signal.get("buy_date") or trade_date,
                "backtest_trade_date": trade_date,
                "signal_price": signal.get("signal_price"),
                "signal_score": signal.get("signal_score"),
                "theme": signal.get("theme"),
                "structure_period": signal.get("structure_period"),
                "workflow_final_score": signal.get("workflow_final_score"),
                "score_snapshot": {
                    "kaipanla_strength_score": signal.get("kaipanla_strength_score"),
                    "kaipanla_strength_grade": signal.get("kaipanla_strength_grade"),
                    "kaipanla_candidate_sectors": signal.get("kaipanla_candidate_sectors"),
                    "stock_sector_membership_count": signal.get("stock_sector_membership_count"),
                },
                **evaluation,
                **dynamic_evaluation,
            }
            day_trades.append(trade)
            trades.append(trade)
        daily_results.append({"trade_date": trade_date, "index": index, "workflow_counts": {"raw_signals": raw_counts[trade_date], "selected": len(selected), "rejected": rejected_counts[trade_date] + max(0, len(candidates[trade_date]) - len(selected))}, "selected_count": len(selected), "rejected_count": rejected_counts[trade_date] + max(0, len(candidates[trade_date]) - len(selected)), "summary": summarize_evaluated_trades(day_trades)})
    summary = summarize_evaluated_trades(trades)
    return {"backtest": "jie-ge-vector-topn-h1-v1", "generated_at": datetime.now().isoformat(timespec="seconds"), "config": vars(args), "counts": {"trade_days": len(trade_dates), "trades": len(trades), "evaluated": summary.get("evaluated", 0), "data_missing": summary.get("data_missing", 0)}, "summary": summary, "daily_results": daily_results, "trades": trades}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2025-06-30")
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-stocks", type=int, default=5200)
    parser.add_argument("--signal-window-days", type=int, default=10)
    parser.add_argument("--min-sector-score", type=float, default=40.0)
    parser.add_argument("--min-final-score", type=float, default=0.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--require-sector-strength-top-n", type=int, default=10)
    parser.add_argument("--db-path", default="data/ashare.duckdb")
    parser.add_argument("--output", default="/tmp/jie_ge_vector_top5_h1.json")
    parser.add_argument("--daily-preload-days", type=int, default=560)
    parser.add_argument("--minute-preload-days", type=int, default=90)
    parser.add_argument("--stock-cooldown-days", type=int, default=20)
    parser.add_argument("--take-profit-pct", type=float, default=6.0)
    parser.add_argument("--stop-loss-pct", type=float, default=-4.0)
    parser.add_argument("--progress-every", type=int, default=1, help="Deprecated: progress is printed for every stock.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = run(args)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(output_path)
    print(json.dumps({"counts": output["counts"], "summary": output["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
