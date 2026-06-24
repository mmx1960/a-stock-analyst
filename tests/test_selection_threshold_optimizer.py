from __future__ import annotations

import json

from backtest.optimization.threshold_optimizer import ThresholdCandidate, evaluate_threshold_candidates
from backtest.runners import optimize_selection_thresholds as runner


def _trade(strategy: str, sector: float, final: float, max_return: float, hit: bool = True) -> dict:
    return {
        "strategy_id": strategy,
        "workflow_final_score": final,
        "score_snapshot": {"kaipanla_strength_score": sector},
        "evaluation_status": "evaluated",
        "win": max_return > 0,
        "hit_target": hit,
        "max_return_pct": max_return,
        "close_return_pct": max_return / 2,
        "min_return_pct": -2.0,
    }


def test_evaluate_threshold_candidates_groups_by_strategy() -> None:
    trades = [
        _trade("s1", 70, 60, 12, True),
        _trade("s1", 50, 42, 3, False),
        _trade("s2", 80, 70, 20, True),
    ]
    result = evaluate_threshold_candidates(
        trades,
        candidates=[ThresholdCandidate(45, 40), ThresholdCandidate(65, 55)],
        min_samples=1,
    )

    assert set(result["strategy_results"]) == {"s1", "s2"}
    assert result["recommendations"]["s1"]["min_sector_score"] == 65
    assert result["recommendations"]["s1"]["evaluated"] == 1
    assert result["recommendations"]["s2"]["hit_target_rate"] == 100.0


def test_evaluate_threshold_candidates_respects_min_samples() -> None:
    result = evaluate_threshold_candidates(
        [_trade("s1", 70, 60, 12, True)],
        candidates=[ThresholdCandidate(65, 55)],
        min_samples=2,
    )

    assert result["strategy_results"]["s1"][0]["sample_ok"] is False
    assert result["recommendations"]["s1"] is None


def test_optimize_runner_loads_trades_and_writes_recommendation(tmp_path) -> None:
    input_path = tmp_path / "backtest.json"
    output_path = tmp_path / "optimized.json"
    input_path.write_text(json.dumps({"trades": [_trade("s1", 70, 60, 12, True)]}), encoding="utf-8")

    args = runner.parse_args(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--sector-scores",
            "65",
            "--final-scores",
            "55",
            "--min-samples",
            "1",
        ]
    )
    runner.run(args)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["trade_count"] == 1
    assert payload["recommendations"]["s1"]["min_final_score"] == 55.0
