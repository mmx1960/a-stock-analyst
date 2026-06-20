"""
运行进攻型三买选股：热点板块 + 主升趋势 + 三买结构。
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from backtest.strategies.strategy_attack_third_buy import scan_attack_third_buy, save_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run attack-third-buy realtime selector")
    parser.add_argument("--max-stocks", type=int, default=80, help="最多扫描热点股票数，默认 80")
    parser.add_argument("--min-heat-score", type=float, default=45.0, help="热点板块最低热度分，默认 45")
    parser.add_argument("--signal-window-days", type=int, default=10, help="三买信号最大距今天数，默认 10")
    parser.add_argument("--pool-mode", choices=["limit_up", "repeated_boards", "combined"], default="combined", help="股票池模式，默认 combined")
    parser.add_argument("--board-lookback-days", type=int, default=10, help="板块重复统计窗口，默认 10")
    parser.add_argument("--board-top-n", type=int, default=10, help="每日前 N 热点板块，默认 10")
    parser.add_argument("--board-min-appearances", type=int, default=3, help="进入前 N 的最少次数，默认 3")
    parser.add_argument("--structure-period", choices=["daily", "5", "15", "30", "60"], default="30", help="三买结构检测周期，默认 30 分钟")
    parser.add_argument("--throttle", type=float, default=0.3, help="单股扫描间隔秒数，默认 0.3")
    parser.add_argument("--output", default="current_attack_third_buy.json", help="输出 JSON 文件")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    results = scan_attack_third_buy(
        max_stocks=args.max_stocks,
        min_heat_score=args.min_heat_score,
        signal_window_days=args.signal_window_days,
        pool_mode=args.pool_mode,
        board_lookback_days=args.board_lookback_days,
        board_top_n=args.board_top_n,
        board_min_appearances=args.board_min_appearances,
        structure_period=args.structure_period,
        throttle_seconds=args.throttle,
    )
    output = save_results(results, Path(args.output))
    print(output)
    print(f"signals={len(results)}")
    for item in results[:20]:
        print(
            f"{item['code']} {item.get('name')} score={item['attack_score']} "
            f"theme={item.get('theme')} freq={item.get('structure_freq')} buy={item.get('buy_date')} reason={item.get('third_buy_reason')}"
        )


if __name__ == "__main__":
    main()
