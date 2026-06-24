from __future__ import annotations

import pandas as pd

from app.core.data.market_data_provider import MarketDataProvider
from backtest.evaluation.hold_return import evaluate_signal_hold_return, summarize_evaluated_trades
from backtest.runners import run_selection_backtest as runner
from backtest.strategies import registry
from backtest.workflows import selection_workflow


class InMemoryProvider(MarketDataProvider):
    def get_stock_list(self, limit=None):
        return pd.DataFrame([{"code": "000001", "name": "平安银行"}])

    def get_daily_bars(self, code, start_date=None, end_date=None, adjust="hfq"):
        return pd.DataFrame(
            [
                {"trade_date": "2026-06-18", "open": 10, "high": 10.2, "low": 9.8, "close": 10.0},
                {"trade_date": "2026-06-19", "open": 10.1, "high": 11.0, "low": 10.0, "close": 10.8},
                {"trade_date": "2026-06-22", "open": 10.8, "high": 11.5, "low": 10.5, "close": 11.2},
            ]
        )

    def get_minute_bars(self, code, period="30", start_date=None, end_date=None):
        return pd.DataFrame()

    def get_realtime_quote(self, code):
        return {"code": code, "name": code, "price": 10.0}

    def get_sector_membership(self, code, current_only=True):
        return pd.DataFrame()

    def get_sector_strength(self, start_date=None, end_date=None):
        return pd.DataFrame()


def _registered_test_strategy(**_: object) -> list[dict]:
    return [
        {
            "strategy_id": "unit_test_real_workflow_strategy",
            "strategy_name": "Unit Test Strategy",
            "code": "000001",
            "name": "平安银行",
            "buy_date": "2026-06-18",
            "signal_price": 10.0,
            "signal_score": 80.0,
            "theme_heat_score": 50.0,
            "kaipanla_strength_score": 100.0,
        }
    ]


def test_evaluate_signal_hold_return_uses_future_daily_bars() -> None:
    result = evaluate_signal_hold_return(
        {"code": "000001", "buy_date": "2026-06-18", "signal_price": 10.0},
        provider=InMemoryProvider(),
        hold_days=2,
    )

    assert result["evaluation_status"] == "evaluated"
    assert result["max_return_pct"] == 15.0
    assert result["close_return_pct"] == 12.0
    assert result["hit_target"] is True


def test_summarize_evaluated_trades_counts_missing() -> None:
    summary = summarize_evaluated_trades(
        [
            {"evaluation_status": "evaluated", "win": True, "hit_target": True, "max_return_pct": 12, "close_return_pct": 5, "min_return_pct": -2},
            {"evaluation_status": "data_missing"},
        ]
    )

    assert summary["trades"] == 2
    assert summary["evaluated"] == 1
    assert summary["data_missing"] == 1
    assert summary["win_rate"] == 100.0


def test_run_selection_backtest_replays_real_workflow_and_evaluates(monkeypatch) -> None:
    monkeypatch.setattr(selection_workflow, "check_no_touch_filters", lambda **_: (True, {"no_touch_ok": True, "no_touch_reasons": []}))
    strategy_id = "unit_test_real_workflow_strategy"
    original = dict(registry._STRATEGIES)
    try:
        registry._STRATEGIES[strategy_id] = registry.SelectionStrategy(
            strategy_id=strategy_id,
            strategy_name="Unit Test Strategy",
            description="registered test strategy",
            runner=_registered_test_strategy,
        )
        config = runner.SelectionBacktestConfig(
            strategy_ids=[strategy_id],
            start_date="2026-06-18",
            end_date="2026-06-18",
            hold_days=2,
            max_stocks=1,
            min_sector_score=0,
            min_final_score=0,
        )

        output = runner.run_selection_backtest(config, provider=InMemoryProvider(), trade_dates=["2026-06-18"])
    finally:
        registry._STRATEGIES.clear()
        registry._STRATEGIES.update(original)

    assert output["counts"] == {"trade_days": 1, "trades": 1, "evaluated": 1, "data_missing": 0}
    assert output["summary"]["hit_target_rate"] == 100.0
    assert output["daily_results"][0]["workflow_counts"]["selected"] == 1
    assert output["trades"][0]["strategy_id"] == strategy_id


def test_build_config_normalizes_strategy_and_dates() -> None:
    args = runner.parse_args(
        [
            "--strategies",
            "attack_third_buy_30m,attack_third_buy_daily",
            "--start-date",
            "20260618",
            "--end-date",
            "20260620",
            "--max-trade-days",
            "2",
        ]
    )

    config = runner.build_config(args)

    assert config.strategy_ids == ["attack_third_buy_30m", "attack_third_buy_daily"]
    assert config.start_date == "2026-06-18"
    assert config.end_date == "2026-06-20"
    assert config.max_trade_days == 2

def test_workflow_disables_sector_top3_for_jie_ge_dragon_pullback(monkeypatch) -> None:
    calls = []

    def fake_no_touch(**kwargs):
        calls.append(kwargs)
        return True, {"no_touch_ok": True, "no_touch_reasons": [], "no_touch_enforce_sector_top3": kwargs.get("enforce_sector_top3")}

    monkeypatch.setattr(selection_workflow, "score_sector_strength_safe", lambda **_: {"kaipanla_strength_score": 100.0})
    monkeypatch.setattr(selection_workflow, "check_no_touch_filters", fake_no_touch)

    selected, rejected = selection_workflow.enrich_and_filter_signals(
        [{"strategy_id": "jie_ge_emotion_dragon_pullback", "code": "000001", "buy_date": "2026-06-18", "signal_score": 80.0}],
        sector_score_date="2026-06-18",
        sector_lookback_trade_days=10,
        min_sector_score=0,
        min_final_score=0,
        top_n=10,
    )

    assert selected and not rejected
    assert calls[-1]["enforce_sector_top3"] is False


def test_workflow_enforces_sector_top3_for_breakout_first_board(monkeypatch) -> None:
    calls = []

    def fake_no_touch(**kwargs):
        calls.append(kwargs)
        return True, {"no_touch_ok": True, "no_touch_reasons": [], "no_touch_enforce_sector_top3": kwargs.get("enforce_sector_top3")}

    monkeypatch.setattr(selection_workflow, "score_sector_strength_safe", lambda **_: {"kaipanla_strength_score": 100.0})
    monkeypatch.setattr(selection_workflow, "check_no_touch_filters", fake_no_touch)

    selected, rejected = selection_workflow.enrich_and_filter_signals(
        [{"strategy_id": "breakout_first_board", "code": "000001", "buy_date": "2026-06-18", "signal_score": 80.0}],
        sector_score_date="2026-06-18",
        sector_lookback_trade_days=10,
        min_sector_score=0,
        min_final_score=0,
        top_n=10,
    )

    assert selected and not rejected
    assert calls[-1]["enforce_sector_top3"] is True

def test_dynamic_exit_takes_profit_before_time_exit() -> None:
    from backtest.evaluation.hold_return import evaluate_signal_dynamic_exit

    class ProfitProvider(InMemoryProvider):
        def get_daily_bars(self, code, start_date=None, end_date=None, adjust="hfq"):
            return pd.DataFrame([
                {"trade_date": "2026-06-18", "open": 10, "high": 10.1, "low": 9.9, "close": 10},
                {"trade_date": "2026-06-19", "open": 10, "high": 10.7, "low": 9.8, "close": 10.2},
                {"trade_date": "2026-06-22", "open": 10.2, "high": 10.3, "low": 9.0, "close": 9.2},
            ])

    result = evaluate_signal_dynamic_exit({"code": "000001", "buy_date": "2026-06-18", "signal_price": 10}, provider=ProfitProvider(), hold_days=2)

    assert result["dynamic_exit_reason"] == "take_profit"
    assert result["dynamic_return_pct"] == 6.0


def test_dynamic_exit_stops_loss() -> None:
    from backtest.evaluation.hold_return import evaluate_signal_dynamic_exit

    class LossProvider(InMemoryProvider):
        def get_daily_bars(self, code, start_date=None, end_date=None, adjust="hfq"):
            return pd.DataFrame([
                {"trade_date": "2026-06-18", "open": 10, "high": 10.1, "low": 9.9, "close": 10},
                {"trade_date": "2026-06-19", "open": 10, "high": 10.1, "low": 9.4, "close": 9.5},
            ])

    result = evaluate_signal_dynamic_exit({"code": "000001", "buy_date": "2026-06-18", "signal_price": 10}, provider=LossProvider(), hold_days=2)

    assert result["dynamic_exit_reason"] == "stop_loss"
    assert result["dynamic_return_pct"] == -4.0

