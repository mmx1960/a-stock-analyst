from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ThresholdCandidate:
    min_sector_score: float
    min_final_score: float


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sector_score(trade: dict[str, Any]) -> float:
    snapshot = trade.get("score_snapshot") or {}
    return _safe_float(snapshot.get("kaipanla_strength_score"))


def _final_score(trade: dict[str, Any]) -> float:
    return _safe_float(trade.get("workflow_final_score"))


def _strategy_id(trade: dict[str, Any]) -> str:
    return str(trade.get("strategy_id") or "unknown")


def _summarize_subset(trades: list[dict[str, Any]], total_count: int) -> dict[str, Any]:
    evaluated = [trade for trade in trades if trade.get("evaluation_status") == "evaluated"]
    if not evaluated:
        return {
            "signals": len(trades),
            "evaluated": 0,
            "coverage_pct": round(len(trades) / total_count * 100.0, 2) if total_count else 0.0,
            "win_rate": 0.0,
            "hit_target_rate": 0.0,
            "avg_max_return_pct": 0.0,
            "median_max_return_pct": 0.0,
            "avg_close_return_pct": 0.0,
            "avg_min_return_pct": 0.0,
        }
    max_returns = [_safe_float(trade.get("max_return_pct")) for trade in evaluated]
    close_returns = [_safe_float(trade.get("close_return_pct")) for trade in evaluated]
    min_returns = [_safe_float(trade.get("min_return_pct")) for trade in evaluated]
    wins = [1.0 if trade.get("win") else 0.0 for trade in evaluated]
    targets = [1.0 if trade.get("hit_target") else 0.0 for trade in evaluated]
    return {
        "signals": len(trades),
        "evaluated": len(evaluated),
        "coverage_pct": round(len(trades) / total_count * 100.0, 2) if total_count else 0.0,
        "win_rate": round(sum(wins) / len(wins) * 100.0, 2),
        "hit_target_rate": round(sum(targets) / len(targets) * 100.0, 2),
        "avg_max_return_pct": round(sum(max_returns) / len(max_returns), 4),
        "median_max_return_pct": round(float(pd.Series(max_returns).median()), 4),
        "avg_close_return_pct": round(sum(close_returns) / len(close_returns), 4),
        "avg_min_return_pct": round(sum(min_returns) / len(min_returns), 4),
    }


def evaluate_threshold_candidates(
    trades: list[dict[str, Any]],
    *,
    candidates: list[ThresholdCandidate],
    min_samples: int = 1,
) -> dict[str, Any]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_strategy.setdefault(_strategy_id(trade), []).append(trade)

    strategy_results: dict[str, list[dict[str, Any]]] = {}
    recommendations: dict[str, dict[str, Any] | None] = {}
    for strategy_id, strategy_trades in sorted(by_strategy.items()):
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            selected = [
                trade
                for trade in strategy_trades
                if _sector_score(trade) >= candidate.min_sector_score and _final_score(trade) >= candidate.min_final_score
            ]
            summary = _summarize_subset(selected, len(strategy_trades))
            row = {
                "strategy_id": strategy_id,
                "min_sector_score": candidate.min_sector_score,
                "min_final_score": candidate.min_final_score,
                "sample_ok": summary["evaluated"] >= min_samples,
                **summary,
            }
            rows.append(row)
        rows.sort(key=lambda item: (not item["sample_ok"], -item["hit_target_rate"], -item["avg_max_return_pct"], -item["evaluated"]))
        strategy_results[strategy_id] = rows
        valid = [row for row in rows if row["sample_ok"]]
        recommendations[strategy_id] = valid[0] if valid else None

    return {
        "optimizer": "threshold-candidate-optimizer-v1",
        "min_samples": min_samples,
        "candidate_count": len(candidates),
        "strategy_results": strategy_results,
        "recommendations": recommendations,
    }


def default_threshold_candidates() -> list[ThresholdCandidate]:
    return [
        ThresholdCandidate(min_sector_score=sector, min_final_score=final)
        for sector in (45.0, 55.0, 65.0, 75.0)
        for final in (40.0, 45.0, 50.0, 55.0)
    ]
