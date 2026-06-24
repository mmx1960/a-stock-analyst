from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.strategies.kaipanla_sector_strength_score import enrich_buy_points_with_sector_strength

DEFAULT_INPUT = Path("backtest/results_v6/attack_third_buy_full_universe_2024.checkpoint.json")
DEFAULT_OUTPUT = Path("backtest/results_v6/attack_third_buy_score_optimization.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline optimize attack-third-buy sector scoring from backtest results")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="30分钟三买回测 JSON/checkpoint")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="优化报告输出 JSON")
    parser.add_argument("--lookback-trade-days", type=int, default=10)
    parser.add_argument("--limit-signals", type=int, default=0, help="仅用于 smoke；0 表示全量")
    parser.add_argument("--target-return", type=float, default=10.0, help="胜率阈值收益，默认 10%%")
    parser.add_argument("--min-coverage", type=float, default=0.1, help="推荐阈值最低覆盖率，默认 10%%")
    return parser.parse_args()


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _limit_report_signals(report: dict[str, Any], limit: int) -> dict[str, Any]:
    if limit <= 0:
        return report
    copied = {**report, "detailed": []}
    remaining = limit
    for stock in report.get("detailed", []):
        points = stock.get("buy_points", [])
        if not points:
            continue
        selected = points[:remaining]
        if selected:
            copied["detailed"].append({**stock, "buy_points": selected, "total_buy_points": len(selected)})
            remaining -= len(selected)
        if remaining <= 0:
            break
    return copied


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _flatten_rows(report: dict[str, Any], *, lookback_trade_days: int) -> list[dict[str, Any]]:
    rows = enrich_buy_points_with_sector_strength(report, lookback_trade_days=lookback_trade_days)
    for row in rows:
        row["attack_score"] = _safe_float(row.get("attack_score"), 0.0)
        row["theme_heat_score"] = _safe_float(row.get("theme_heat_score"), 0.0)
        row["sector_score"] = _safe_float(row.get("kaipanla_strength_score"), 0.0)
        row["max_return"] = _safe_float(row.get("max_return"), 0.0)
    return rows


def _has_workflow_score_columns(frame: pd.DataFrame) -> bool:
    return bool((frame["attack_score"].abs().sum() > 0) or (frame["theme_heat_score"].abs().sum() > 0))


def _score_rows(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    sector_score = pd.Series(frame["sector_score"], dtype="float64")
    if not _has_workflow_score_columns(frame):
        return sector_score
    attack_score = pd.Series(frame["attack_score"], dtype="float64")
    theme_heat_score = pd.Series(frame["theme_heat_score"], dtype="float64")
    return (
        attack_score * weights["attack"]
        + sector_score * weights["sector"]
        + theme_heat_score * weights["theme"]
    )


def _evaluate_selection(frame: pd.DataFrame, selected: pd.DataFrame, *, target_return: float) -> dict[str, Any]:
    baseline = frame[frame["status"] == "已实现"].copy() if "status" in frame.columns else frame.copy()
    realized = selected[selected["status"] == "已实现"].copy() if "status" in selected.columns else selected.copy()
    if realized.empty:
        return {
            "signal_count": int(len(selected)),
            "coverage": round(len(selected) / len(frame), 4) if len(frame) else 0.0,
            "realized_count": 0,
        }
    baseline_returns = pd.Series(baseline["max_return"], dtype="float64").dropna()
    returns = pd.Series(realized["max_return"], dtype="float64").dropna()
    avg_return = float(returns.mean())
    baseline_avg = float(baseline_returns.mean()) if not baseline_returns.empty else 0.0
    win_rate = float((returns > target_return).mean())
    baseline_win_rate = float((baseline_returns > target_return).mean()) if not baseline_returns.empty else 0.0
    return {
        "signal_count": int(len(selected)),
        "coverage": round(len(selected) / len(frame), 4) if len(frame) else 0.0,
        "realized_count": int(len(returns)),
        "avg_return": round(avg_return, 2),
        "median_return": round(float(returns.median()), 2),
        "win_rate_target": round(win_rate * 100, 2),
        "loss_rate": round(float((returns < 0).mean() * 100), 2),
        "avg_lift": round(avg_return - baseline_avg, 2),
        "win_lift": round((win_rate - baseline_win_rate) * 100, 2),
    }


def _bucket_summary(frame: pd.DataFrame, *, target_return: float) -> list[dict[str, Any]]:
    bins = [-0.01, 0, 20, 35, 55, 75, 100]
    labels = ["NO_SCORE", "S_1_19", "S_20_34", "S_35_54", "S_55_74", "S_75_100"]
    data = frame.copy()
    data["sector_bucket"] = pd.cut(data["sector_score"], bins=bins, labels=labels, include_lowest=True)
    rows = []
    for bucket, group in data.groupby("sector_bucket", observed=False):
        rows.append({"bucket": str(bucket), **_evaluate_selection(data, group, target_return=target_return)})
    return rows


def _optimize_thresholds(frame: pd.DataFrame, *, target_return: float, min_coverage: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    weight_sets = [
        {"attack": 0.55, "sector": 0.35, "theme": 0.10},
        {"attack": 0.50, "sector": 0.40, "theme": 0.10},
        {"attack": 0.45, "sector": 0.45, "theme": 0.10},
        {"attack": 0.60, "sector": 0.30, "theme": 0.10},
    ]
    for weights, min_sector, min_final in product(weight_sets, [0, 5, 10, 15, 20, 25, 35, 45, 55], [0, 5, 10, 15, 20, 25, 35, 45, 55, 65, 75]):
        data = frame.copy()
        data["final_score"] = _score_rows(data, weights)
        selected = data[(data["sector_score"] >= min_sector) & (data["final_score"] >= min_final)]
        metrics = _evaluate_selection(data, selected, target_return=target_return)
        if metrics.get("coverage", 0.0) < min_coverage or metrics.get("realized_count", 0) == 0:
            continue
        candidates.append({
            "weights": weights,
            "min_sector_score": min_sector,
            "min_final_score": min_final,
            **metrics,
        })
    candidates.sort(key=lambda row: (row.get("avg_lift", -999), row.get("win_lift", -999), row.get("coverage", 0)), reverse=True)
    return candidates[:20]


def run(args: argparse.Namespace) -> Path:
    report = _limit_report_signals(_load_report(Path(args.input)), args.limit_signals)
    rows = _flatten_rows(report, lookback_trade_days=args.lookback_trade_days)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no buy point rows in backtest report")
    baseline = _evaluate_selection(frame, frame, target_return=args.target_return)
    output = {
        "optimizer": "attack-third-buy-sector-score-offline-v1",
        "source": args.input,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lookback_trade_days": args.lookback_trade_days,
        "target_return": args.target_return,
        "signal_count": int(len(frame)),
        "baseline": baseline,
        "sector_bucket_summary": _bucket_summary(frame, target_return=args.target_return),
        "recommended_parameter_sets": _optimize_thresholds(frame, target_return=args.target_return, min_coverage=args.min_coverage),
        "notes": [
            "This optimizer is offline only; realtime workflow must not depend on backtest labels.",
            "Use recommended weights/thresholds to update run_attack_third_buy_workflow defaults after manual review.",
        ],
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)
    print(json.dumps({
        "signal_count": output["signal_count"],
        "baseline": output["baseline"],
        "top_recommendations": output["recommended_parameter_sets"][:5],
    }, ensure_ascii=False, indent=2))
    return output_path


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
