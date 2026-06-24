from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.kaipanla_sector_strength_score import (
    enrich_buy_points_with_sector_strength,
    summarize_strength_buckets,
)

DEFAULT_INPUT = Path("backtest/results_v6/attack_third_buy_full_universe_2024.checkpoint.json")
DEFAULT_OUTPUT_DIR = Path("backtest/results_v6")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze attack-third-buy returns by Kaipanla sector strength")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="回测 JSON 或 checkpoint 路径")
    parser.add_argument("--output", help="输出分析 JSON 路径")
    parser.add_argument("--lookback-trade-days", type=int, default=10, help="买点日前回看交易日数量")
    parser.add_argument("--limit-signals", type=int, default=0, help="仅分析前 N 个买点；0 表示全部")
    return parser.parse_args()


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"input report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _limit_report_signals(report: dict[str, Any], limit: int) -> dict[str, Any]:
    if limit <= 0:
        return report
    copied = {**report, "detailed": []}
    remaining = limit
    for stock in report.get("detailed", []):
        buy_points = stock.get("buy_points", [])
        if not buy_points:
            continue
        selected = buy_points[:remaining]
        if selected:
            copied["detailed"].append({**stock, "buy_points": selected, "total_buy_points": len(selected)})
            remaining -= len(selected)
        if remaining <= 0:
            break
    return copied


def _build_output_path(input_path: Path, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = input_path.name.replace(".checkpoint", "").replace(".json", "")
    return DEFAULT_OUTPUT_DIR / f"{stem}_sector_strength_analysis_{suffix}.json"


def _coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"scored_signals": 0, "scored_ratio_pct": 0.0, "grade_counts": {}, "warnings": ["no_signals_analyzed"]}

    grade_counts = Counter(str(row.get("kaipanla_strength_grade", "UNKNOWN")) for row in rows)
    scored_signals = sum(1 for row in rows if float(row.get("kaipanla_strength_score") or 0) > 0)
    warnings = []
    no_data_count = grade_counts.get("NO_DATA", 0)
    if no_data_count == len(rows):
        warnings.append("all_signals_no_kaipanla_history_or_candidate_sector")
    elif no_data_count / len(rows) >= 0.8:
        warnings.append("most_signals_no_kaipanla_history_or_candidate_sector")

    return {
        "scored_signals": scored_signals,
        "scored_ratio_pct": round(scored_signals / len(rows) * 100, 2),
        "grade_counts": dict(sorted(grade_counts.items())),
        "warnings": warnings,
    }


def analyze(args: argparse.Namespace) -> Path:
    input_path = Path(args.input)
    report = _limit_report_signals(_load_report(input_path), args.limit_signals)
    rows = enrich_buy_points_with_sector_strength(
        report,
        lookback_trade_days=args.lookback_trade_days,
    )
    summary = summarize_strength_buckets(rows)
    coverage = _coverage_summary(rows)
    output = {
        "source": str(input_path),
        "strategy": report.get("strategy"),
        "processed_stocks": report.get("processed_stocks"),
        "source_total_buy_points": report.get("total_buy_points"),
        "analyzed_signals": len(rows),
        **coverage,
        "lookback_trade_days": args.lookback_trade_days,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **summary,
        "signals": rows,
    }
    output_path = _build_output_path(input_path, args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps({
        "processed_stocks": output["processed_stocks"],
        "source_total_buy_points": output["source_total_buy_points"],
        "analyzed_signals": output["analyzed_signals"],
        "scored_signals": output["scored_signals"],
        "scored_ratio_pct": output["scored_ratio_pct"],
        "grade_counts": output["grade_counts"],
        "warnings": output["warnings"],
        "bucket_summary": output["bucket_summary"],
        "recommended_filters": output["recommended_filters"],
    }, ensure_ascii=False, indent=2))
    return output_path


def main() -> None:
    analyze(parse_args())


if __name__ == "__main__":
    main()
