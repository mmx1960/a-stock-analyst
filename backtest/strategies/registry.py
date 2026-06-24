from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.data.market_data_provider import MarketDataProvider
from backtest.strategies.strategy_attack_third_buy import scan_attack_third_buy
from backtest.strategies.strategy_daily_uptrend_ma13_pullback import scan_daily_uptrend_30m_ma13_pullback
from backtest.strategies.strategy_d_shen_trend_pullback import scan_d_shen_trend_30m_pullback
from backtest.strategies.strategy_jie_ge_emotion_dragon_pullback import scan_jie_ge_emotion_dragon_pullback
from backtest.strategies.strategy_nine_breakout import scan_strategy_nine_breakout

StrategyRunner = Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True)
class SelectionStrategy:
    strategy_id: str
    strategy_name: str
    description: str
    runner: StrategyRunner
    default_params: dict[str, Any] = field(default_factory=dict)

    def run(self, *, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
        merged = {**self.default_params, **params}
        signals = self.runner(provider=provider, **merged)
        return [normalize_strategy_signal(signal, self) for signal in signals]


def normalize_strategy_signal(signal: dict[str, Any], strategy: SelectionStrategy) -> dict[str, Any]:
    normalized = dict(signal)
    normalized.setdefault("strategy_id", strategy.strategy_id)
    normalized.setdefault("strategy_name", strategy.strategy_name)
    normalized.setdefault("signal_score", normalized.get("attack_score", 0))
    normalized.setdefault("signal_reason", normalized.get("third_buy_reason") or normalized.get("reason"))
    normalized.setdefault("signal_price", normalized.get("price") or normalized.get("current_price"))
    normalized.setdefault("strategy_metadata", {})
    return normalized


def _run_attack_third_buy(*, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
    return scan_attack_third_buy(provider=provider, **params)


def _run_daily_uptrend_ma13_pullback(*, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
    return scan_daily_uptrend_30m_ma13_pullback(provider=provider, **params)


def _run_d_shen_trend_pullback(*, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
    return scan_d_shen_trend_30m_pullback(provider=provider, **params)


def _run_jie_ge_emotion_dragon_pullback(*, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
    return scan_jie_ge_emotion_dragon_pullback(provider=provider, **params)


def _run_strategy_nine_breakout(*, provider: MarketDataProvider | None = None, **params: Any) -> list[dict[str, Any]]:
    return scan_strategy_nine_breakout(provider=provider, **params)


_STRATEGIES: dict[str, SelectionStrategy] = {
    "attack_third_buy_30m": SelectionStrategy(
        strategy_id="attack_third_buy_30m",
        strategy_name="30分钟进攻型三买",
        description="热点池内按 30 分钟平台突破、回踩不破、再启动结构选股。",
        runner=_run_attack_third_buy,
        default_params={"structure_period": "30"},
    ),
    "attack_third_buy_daily": SelectionStrategy(
        strategy_id="attack_third_buy_daily",
        strategy_name="日线进攻型三买",
        description="进攻型三买的日线结构回退版本。",
        runner=_run_attack_third_buy,
        default_params={"structure_period": "daily"},
    ),
    "daily_uptrend_30m_ma13_pullback": SelectionStrategy(
        strategy_id="daily_uptrend_30m_ma13_pullback",
        strategy_name="日线上涨趋势 + 30分钟回踩MA13",
        description="日线 MA20>MA60 且价格保持上涨趋势，30分钟线回踩并收回 MA13 时买入。",
        runner=_run_daily_uptrend_ma13_pullback,
        default_params={},
    ),
    "d_shen_trend_30m_pullback": SelectionStrategy(
        strategy_id="d_shen_trend_30m_pullback",
        strategy_name="D神趋势 + 板块资金 + 30分钟回踩",
        description="按 D神框架先看大周期趋势和板块资金，再用 30分钟均线粘合/回踩 MA13 触发买点。",
        runner=_run_d_shen_trend_pullback,
        default_params={},
    ),
    "jie_ge_emotion_dragon_pullback": SelectionStrategy(
        strategy_id="jie_ge_emotion_dragon_pullback",
        strategy_name="杰哥情绪龙头低吸",
        description="按杰哥框架优先核心人气/资金态度，第一波主升后回调，30分钟企稳反抽触发低吸。",
        runner=_run_jie_ge_emotion_dragon_pullback,
        default_params={},
    ),
    "strategy_nine_breakout": SelectionStrategy(
        strategy_id="strategy_nine_breakout",
        strategy_name="九号策略：强势板块前高突破",
        description="近 5 日强势板块内，筛选半年上升趋势中快要突破前期高点或刚突破前期高点的股票。",
        runner=_run_strategy_nine_breakout,
        default_params={},
    ),
}


def get_strategy(strategy_id: str) -> SelectionStrategy:
    try:
        return _STRATEGIES[strategy_id]
    except KeyError as exc:
        available = ", ".join(sorted(_STRATEGIES))
        raise ValueError(f"Unknown strategy_id={strategy_id!r}; available: {available}") from exc


def list_strategies() -> list[SelectionStrategy]:
    return [strategy for _, strategy in sorted(_STRATEGIES.items())]


def parse_strategy_ids(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [item.strip() for item in str(value).split(",") if item.strip()]
