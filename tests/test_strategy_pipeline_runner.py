from __future__ import annotations

import json

from backtest.runners import run_strategy_pipeline as pipeline


def test_build_config_normalizes_strategy_dates_and_thresholds() -> None:
    args = pipeline.parse_args(
        [
            "--strategies",
            "daily_uptrend_30m_ma13_pullback,attack_third_buy_daily",
            "--start-date",
            "20260522",
            "--end-date",
            "20260618",
            "--sector-scores",
            "0,20",
            "--final-scores",
            "0,60",
            "--min-samples",
            "1",
        ]
    )

    config = pipeline.build_config(args)

    assert config.strategy_ids == ["daily_uptrend_30m_ma13_pullback", "attack_third_buy_daily"]
    assert config.start_date == "2026-05-22"
    assert config.end_date == "2026-06-18"
    assert config.sector_scores == "0,20"
    assert config.final_scores == "0,60"
    assert config.min_samples == 1


def test_run_strategy_pipeline_writes_all_stage_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pipeline,
        "collect_data_coverage",
        lambda db_path="data/ashare.duckdb": {"db_path": db_path, "stock_basic": {"row_count": 1}},
    )
    monkeypatch.setattr(
        pipeline,
        "run_selection_workflow",
        lambda config: {
            "counts": {"raw_signals": 1, "selected": 1, "rejected": 0},
            "selected": [{"code": "000001"}],
            "rejected": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "run_selection_backtest",
        lambda config: {
            "counts": {"trade_days": 1, "trades": 1, "evaluated": 1, "data_missing": 0},
            "summary": {"hit_target_rate": 100.0},
            "trades": [
                {
                    "strategy_id": "daily_uptrend_30m_ma13_pullback",
                    "workflow_final_score": 70,
                    "score_snapshot": {"kaipanla_strength_score": 50},
                    "evaluation_status": "evaluated",
                    "max_return_pct": 12,
                    "hit_target": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        pipeline.optimize_selection_thresholds,
        "optimize_from_file",
        lambda args: {
            "trade_count": 1,
            "recommendations": {"daily_uptrend_30m_ma13_pullback": {"min_sector_score": 0.0}},
        },
    )

    config = pipeline.StrategyPipelineConfig(
        strategy_ids=["daily_uptrend_30m_ma13_pullback"],
        start_date="2026-05-22",
        end_date="2026-05-22",
        min_samples=1,
        db_path="test.duckdb",
    )

    summary = pipeline.run_strategy_pipeline(config, output_dir=tmp_path)

    outputs = summary["outputs"]
    assert summary["workflow_counts"]["selected"] == 1
    assert summary["backtest_counts"]["evaluated"] == 1
    assert summary["optimization_recommendations"]["daily_uptrend_30m_ma13_pullback"]["min_sector_score"] == 0.0
    for path in outputs.values():
        payload = json.loads(open(path, encoding="utf-8").read())
        assert isinstance(payload, dict)
