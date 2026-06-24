from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.registry import list_strategies, parse_strategy_ids
from backtest.workflows.selection_workflow import SelectionWorkflowConfig, run_selection_workflow

DEFAULT_OUTPUT = Path("backtest/results_v6/current_stock_selection_workflow.json")


def parse_args() -> argparse.Namespace:
    available = ",".join(strategy.strategy_id for strategy in list_strategies())
    parser = argparse.ArgumentParser(description="Multi-strategy stock selection workflow")
    parser.add_argument("--strategy", default="attack_third_buy_30m", help=f"单策略 ID，默认 attack_third_buy_30m；可选: {available}")
    parser.add_argument("--strategies", default="", help=f"多策略逗号分隔；设置后覆盖 --strategy；可选: {available}")
    parser.add_argument("--max-stocks", type=int, default=80)
    parser.add_argument("--min-heat-score", type=float, default=45.0)
    parser.add_argument("--signal-window-days", type=int, default=10)
    parser.add_argument("--pool-mode", choices=["limit_up", "repeated_boards", "kaipanla", "kaipanla_cache", "combined"], default="combined")
    parser.add_argument("--board-lookback-days", type=int, default=10)
    parser.add_argument("--board-top-n", type=int, default=10)
    parser.add_argument("--board-min-appearances", type=int, default=3)
    parser.add_argument("--throttle", type=float, default=0.3)
    parser.add_argument("--sector-score-date", default="")
    parser.add_argument("--as-of-date", default="", help="策略扫描截止日期；默认跟随 --sector-score-date 或当天")
    parser.add_argument("--sector-lookback-trade-days", type=int, default=10)
    parser.add_argument("--min-sector-score", type=float, default=65.0)
    parser.add_argument("--min-final-score", type=float, default=45.0)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--require-sector-strength-top-n", type=int, default=10, help="要求买点当天所属板块进入开盘啦强度排名前 N；0 表示关闭")
    parser.add_argument("--merge-mode", choices=["best_score", "none"], default="best_score")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--list-strategies", action="store_true")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> SelectionWorkflowConfig:
    strategy_ids = parse_strategy_ids(args.strategies or args.strategy)
    return SelectionWorkflowConfig(
        strategy_ids=strategy_ids,
        max_stocks=args.max_stocks,
        min_heat_score=args.min_heat_score,
        signal_window_days=args.signal_window_days,
        pool_mode=args.pool_mode,
        board_lookback_days=args.board_lookback_days,
        board_top_n=args.board_top_n,
        board_min_appearances=args.board_min_appearances,
        throttle=args.throttle,
        sector_score_date=args.sector_score_date,
        as_of_date=args.as_of_date,
        sector_lookback_trade_days=args.sector_lookback_trade_days,
        min_sector_score=args.min_sector_score,
        min_final_score=args.min_final_score,
        top_n=args.top_n,
        require_sector_strength_top_n=args.require_sector_strength_top_n,
        merge_mode=args.merge_mode,
    )


def run(args: argparse.Namespace) -> Path | None:
    if args.list_strategies:
        for strategy in list_strategies():
            print(f"{strategy.strategy_id}\t{strategy.strategy_name}\t{strategy.description}")
        return None
    output = run_selection_workflow(build_config(args))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps(output["counts"], ensure_ascii=False, indent=2))
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run(parse_args())


if __name__ == "__main__":
    main()
