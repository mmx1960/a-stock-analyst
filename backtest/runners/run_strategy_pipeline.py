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

from backtest.runners import optimize_selection_thresholds
from backtest.runners.run_selection_backtest import (
    SelectionBacktestConfig,
    _json_default,
    _normalize_date,
    run_selection_backtest,
)
from backtest.strategies.registry import parse_strategy_ids
from backtest.workflows.selection_workflow import SelectionWorkflowConfig, run_selection_workflow

DEFAULT_OUTPUT_DIR = Path("backtest/results_v6/pipeline")
DEFAULT_DB_PATH = "data/ashare.duckdb"


@dataclass(frozen=True)
class StrategyPipelineConfig:
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
    sector_scores: str = "45,55,65,75"
    final_scores: str = "40,45,50,55"
    min_samples: int = 5
    db_path: str = DEFAULT_DB_PATH


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return path


def _table_coverage(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    try:
        frame = con.execute(sql).df()
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if frame.empty:
        return {"available": True}
    row = frame.iloc[0].to_dict()
    return {key: _json_default(value) if pd.notna(value) else None for key, value in row.items()}


def collect_data_coverage(*, db_path: str = DEFAULT_DB_PATH) -> dict[str, Any]:
    with duckdb.connect(db_path, read_only=True) as con:
        return {
            "db_path": db_path,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stock_basic": _table_coverage(
                con,
                "SELECT count(*) AS row_count, count(distinct code) AS code_count FROM stock_basic",
            ),
            "daily_hfq": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct code) AS code_count,
                       min(trade_date) AS min_date, max(trade_date) AS max_date
                FROM daily_kline
                WHERE adjust = 'hfq'
                """,
            ),
            "minute_30": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct code) AS code_count,
                       min(trade_dt) AS min_dt, max(trade_dt) AS max_dt
                FROM minute_kline
                WHERE period = '30'
                """,
            ),
            "minute_60": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct code) AS code_count,
                       min(trade_dt) AS min_dt, max(trade_dt) AS max_dt
                FROM minute_kline
                WHERE period = '60'
                """,
            ),
            "sector_membership_current": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct code) AS code_count,
                       count(distinct sector_name) AS sector_count
                FROM stock_sector_membership
                WHERE is_current = true
                """,
            ),
            "sector_strength": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct trade_date) AS trade_day_count,
                       count(distinct sector_name) AS sector_count,
                       min(trade_date) AS min_date, max(trade_date) AS max_date
                FROM kaipanla_sector_strength
                """,
            ),
            "realtime_quote_snapshot": _table_coverage(
                con,
                """
                SELECT count(*) AS row_count, count(distinct code) AS code_count,
                       min(trade_dt) AS min_dt, max(trade_dt) AS max_dt
                FROM realtime_quote_snapshot
                """,
            ),
        }


def _build_workflow_config(config: StrategyPipelineConfig) -> SelectionWorkflowConfig:
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
        sector_score_date=config.end_date,
        as_of_date=config.end_date,
        sector_lookback_trade_days=config.sector_lookback_trade_days,
        min_sector_score=config.min_sector_score,
        min_final_score=config.min_final_score,
        top_n=config.top_n,
        merge_mode=config.merge_mode,
    )


def _build_backtest_config(config: StrategyPipelineConfig) -> SelectionBacktestConfig:
    return SelectionBacktestConfig(
        strategy_ids=config.strategy_ids,
        start_date=config.start_date,
        end_date=config.end_date,
        hold_days=config.hold_days,
        max_trade_days=config.max_trade_days,
        max_stocks=config.max_stocks,
        min_heat_score=config.min_heat_score,
        signal_window_days=config.signal_window_days,
        pool_mode=config.pool_mode,
        board_lookback_days=config.board_lookback_days,
        board_top_n=config.board_top_n,
        board_min_appearances=config.board_min_appearances,
        throttle=config.throttle,
        sector_lookback_trade_days=config.sector_lookback_trade_days,
        min_sector_score=config.min_sector_score,
        min_final_score=config.min_final_score,
        top_n=config.top_n,
        merge_mode=config.merge_mode,
        adjust=config.adjust,
    )


def _build_optimizer_args(config: StrategyPipelineConfig, *, input_path: Path, output_path: Path) -> argparse.Namespace:
    return optimize_selection_thresholds.parse_args(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sector-scores",
            config.sector_scores,
            "--final-scores",
            config.final_scores,
            "--min-samples",
            str(config.min_samples),
        ]
    )


def run_strategy_pipeline(config: StrategyPipelineConfig, *, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    strategy_slug = "_".join(config.strategy_ids)
    run_dir = output_dir / f"{timestamp}_{strategy_slug}"
    workflow_path = run_dir / "selection_workflow.json"
    backtest_path = run_dir / "selection_backtest.json"
    optimization_path = run_dir / "threshold_optimization.json"
    coverage_path = run_dir / "data_coverage.json"
    summary_path = run_dir / "pipeline_summary.json"

    coverage = collect_data_coverage(db_path=config.db_path)
    workflow = run_selection_workflow(_build_workflow_config(config))
    backtest = run_selection_backtest(_build_backtest_config(config))
    _write_json(workflow_path, workflow)
    _write_json(backtest_path, backtest)
    _write_json(coverage_path, coverage)
    optimizer_args = _build_optimizer_args(config, input_path=backtest_path, output_path=optimization_path)
    optimization = optimize_selection_thresholds.optimize_from_file(optimizer_args)
    _write_json(optimization_path, optimization)

    summary = {
        "pipeline": "strategy-pipeline-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "config": asdict(config),
        "outputs": {
            "workflow": str(workflow_path),
            "backtest": str(backtest_path),
            "optimization": str(optimization_path),
            "coverage": str(coverage_path),
            "summary": str(summary_path),
        },
        "workflow_counts": workflow.get("counts", {}),
        "backtest_counts": backtest.get("counts", {}),
        "backtest_summary": backtest.get("summary", {}),
        "optimization_recommendations": optimization.get("recommendations", {}),
        "coverage_snapshot": coverage,
    }
    _write_json(summary_path, summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-click strategy workflow -> backtest -> threshold optimization pipeline")
    parser.add_argument("--strategy", default="attack_third_buy_30m")
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
    parser.add_argument("--sector-scores", default="45,55,65,75")
    parser.add_argument("--final-scores", default="40,45,50,55")
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> StrategyPipelineConfig:
    return StrategyPipelineConfig(
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
        sector_scores=args.sector_scores,
        final_scores=args.final_scores,
        min_samples=args.min_samples,
        db_path=args.db_path,
    )


def run(args: argparse.Namespace) -> Path:
    summary = run_strategy_pipeline(build_config(args), output_dir=Path(args.output_dir))
    summary_path = Path(summary["outputs"]["summary"])
    print(summary_path)
    print(
        json.dumps(
            {
                "workflow_counts": summary["workflow_counts"],
                "backtest_counts": summary["backtest_counts"],
                "recommendations": summary["optimization_recommendations"],
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )
    return summary_path


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
