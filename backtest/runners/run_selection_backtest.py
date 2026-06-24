from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider
from backtest.evaluation.hold_return import evaluate_signal_hold_return, summarize_evaluated_trades
from backtest.strategies.registry import parse_strategy_ids
from backtest.workflows.selection_workflow import SelectionWorkflowConfig, run_selection_workflow

DEFAULT_OUTPUT = Path("backtest/results_v6/selection_backtest_smoke.json")


@dataclass(frozen=True)
class SelectionBacktestConfig:
    strategy_ids: list[str]
    start_date: str
    end_date: str
    hold_days: int = 10
    max_trade_days: int = 0
    max_stocks: int = 80
    min_heat_score: float = 45.0
    signal_window_days: int = 10
    pool_mode: str = "combined"
    board_lookback_days: int = 10
    board_top_n: int = 10
    board_min_appearances: int = 3
    throttle: float = 0.0
    sector_lookback_trade_days: int = 10
    min_sector_score: float = 65.0
    min_final_score: float = 45.0
    top_n: int = 30
    merge_mode: str = "best_score"
    adjust: str = "hfq"


def _normalize_date(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def resolve_trade_dates_from_daily_kline(
    *,
    start_date: str,
    end_date: str,
    db_path: str = "data/ashare.duckdb",
    max_trade_days: int = 0,
) -> list[str]:
    start = _normalize_date(start_date)
    end = _normalize_date(end_date)
    with duckdb.connect(db_path, read_only=True) as con:
        frame = con.execute(
            """
            SELECT DISTINCT trade_date
            FROM daily_kline
            WHERE trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
            """,
            [start, end],
        ).df()
    if frame.empty:
        return []
    dates = [str(pd.to_datetime(value).date()) for value in frame["trade_date"].tolist()]
    return dates[:max_trade_days] if max_trade_days > 0 else dates


def _workflow_config_for_date(config: SelectionBacktestConfig, trade_date: str) -> SelectionWorkflowConfig:
    return SelectionWorkflowConfig(
        strategy_ids=config.strategy_ids,
        max_stocks=config.max_stocks,
        min_heat_score=config.min_heat_score,
        signal_window_days=config.signal_window_days,
        pool_mode=config.pool_mode,
        board_lookback_days=config.board_lookback_days,
        board_top_n=config.board_top_n,
        board_min_appearances=config.board_min_appearances,
        throttle=config.throttle,
        sector_score_date=trade_date,
        as_of_date=trade_date,
        sector_lookback_trade_days=config.sector_lookback_trade_days,
        min_sector_score=config.min_sector_score,
        min_final_score=config.min_final_score,
        top_n=config.top_n,
        merge_mode=config.merge_mode,
    )


def _project_trade(signal: dict[str, Any], evaluation: dict[str, Any], trade_date: str) -> dict[str, Any]:
    return {
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
        "workflow_score_breakdown": signal.get("workflow_score_breakdown"),
        "workflow_reject_reasons": signal.get("workflow_reject_reasons"),
        "score_snapshot": {
            "kaipanla_strength_score": signal.get("kaipanla_strength_score"),
            "kaipanla_strength_grade": signal.get("kaipanla_strength_grade"),
            "kaipanla_candidate_sectors": signal.get("kaipanla_candidate_sectors"),
            "stock_sector_membership_count": signal.get("stock_sector_membership_count"),
        },
        **evaluation,
    }


def run_selection_backtest(
    config: SelectionBacktestConfig,
    *,
    provider: MarketDataProvider | None = None,
    trade_dates: list[str] | None = None,
) -> dict[str, Any]:
    provider = provider or DuckDBMarketDataProvider()
    dates = trade_dates or resolve_trade_dates_from_daily_kline(
        start_date=config.start_date,
        end_date=config.end_date,
        max_trade_days=config.max_trade_days,
    )
    daily_results: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for index, trade_date in enumerate(dates, start=1):
        workflow = run_selection_workflow(_workflow_config_for_date(config, trade_date), provider=provider)
        day_trades: list[dict[str, Any]] = []
        for signal in workflow.get("selected", []):
            evaluation = evaluate_signal_hold_return(
                signal,
                provider=provider,
                hold_days=config.hold_days,
                adjust=config.adjust,
            )
            trade = _project_trade(signal, evaluation, trade_date)
            day_trades.append(trade)
            trades.append(trade)
        daily_results.append(
            {
                "trade_date": trade_date,
                "index": index,
                "workflow_counts": workflow.get("counts", {}),
                "strategy_counts": workflow.get("strategy_counts", {}),
                "selected_count": len(workflow.get("selected", [])),
                "rejected_count": len(workflow.get("rejected", [])),
                "summary": summarize_evaluated_trades(day_trades),
            }
        )
        print(f"[{index}/{len(dates)}] {trade_date} selected={len(day_trades)} evaluated={daily_results[-1]['summary']['evaluated']}")

    summary = summarize_evaluated_trades(trades)
    return {
        "backtest": "selection-backtest-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": asdict(config),
        "trade_dates": dates,
        "counts": {
            "trade_days": len(dates),
            "trades": len(trades),
            "evaluated": summary.get("evaluated", 0),
            "data_missing": summary.get("data_missing", 0),
        },
        "summary": summary,
        "daily_results": daily_results,
        "trades": trades,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    available_hint = "attack_third_buy_30m,attack_third_buy_daily"
    parser = argparse.ArgumentParser(description="Run selection workflow replay and hold-return backtest")
    parser.add_argument("--strategy", default="attack_third_buy_30m", help=f"单策略 ID，默认 attack_third_buy_30m；示例: {available_hint}")
    parser.add_argument("--strategies", default="", help="多策略逗号分隔；设置后覆盖 --strategy")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-trade-days", type=int, default=0)
    parser.add_argument("--max-stocks", type=int, default=80)
    parser.add_argument("--min-heat-score", type=float, default=45.0)
    parser.add_argument("--signal-window-days", type=int, default=10)
    parser.add_argument("--pool-mode", choices=["limit_up", "repeated_boards", "kaipanla", "kaipanla_cache", "combined"], default="combined")
    parser.add_argument("--board-lookback-days", type=int, default=10)
    parser.add_argument("--board-top-n", type=int, default=10)
    parser.add_argument("--board-min-appearances", type=int, default=3)
    parser.add_argument("--throttle", type=float, default=0.0)
    parser.add_argument("--sector-lookback-trade-days", type=int, default=10)
    parser.add_argument("--min-sector-score", type=float, default=65.0)
    parser.add_argument("--min-final-score", type=float, default=45.0)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--merge-mode", choices=["best_score", "none"], default="best_score")
    parser.add_argument("--adjust", default="hfq")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> SelectionBacktestConfig:
    return SelectionBacktestConfig(
        strategy_ids=parse_strategy_ids(args.strategies or args.strategy),
        start_date=_normalize_date(args.start_date),
        end_date=_normalize_date(args.end_date),
        hold_days=args.hold_days,
        max_trade_days=args.max_trade_days,
        max_stocks=args.max_stocks,
        min_heat_score=args.min_heat_score,
        signal_window_days=args.signal_window_days,
        pool_mode=args.pool_mode,
        board_lookback_days=args.board_lookback_days,
        board_top_n=args.board_top_n,
        board_min_appearances=args.board_min_appearances,
        throttle=args.throttle,
        sector_lookback_trade_days=args.sector_lookback_trade_days,
        min_sector_score=args.min_sector_score,
        min_final_score=args.min_final_score,
        top_n=args.top_n,
        merge_mode=args.merge_mode,
        adjust=args.adjust,
    )


def run(args: argparse.Namespace) -> Path:
    output = run_selection_backtest(build_config(args))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(output_path)
    print(json.dumps(output["counts"], ensure_ascii=False, indent=2))
    return output_path


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
