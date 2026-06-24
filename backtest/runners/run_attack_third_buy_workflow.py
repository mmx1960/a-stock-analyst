from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.workflows.selection_workflow import SelectionWorkflowConfig, run_selection_workflow

DEFAULT_OUTPUT = Path("backtest/results_v6/current_attack_third_buy_workflow.json")

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attack-third-buy workflow compatibility runner")
    parser.add_argument("--max-stocks", type=int, default=80, help="最多扫描热点股票数，默认 80")
    parser.add_argument("--min-heat-score", type=float, default=45.0, help="热点股票池最低主题热度分，默认 45")
    parser.add_argument("--signal-window-days", type=int, default=10, help="三买信号最大距今天数，默认 10")
    parser.add_argument("--pool-mode", choices=["limit_up", "repeated_boards", "kaipanla", "kaipanla_cache", "combined"], default="combined")
    parser.add_argument("--board-lookback-days", type=int, default=10)
    parser.add_argument("--board-top-n", type=int, default=10)
    parser.add_argument("--board-min-appearances", type=int, default=3)
    parser.add_argument("--structure-period", choices=["daily", "5", "15", "30", "60"], default="30")
    parser.add_argument("--throttle", type=float, default=0.3)
    parser.add_argument("--sector-score-date", default="", help="开盘啦板块热点评分日期；默认使用当天")
    parser.add_argument("--as-of-date", default="", help="策略扫描截止日期；默认跟随 --sector-score-date 或当天")
    parser.add_argument("--sector-lookback-trade-days", type=int, default=10, help="板块热点评分回看交易日，默认 10")
    parser.add_argument("--min-sector-score", type=float, default=65.0, help="二次筛选最低开盘啦板块分，默认 65")
    parser.add_argument("--min-final-score", type=float, default=45.0, help="二次筛选最低综合分，默认 45")
    parser.add_argument("--top-n", type=int, default=30, help="最终最多输出 N 只，默认 30；0 表示不限")
    parser.add_argument("--require-sector-strength-top-n", type=int, default=10, help="要求买点当天所属板块进入开盘啦强度排名前 N；0 表示关闭")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 文件")
    return parser.parse_args()


def _strategy_id_from_period(structure_period: str) -> str:
    return "attack_third_buy_daily" if structure_period == "daily" else "attack_third_buy_30m"


def build_config(args: argparse.Namespace) -> SelectionWorkflowConfig:
    return SelectionWorkflowConfig(
        strategy_ids=[_strategy_id_from_period(args.structure_period)],
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
    )


def run_workflow(args: argparse.Namespace) -> Path:
    output = run_selection_workflow(build_config(args))
    output["workflow"] = "attack-third-buy-sector-heat-v1"
    output["structure_period"] = args.structure_period
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps(output["counts"], ensure_ascii=False, indent=2))
    for item in output["selected"][:20]:
        print(
            f"{item.get('code')} {item.get('name')} final={item.get('workflow_final_score')} "
            f"sector={item.get('kaipanla_strength_score')} attack={item.get('attack_score')} "
            f"theme={item.get('theme')} candidates={item.get('kaipanla_candidate_sectors', [])[:5]}"
        )
    return output_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_workflow(parse_args())


if __name__ == "__main__":
    main()
