from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.core.data.market_data_provider import DuckDBMarketDataProvider, MarketDataProvider
from app.core.storage.duckdb_store import DuckDBStore
from backtest.filters.no_touch_filters import check_no_touch_filters
from backtest.strategies.kaipanla_sector_strength_score import expand_sector_aliases, score_sector_strength_safe
from backtest.strategies.registry import get_strategy, parse_strategy_ids

_SECTOR_STRENGTH_TOP_N_CACHE: dict[tuple[str, int], list[dict[str, Any]]] = {}


@dataclass(frozen=True)
class SelectionWorkflowConfig:
    strategy_ids: list[str] = field(default_factory=lambda: ["attack_third_buy_30m"])
    max_stocks: int = 80
    min_heat_score: float = 45.0
    signal_window_days: int = 10
    pool_mode: str = "combined"
    board_lookback_days: int = 10
    board_top_n: int = 10
    board_min_appearances: int = 3
    throttle: float = 0.3
    sector_score_date: str = ""
    as_of_date: str = ""
    sector_lookback_trade_days: int = 10
    min_sector_score: float = 65.0
    min_final_score: float = 45.0
    top_n: int = 30
    require_sector_strength_top_n: int = 10
    merge_mode: str = "best_score"
    include_watchlist: bool = False


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _requires_sector_top3(strategy_id: str) -> bool:
    normalized = str(strategy_id or "")
    return normalized in {"attack_third_buy_30m", "breakout_first_board"} or "first_board" in normalized or "breakout" in normalized


def check_sector_strength_top_n(
    *,
    candidate_sectors: list[str] | set[str] | tuple[str, ...],
    trade_date: str,
    top_n: int = 10,
    store: DuckDBStore | None = None,
) -> tuple[bool, dict[str, Any]]:
    sectors = expand_sector_aliases(candidate_sectors)
    if top_n <= 0:
        return True, {"sector_strength_top_n_required": 0, "sector_strength_top_n_skipped": True}
    if not sectors:
        return False, {
            "sector_strength_top_n_ok": False,
            "sector_strength_top_n_required": int(top_n),
            "sector_strength_top_n_reason": "no_candidate_sectors",
            "sector_strength_top_n_matched": [],
        }
    normalized_date = str(trade_date or "")[:10]
    cache_key = (normalized_date, int(top_n))
    ranked = _SECTOR_STRENGTH_TOP_N_CACHE.get(cache_key)
    if ranked is None:
        store = store or DuckDBStore()
        with store._connect() as con:
            frame = con.execute(
                """
                with ranked as (
                  select trade_date, sector_name, strength_score, capital_score, turnover,
                         row_number() over (
                           partition by trade_date
                           order by coalesce(strength_score,0) desc, coalesce(capital_score,0) desc, coalesce(turnover,0) desc
                         ) as rn
                  from kaipanla_sector_strength
                  where trade_date = ?
                )
                select * from ranked where rn <= ? order by rn asc
                """,
                [normalized_date, int(top_n)],
            ).df()
        ranked = frame.to_dict("records") if frame is not None and not frame.empty else []
        _SECTOR_STRENGTH_TOP_N_CACHE[cache_key] = ranked
    matched = [row for row in ranked if str(row.get("sector_name") or "").strip() in sectors]
    if matched:
        best = matched[0]
        return True, {
            "sector_strength_top_n_ok": True,
            "sector_strength_top_n_required": int(top_n),
            "sector_strength_top_n_trade_date": normalized_date,
            "sector_strength_top_n_matched": [str(row.get("sector_name")) for row in matched],
            "sector_strength_top_n_best_rank": int(_safe_float(best.get("rn"))),
            "sector_strength_top_n_best_sector": str(best.get("sector_name")),
        }
    return False, {
        "sector_strength_top_n_ok": False,
        "sector_strength_top_n_required": int(top_n),
        "sector_strength_top_n_trade_date": normalized_date,
        "sector_strength_top_n_reason": "candidate_sector_not_in_top_n",
        "sector_strength_top_n_available_sectors": [str(row.get("sector_name")) for row in ranked],
        "sector_strength_top_n_matched": [],
    }


def workflow_final_score(signal: dict[str, Any]) -> tuple[float, dict[str, float | str]]:
    signal_score = _safe_float(signal.get("signal_score", signal.get("attack_score")))
    sector_score = _safe_float(signal.get("kaipanla_strength_score"))
    theme_heat = _safe_float(signal.get("theme_heat_score"))
    market_heat = _safe_float(signal.get("kaipanla_market_heat_score"))
    # D神评价：资金一致性不会假。策略形态只是买点，板块资金必须从配角提高到硬约束。
    final_score = signal_score * 0.45 + sector_score * 0.45 + theme_heat * 0.07 + market_heat * 0.03
    breakdown: dict[str, float | str] = {
        "signal_score_component": round(signal_score * 0.45, 2),
        "sector_score_component": round(sector_score * 0.45, 2),
        "theme_heat_component": round(theme_heat * 0.07, 2),
        "market_heat_component": round(market_heat * 0.03, 2),
        "scoring_profile": "d_shen_fund_consistency_v2",
    }
    if "attack_score" in signal:
        breakdown["attack_score_component"] = round(_safe_float(signal.get("attack_score")) * 0.45, 2)
    return round(final_score, 2), breakdown


def enrich_and_filter_signals(
    signals: list[dict[str, Any]],
    *,
    sector_score_date: str,
    sector_lookback_trade_days: int,
    min_sector_score: float,
    min_final_score: float,
    top_n: int,
    require_sector_strength_top_n: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for signal in signals:
        buy_date = str(signal.get("buy_date") or sector_score_date)
        score = score_sector_strength_safe(
            code=str(signal.get("code") or ""),
            buy_date=buy_date,
            sector_name=signal.get("theme"),
            lookback_trade_days=sector_lookback_trade_days,
        )
        enriched = {**signal, **score}
        top_n_ok, top_n_meta = check_sector_strength_top_n(
            candidate_sectors=enriched.get("kaipanla_candidate_sectors") or [],
            trade_date=buy_date,
            top_n=require_sector_strength_top_n,
        )
        enriched.update(top_n_meta)
        final_score, final_breakdown = workflow_final_score(enriched)
        enriched["workflow_final_score"] = final_score
        enriched["workflow_score_breakdown"] = final_breakdown
        reject_reasons = []
        if not top_n_ok:
            reject_reasons.append("sector_strength_not_top10_on_buy_date")
        if _safe_float(enriched.get("kaipanla_strength_score")) < min_sector_score:
            reject_reasons.append("sector_score_below_threshold")
        if final_score < min_final_score:
            reject_reasons.append("final_score_below_threshold")
        enriched["workflow_reject_reasons"] = reject_reasons
        strategy_id = str(enriched.get("strategy_id") or "")
        no_touch_ok, no_touch_meta = check_no_touch_filters(
            code=str(enriched.get("code") or ""),
            buy_date=buy_date,
            enforce_sector_top3=_requires_sector_top3(strategy_id),
        )
        enriched.update(no_touch_meta)
        if not no_touch_ok:
            reject_reasons.extend(no_touch_meta.get("no_touch_reasons", ["no_touch_filter_failed"]))
            enriched["workflow_reject_reasons"] = reject_reasons
        if reject_reasons:
            rejected.append(enriched)
        else:
            passed.append(enriched)

    passed.sort(key=lambda x: (-_safe_float(x.get("workflow_final_score")), -_safe_float(x.get("kaipanla_strength_score")), -_safe_float(x.get("signal_score"))))
    rejected.sort(key=lambda x: (-_safe_float(x.get("workflow_final_score")), -_safe_float(x.get("kaipanla_strength_score"))))
    if top_n > 0:
        passed = passed[:top_n]
    return passed, rejected


def _dedupe_signals(signals: list[dict[str, Any]], merge_mode: str = "best_score") -> list[dict[str, Any]]:
    if merge_mode != "best_score":
        return signals
    best_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for signal in signals:
        key = (
            str(signal.get("code") or ""),
            str(signal.get("buy_date") or ""),
            str(signal.get("strategy_id") or ""),
        )
        current = best_by_key.get(key)
        if current is None or _safe_float(signal.get("signal_score")) > _safe_float(current.get("signal_score")):
            best_by_key[key] = signal
    return list(best_by_key.values())


def run_selection_workflow(
    config: SelectionWorkflowConfig,
    *,
    provider: MarketDataProvider | None = None,
) -> dict[str, Any]:
    provider = provider or DuckDBMarketDataProvider()
    sector_score_date = config.sector_score_date or datetime.now().strftime("%Y-%m-%d")
    raw_signals: list[dict[str, Any]] = []
    strategy_counts: dict[str, int] = {}

    for strategy_id in config.strategy_ids:
        strategy = get_strategy(strategy_id)
        signals = strategy.run(
            provider=provider,
            max_stocks=config.max_stocks,
            min_heat_score=config.min_heat_score,
            signal_window_days=config.signal_window_days,
            pool_mode=config.pool_mode,
            board_lookback_days=config.board_lookback_days,
            board_top_n=config.board_top_n,
            board_min_appearances=config.board_min_appearances,
            throttle_seconds=config.throttle,
            as_of_date=config.as_of_date or sector_score_date,
            include_watchlist=config.include_watchlist,
        )
        strategy_counts[strategy_id] = len(signals)
        raw_signals.extend(signals)

    raw_signals = _dedupe_signals(raw_signals, merge_mode=config.merge_mode)
    triggered_signals = [signal for signal in raw_signals if signal.get("signal_status", "triggered") == "triggered"]
    watch_signals = [signal for signal in raw_signals if signal.get("signal_status") == "watch"]
    selected, rejected = enrich_and_filter_signals(
        triggered_signals,
        sector_score_date=sector_score_date,
        sector_lookback_trade_days=config.sector_lookback_trade_days,
        min_sector_score=config.min_sector_score,
        min_final_score=config.min_final_score,
        top_n=config.top_n,
        require_sector_strength_top_n=config.require_sector_strength_top_n,
    )
    return {
        "workflow": "stock-selection-workflow-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_ids": list(config.strategy_ids),
        "strategy_counts": strategy_counts,
        "sector_score_date": sector_score_date,
        "sector_lookback_trade_days": config.sector_lookback_trade_days,
        "thresholds": {
            "min_heat_score": config.min_heat_score,
            "min_sector_score": config.min_sector_score,
            "min_final_score": config.min_final_score,
            "top_n": config.top_n,
            "require_sector_strength_top_n": config.require_sector_strength_top_n,
        },
        "config": asdict(config),
        "counts": {
            "raw_signals": len(raw_signals),
            "triggered_signals": len(triggered_signals),
            "watch_signals": len(watch_signals),
            "selected": len(selected),
            "rejected": len(rejected),
        },
        "selected": selected,
        "watchlist": watch_signals,
        "rejected": rejected,
    }


def config_from_strategy_arg(strategy: str | list[str], **kwargs: Any) -> SelectionWorkflowConfig:
    return SelectionWorkflowConfig(strategy_ids=parse_strategy_ids(strategy), **kwargs)
