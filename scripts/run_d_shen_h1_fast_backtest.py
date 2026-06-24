from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
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
    analyze_d_shen_trend_30m_pullback,
    is_risky_stock_name,
)
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


def load_trade_dates(con: duckdb.DuckDBPyConnection, start: str, end: str) -> list[str]:
    frame = con.execute(
        """
        select distinct trade_date
        from daily_kline
        where adjust='hfq' and trade_date between ? and ?
        order by trade_date
        """,
        [start, end],
    ).df()
    return [str(pd.to_datetime(v).date()) for v in frame["trade_date"].tolist()]


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


def enrich_signal(signal: dict[str, Any], *, code: str, name: str, trade_date: str, min_sector_score: float, min_final_score: float) -> dict[str, Any] | None:
    scored = score_sector_strength_safe(
        code=code,
        buy_date=trade_date,
        sector_name=signal.get("theme"),
        lookback_trade_days=10,
    )
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
    daily_results: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    raw_counts = defaultdict(int)
    selected_counts = defaultdict(int)
    rejected_counts = defaultdict(int)

    with duckdb.connect(args.db_path, read_only=True) as con:
        trade_dates = load_trade_dates(con, start, end)
        trade_ts = [pd.to_datetime(d) for d in trade_dates]
        stocks = load_stock_list(con, args.max_stocks)
        total = len(stocks)
        for idx, (_, row) in enumerate(stocks.iterrows(), start=1):
            code = str(row.get("code") or "").zfill(6)
            name = str(row.get("name") or code)
            if is_risky_stock_name(name):
                continue
            daily = load_daily(con, code, preload_start, end)
            if daily.empty:
                continue
            minute = load_minute(con, code, preload_start, end)
            if minute.empty:
                continue
            daily["trade_date"] = pd.to_datetime(daily["trade_date"])
            minute["trade_dt"] = pd.to_datetime(minute["trade_dt"])
            for trade_date, trade_dt in zip(trade_dates, trade_ts):
                daily_slice = daily.loc[daily["trade_date"] <= trade_dt].copy()
                minute_slice = minute.loc[minute["trade_dt"] <= trade_dt + pd.Timedelta(hours=23, minutes=59, seconds=59)].copy()
                signal = analyze_d_shen_trend_30m_pullback(daily_slice, minute_slice, now=trade_dt.to_pydatetime())
                if not signal:
                    continue
                if int(signal.get("days_ago", 999)) > args.signal_window_days:
                    continue
                raw_counts[trade_date] += 1
                enriched = enrich_signal(
                    signal,
                    code=code,
                    name=name,
                    trade_date=trade_date,
                    min_sector_score=args.min_sector_score,
                    min_final_score=args.min_final_score,
                )
                if not enriched:
                    rejected_counts[trade_date] += 1
                    continue
                selected_counts[trade_date] += 1
                evaluation = evaluate_signal_hold_return(
                    enriched,
                    provider=provider,
                    hold_days=args.hold_days,
                    adjust="hfq",
                )
                trade = {
                    "strategy_id": enriched.get("strategy_id"),
                    "strategy_name": enriched.get("strategy_name"),
                    "code": code,
                    "name": name,
                    "buy_date": enriched.get("buy_date") or trade_date,
                    "backtest_trade_date": trade_date,
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
                }
                trades.append(trade)
            if idx % args.progress_every == 0:
                print(f"[{idx}/{total}] trades={len(trades)} selected_days={sum(1 for v in selected_counts.values() if v)}")

    for i, trade_date in enumerate(trade_dates, start=1):
        day_trades = [t for t in trades if t["backtest_trade_date"] == trade_date]
        daily_results[trade_date] = {
            "trade_date": trade_date,
            "index": i,
            "workflow_counts": {
                "raw_signals": raw_counts[trade_date],
                "selected": selected_counts[trade_date],
                "rejected": rejected_counts[trade_date],
            },
            "selected_count": selected_counts[trade_date],
            "rejected_count": rejected_counts[trade_date],
            "summary": summarize_evaluated_trades(day_trades),
        }
    summary = summarize_evaluated_trades(trades)
    return {
        "backtest": "d-shen-fast-h1-backtest-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": vars(args),
        "trade_dates": trade_dates,
        "counts": {
            "trade_days": len(trade_dates),
            "trades": len(trades),
            "evaluated": summary.get("evaluated", 0),
            "data_missing": summary.get("data_missing", 0),
        },
        "summary": summary,
        "daily_results": list(daily_results.values()),
        "trades": trades,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fast D神 H1 backtest using preloaded DB bars")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2025-06-30")
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-stocks", type=int, default=5200)
    parser.add_argument("--signal-window-days", type=int, default=10)
    parser.add_argument("--min-sector-score", type=float, default=40.0)
    parser.add_argument("--min-final-score", type=float, default=0.0)
    parser.add_argument("--db-path", default="data/ashare.duckdb")
    parser.add_argument("--output", default="/tmp/d_shen_fast_h1_backtest.json")
    parser.add_argument("--progress-every", type=int, default=100)
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
