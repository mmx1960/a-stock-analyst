from __future__ import annotations

from backtest.runners import run_attack_third_buy_full_universe as runner


def test_build_report_includes_sector_strength_summary() -> None:
    results = [
        {
            "code": "000001",
            "name": "平安银行",
            "total_buy_points": 2,
            "buy_points": [
                {"buy_year": 2024, "max_return": 18.0, "status": "已实现", "kaipanla_strength_score": 80},
                {"buy_year": 2024, "max_return": -3.0, "status": "已实现", "kaipanla_strength_score": 0},
            ],
        }
    ]

    report = runner.build_report(
        stocks=[("000001", "平安银行")],
        processed=1,
        results=results,
        errors=[],
        start_year=2024,
        hold_weeks=10,
        structure_period="daily",
        dedupe_days=20,
        sector_strength_lookback=10,
        elapsed_seconds=1.2,
    )

    summary = report["sector_strength_summary"]
    assert summary["enabled"] is True
    assert summary["scored_signals"] == 1
    assert summary["scored_ratio_pct"] == 50.0
    assert summary["bucket_summary"]
    assert summary["recommended_filters"][-1]["min_score"] == 75
