from __future__ import annotations

import argparse
from pathlib import Path

from backtest.runners import optimize_attack_third_buy_sector_score as optimizer
from backtest.runners import run_attack_third_buy_workflow as attack_workflow_runner
from backtest.strategies import registry
from backtest.workflows import selection_workflow


def test_selection_workflow_filters_by_sector_and_final_score(monkeypatch) -> None:
    fake_strategy = registry.SelectionStrategy(
        strategy_id="fake_strategy",
        strategy_name="Fake Strategy",
        description="test strategy",
        runner=lambda **_: [
            {"code": "000001", "name": "强票", "signal_score": 70, "attack_score": 70, "theme_heat_score": 80, "theme": "机器人"},
            {"code": "000002", "name": "弱票", "signal_score": 45, "attack_score": 45, "theme_heat_score": 30, "theme": "地产"},
        ],
    )
    monkeypatch.setattr(selection_workflow, "get_strategy", lambda _: fake_strategy)

    def fake_score(*, code: str, **_) -> dict:
        return {
            "kaipanla_strength_score": 70 if code == "000001" else 10,
            "kaipanla_strength_grade": "B" if code == "000001" else "D",
            "kaipanla_candidate_sectors": ["机器人"] if code == "000001" else ["地产"],
        }

    monkeypatch.setattr(selection_workflow, "score_sector_strength_safe", fake_score)
    monkeypatch.setattr(
        selection_workflow,
        "check_sector_strength_top_n",
        lambda *, candidate_sectors, trade_date, top_n=10, **_: (
            "机器人" in candidate_sectors,
            {"sector_strength_top_n_ok": "机器人" in candidate_sectors},
        ),
    )
    payload = selection_workflow.run_selection_workflow(
        selection_workflow.SelectionWorkflowConfig(
            strategy_ids=["fake_strategy"],
            sector_score_date="2026-06-19",
            min_sector_score=65,
            min_final_score=45,
            top_n=10,
        )
    )

    assert payload["counts"] == {
        "raw_signals": 2,
        "triggered_signals": 2,
        "watch_signals": 0,
        "selected": 1,
        "rejected": 1,
    }
    assert [item["code"] for item in payload["selected"]] == ["000001"]
    assert payload["selected"][0]["strategy_id"] == "fake_strategy"
    assert payload["rejected"][0]["code"] == "000002"
    assert "sector_score_below_threshold" in payload["rejected"][0]["workflow_reject_reasons"]
    assert "sector_strength_not_top10_on_buy_date" in payload["rejected"][0]["workflow_reject_reasons"]


def test_selection_workflow_enforces_sector_strength_top10(monkeypatch) -> None:
    fake_strategy = registry.SelectionStrategy(
        strategy_id="fake_strategy",
        strategy_name="Fake Strategy",
        description="test strategy",
        runner=lambda **_: [
            {"code": "000001", "name": "强票", "signal_score": 80, "theme_heat_score": 80, "theme": "机器人"},
            {"code": "000002", "name": "强但非Top10", "signal_score": 80, "theme_heat_score": 80, "theme": "地产"},
        ],
    )
    monkeypatch.setattr(selection_workflow, "get_strategy", lambda _: fake_strategy)
    monkeypatch.setattr(
        selection_workflow,
        "score_sector_strength_safe",
        lambda *, code, **_: {
            "kaipanla_strength_score": 80,
            "kaipanla_strength_grade": "A",
            "kaipanla_candidate_sectors": ["机器人"] if code == "000001" else ["地产"],
        },
    )
    monkeypatch.setattr(
        selection_workflow,
        "check_sector_strength_top_n",
        lambda *, candidate_sectors, trade_date, top_n=10, **_: (
            "机器人" in candidate_sectors,
            {"sector_strength_top_n_ok": "机器人" in candidate_sectors},
        ),
    )

    payload = selection_workflow.run_selection_workflow(
        selection_workflow.SelectionWorkflowConfig(
            strategy_ids=["fake_strategy"],
            sector_score_date="2026-06-19",
            min_sector_score=0,
            min_final_score=0,
            top_n=10,
        )
    )

    assert [item["code"] for item in payload["selected"]] == ["000001"]
    assert payload["rejected"][0]["code"] == "000002"
    assert "sector_strength_not_top10_on_buy_date" in payload["rejected"][0]["workflow_reject_reasons"]


def test_selection_workflow_treats_sub_sector_as_top10_parent(monkeypatch) -> None:
    rows = [
        {"sector_name": "芯片", "rn": 1, "strength_score": 10000, "capital_score": 10, "turnover": 100},
        {"sector_name": "机器人概念", "rn": 2, "strength_score": 9000, "capital_score": 9, "turnover": 90},
        {"sector_name": "算力", "rn": 3, "strength_score": 8000, "capital_score": 8, "turnover": 80},
        {"sector_name": "AI应用", "rn": 4, "strength_score": 7000, "capital_score": 7, "turnover": 70},
    ]
    monkeypatch.setitem(selection_workflow._SECTOR_STRENGTH_TOP_N_CACHE, ("2026-06-18", 10), rows)

    for child in ["存储芯片", "人形机器人", "算力租赁", "AI智能体"]:
        ok, meta = selection_workflow.check_sector_strength_top_n(
            candidate_sectors=[child],
            trade_date="2026-06-18",
            top_n=10,
        )
        assert ok, child
        assert meta["sector_strength_top_n_best_sector"] in {"芯片", "机器人概念", "算力", "AI应用"}


def test_selection_workflow_scores_each_signal_buy_date(monkeypatch) -> None:
    fake_strategy = registry.SelectionStrategy(
        strategy_id="fake_strategy",
        strategy_name="Fake Strategy",
        description="test strategy",
        runner=lambda **_: [
            {"code": "000001", "name": "强票", "buy_date": "2026-06-17", "signal_score": 80, "theme_heat_score": 80, "theme": "机器人"},
        ],
    )
    seen_buy_dates: list[str] = []
    monkeypatch.setattr(selection_workflow, "get_strategy", lambda _: fake_strategy)

    def fake_score(*, buy_date: str, **_) -> dict:
        seen_buy_dates.append(buy_date)
        return {
            "kaipanla_strength_score": 80,
            "kaipanla_strength_grade": "A",
            "kaipanla_candidate_sectors": ["机器人"],
        }

    monkeypatch.setattr(selection_workflow, "score_sector_strength_safe", fake_score)
    monkeypatch.setattr(
        selection_workflow,
        "check_sector_strength_top_n",
        lambda *, candidate_sectors, trade_date, top_n=10, **_: (
            trade_date == "2026-06-17",
            {"sector_strength_top_n_ok": trade_date == "2026-06-17"},
        ),
    )

    payload = selection_workflow.run_selection_workflow(
        selection_workflow.SelectionWorkflowConfig(
            strategy_ids=["fake_strategy"],
            sector_score_date="2026-06-19",
            min_sector_score=0,
            min_final_score=0,
            top_n=10,
        )
    )

    assert seen_buy_dates == ["2026-06-17"]
    assert [item["code"] for item in payload["selected"]] == ["000001"]


def test_selection_workflow_uses_d_shen_fund_consistency_score() -> None:
    final_score, breakdown = selection_workflow.workflow_final_score(
        {
            "signal_score": 80,
            "kaipanla_strength_score": 60,
            "theme_heat_score": 50,
            "kaipanla_market_heat_score": 40,
        }
    )

    assert final_score == 67.7
    assert breakdown["scoring_profile"] == "d_shen_fund_consistency_v2"
    assert breakdown["sector_score_component"] == 27.0


def test_attack_runner_defaults_to_strict_sector_threshold(monkeypatch) -> None:
    monkeypatch.setattr(attack_workflow_runner.sys, "argv", ["run_attack_third_buy_workflow.py"])

    args = attack_workflow_runner.parse_args()
    config = attack_workflow_runner.build_config(args)

    assert args.min_sector_score == 65.0
    assert config.min_sector_score == 65.0
    assert config.min_final_score == 45.0
    assert args.require_sector_strength_top_n == 10
    assert config.require_sector_strength_top_n == 10
    assert config.strategy_ids == ["attack_third_buy_30m"]


def test_strategy_registry_lists_attack_third_buy_variants() -> None:
    strategy_ids = {strategy.strategy_id for strategy in registry.list_strategies()}

    assert "attack_third_buy_30m" in strategy_ids
    assert "attack_third_buy_daily" in strategy_ids
    assert registry.parse_strategy_ids("attack_third_buy_30m,attack_third_buy_daily") == [
        "attack_third_buy_30m",
        "attack_third_buy_daily",
    ]


def test_optimizer_recommends_parameter_sets(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "bt.json"
    output_path = tmp_path / "opt.json"
    input_path.write_text('{"detailed": []}', encoding="utf-8")
    monkeypatch.setattr(
        optimizer,
        "enrich_buy_points_with_sector_strength",
        lambda report, lookback_trade_days=10: [
            {"code": "000001", "status": "已实现", "attack_score": 70, "theme_heat_score": 80, "kaipanla_strength_score": 70, "max_return": 30},
            {"code": "000002", "status": "已实现", "attack_score": 45, "theme_heat_score": 30, "kaipanla_strength_score": 10, "max_return": -8},
            {"code": "000003", "status": "已实现", "attack_score": 65, "theme_heat_score": 60, "kaipanla_strength_score": 55, "max_return": 18},
        ],
    )
    args = argparse.Namespace(
        input=str(input_path),
        output=str(output_path),
        lookback_trade_days=10,
        limit_signals=0,
        target_return=10.0,
        min_coverage=0.1,
    )

    optimizer.run(args)

    payload = optimizer.json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["optimizer"] == "attack-third-buy-sector-score-offline-v1"
    assert payload["recommended_parameter_sets"]
    assert payload["notes"][0].startswith("This optimizer is offline only")
