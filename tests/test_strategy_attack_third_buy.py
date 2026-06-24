from datetime import datetime, timedelta

import pandas as pd

from app.core.data.market_data_provider import MarketDataProvider
from backtest.strategies import strategy_attack_third_buy as attack
from backtest.strategies.strategy_attack_third_buy import (
    analyze_attack_third_buy_signal,
    build_theme_heat_map,
    detect_attack_third_buy_structure,
    scan_attack_third_buy,
)


def _make_attack_df() -> pd.DataFrame:
    rows = []
    start = datetime(2025, 1, 1)
    price = 10.0
    for i in range(125):
        price *= 1.004
        rows.append({
            "date": start + timedelta(days=i),
            "open": price * 0.995,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1_000_000 + i * 1000,
        })
    for i in range(35):
        close = 16.5 + (i % 6) * 0.08
        rows.append({
            "date": start + timedelta(days=125 + i),
            "open": close * 0.995,
            "high": 17.1,
            "low": 16.25,
            "close": close,
            "volume": 1_200_000,
        })
    # breakout
    for j, close in enumerate([17.7, 17.95, 17.8]):
        rows.append({
            "date": start + timedelta(days=160 + j),
            "open": close * 0.98,
            "high": close * 1.02,
            "low": close * 0.97,
            "close": close,
            "volume": 2_000_000,
        })
    # pullback above platform high
    for j, close in enumerate([17.45, 17.35, 17.4, 17.5]):
        rows.append({
            "date": start + timedelta(days=163 + j),
            "open": close * 1.005,
            "high": close * 1.01,
            "low": 16.9,
            "close": close,
            "volume": 950_000,
        })
    # restart day
    rows.append({
        "date": start + timedelta(days=167),
        "open": 17.55,
        "high": 18.35,
        "low": 17.45,
        "close": 18.25,
        "volume": 2_400_000,
    })
    return pd.DataFrame(rows)


def test_build_theme_heat_map_from_limit_up_payload():
    payload = {
        "limit_up": {
            "items": [
                {
                    "stock_code": "000001",
                    "stock_name": "测试A",
                    "sw_level1_name": "机器人",
                    "industry": "机器人",
                    "limit_up_days": 2,
                    "first_limit_up_time": "09:25",
                    "sealed_amount": 200_000_000,
                    "break_board_count": 0,
                },
                {
                    "stock_code": "000002",
                    "stock_name": "测试B",
                    "sw_level1_name": "机器人",
                    "industry": "机器人",
                    "limit_up_days": 1,
                    "first_limit_up_time": "10:00",
                    "sealed_amount": 50_000_000,
                    "break_board_count": 1,
                },
            ]
        }
    }
    heat = build_theme_heat_map(payload)
    assert "机器人" in heat
    assert heat["机器人"]["limit_up_count"] == 2
    assert heat["机器人"]["theme_heat_score"] > 30


def test_detect_attack_third_buy_structure_passes_platform_breakout_pullback_restart():
    signal = detect_attack_third_buy_structure(_make_attack_df(), now=datetime(2025, 5, 3))
    assert signal is not None
    assert signal["third_buy_ok"] is True
    assert signal["structure_period"] == "30"
    assert signal["structure_freq"] == "30分钟"
    assert signal["pre_uptrend_pct"] >= 25
    assert signal["pullback_low"] >= signal["platform_high"] * 0.98
    assert signal["restart_volume_ratio"] >= 1.1


def test_detect_attack_third_buy_structure_can_still_use_daily_period():
    signal = detect_attack_third_buy_structure(_make_attack_df(), now=datetime(2025, 5, 3), structure_period="daily")
    assert signal is not None
    assert signal["structure_period"] == "daily"
    assert signal["structure_freq"] == "日线"


def test_analyze_attack_third_buy_signal_scores_theme_and_structure():
    theme = {
        "theme": "机器人",
        "theme_heat_score": 80,
        "theme_limit_up_count": 6,
        "theme_max_limit_up_days": 3,
        "stock_limit_up_days": 1,
        "stock_first_limit_up_time": "09:25",
        "stock_sealed_amount": 100_000_000,
        "stock_break_board_count": 0,
    }
    signal = analyze_attack_third_buy_signal(_make_attack_df(), theme, now=datetime(2025, 5, 3))
    assert signal is not None
    assert signal["attack_score"] > 40
    assert signal["theme"] == "机器人"
    assert signal["score_breakdown"]["theme_heat_score"] == 80


def test_scan_attack_third_buy_filters_stale_signals(monkeypatch):
    monkeypatch.setattr(
        attack,
        "get_hot_theme_codes",
        lambda payload, min_heat_score=45.0: {"000001": {"theme": "测试", "theme_heat_score": 80, "stock_name": "测试股"}},
    )
    monkeypatch.setattr(attack.bigamap_provider, "get_limit_up_review", lambda: {"limit_up": {"items": []}})
    monkeypatch.setattr(attack.kaipanla_provider, "get_cached_hot_stock_map", lambda: {})
    monkeypatch.setattr(attack.data_provider, "get_realtime_quote", lambda code: {"name": "测试股", "price": 10, "turnover": 100_000_000})
    minute_calls = []
    monkeypatch.setattr(attack.data_provider, "get_kline_minute", lambda code, period="30": minute_calls.append((code, period)) or _make_attack_df())
    monkeypatch.setattr(attack.data_provider, "get_kline_daily", lambda code, start_date="": (_ for _ in ()).throw(AssertionError("daily kline should not be used by default")))
    monkeypatch.setattr(
        attack,
        "analyze_attack_third_buy_signal",
        lambda df, theme, now=None, structure_period="30": {"days_ago": 28, "attack_score": 50, "third_buy_reason": "test"},
    )

    assert scan_attack_third_buy(max_stocks=1, signal_window_days=10, throttle_seconds=0) == []
    assert minute_calls == [("000001", "30")]


class FakeMarketDataProvider(MarketDataProvider):
    def __init__(self):
        self.minute_calls = []
        self.daily_calls = []

    def get_realtime_quote(self, code):
        return {"name": "测试股", "price": 10, "turnover": 100_000_000}

    def get_minute_bars(self, code, period="30", start_date=None, end_date=None):
        self.minute_calls.append((code, period))
        return _make_attack_df()

    def get_daily_bars(self, code, start_date=None, end_date=None, adjust="hfq"):
        self.daily_calls.append((code, start_date))
        return _make_attack_df()

    def get_stock_list(self, limit=None):
        return pd.DataFrame()

    def get_sector_membership(self, code, current_only=True):
        return pd.DataFrame()

    def get_sector_strength(self, start_date=None, end_date=None):
        return pd.DataFrame()


def test_scan_attack_third_buy_uses_injected_market_data_provider(monkeypatch):
    monkeypatch.setattr(
        attack,
        "get_hot_theme_codes",
        lambda payload, min_heat_score=45.0: {"000001": {"theme": "测试", "theme_heat_score": 80, "stock_name": "测试股"}},
    )
    monkeypatch.setattr(attack.bigamap_provider, "get_limit_up_review", lambda: {"limit_up": {"items": []}})
    monkeypatch.setattr(attack.kaipanla_provider, "get_cached_hot_stock_map", lambda: {})
    monkeypatch.setattr(attack.data_provider, "get_realtime_quote", lambda code: (_ for _ in ()).throw(AssertionError("global data_provider quote should not be used")))
    monkeypatch.setattr(attack.data_provider, "get_kline_minute", lambda code, period="30": (_ for _ in ()).throw(AssertionError("global data_provider minute should not be used")))
    monkeypatch.setattr(
        attack,
        "analyze_attack_third_buy_signal",
        lambda df, theme, now=None, structure_period="30": {"days_ago": 1, "attack_score": 50, "third_buy_reason": "test"},
    )
    provider = FakeMarketDataProvider()

    result = scan_attack_third_buy(max_stocks=1, signal_window_days=10, throttle_seconds=0, provider=provider)

    assert result[0]["code"] == "000001"
    assert result[0]["name"] == "测试股"
    assert provider.minute_calls == [("000001", "30")]
    assert provider.daily_calls == []
