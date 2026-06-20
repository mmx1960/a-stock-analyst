#!/usr/bin/env python3
"""
v3.1 AB compare runner

目标：
- 固定同一样本股票池
- 分别跑 baseline / confirmed
- 逐笔 flatten buy points
- 强制校验 confirmed_keys ⊆ baseline_keys
- 输出 summary / delta / rejection_report
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data_provider import data_provider
from backtest.strategies.strategy_v3_1_backtest import backtest_stock_v3_1

SCENARIO_BASELINE = {
    "name": "baseline",
    "enforce_weekly_context": False,
    "enforce_historical_high_filter": True,
    "enforce_ma250_filter": False,
    "enforce_volume_pattern": False,
    "enforce_entry_confirmation": False,
}

SCENARIO_CONFIRMED = {
    "name": "confirmed",
    "enforce_weekly_context": False,
    "enforce_historical_high_filter": True,
    "enforce_ma250_filter": True,
    "enforce_volume_pattern": True,
    "enforce_entry_confirmation": True,
}


@dataclass
class Meta:
    strategy: str
    seed: int
    sample_size: int
    max_stocks: int | None
    start_year: int
    hold_weeks: int
    sample_stocks: list[str]
    generated_at: str


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def safe_str(x: Any, default: str = "") -> str:
    if x is None:
        return default
    return str(x)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def mean_or_zero(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median_or_zero(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def date_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v3.1 AB compare")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--hold-weeks", type=int, default=10)
    parser.add_argument(
        "--output",
        type=str,
        default="backtest/results_v6/ab_v3_1_compare.json",
    )
    return parser.parse_args()


def normalize_stock_list(raw: Any) -> list[tuple[str, str]]:
    if raw is None:
        return []

    pairs: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                code = safe_str(item.get("code")).strip()
                name = safe_str(item.get("name") or code).strip() or code
            elif isinstance(item, str):
                code = item.strip()
                name = code
            else:
                code = safe_str(getattr(item, "code", "")).strip()
                name = safe_str(getattr(item, "name", code)).strip() or code
            if code:
                pairs.append((code, name))
        return sorted(set(pairs), key=lambda x: x[0])

    if hasattr(raw, "to_dict") and hasattr(raw, "columns"):
        cols = set(list(raw.columns))
        code_col = "code" if "code" in cols else None
        if code_col is None:
            for c in ["symbol", "股票代码", "ts_code"]:
                if c in cols:
                    code_col = c
                    break
        name_col = "name" if "name" in cols else None
        if name_col is None:
            for c in ["股票简称", "简称"]:
                if c in cols:
                    name_col = c
                    break
        if code_col:
            for _, row in raw.iterrows():
                code = safe_str(row.get(code_col)).strip()
                name = safe_str(row.get(name_col) if name_col else code).strip() or code
                if code:
                    pairs.append((code, name))
            return sorted(set(pairs), key=lambda x: x[0])

    raise ValueError("Unsupported stock list shape from data_provider.get_stock_list()")


def load_sample_stocks(sample_size: int, seed: int, max_stocks: int | None = None) -> list[tuple[str, str]]:
    raw_stock_list = data_provider.get_stock_list()
    stock_pairs = normalize_stock_list(raw_stock_list)

    if not stock_pairs:
        raise RuntimeError("stock list is empty")

    rng = random.Random(seed)
    if sample_size >= len(stock_pairs):
        sample = stock_pairs[:]
        rng.shuffle(sample)
    else:
        sample = rng.sample(stock_pairs, sample_size)

    if max_stocks is not None:
        sample = sample[:max_stocks]

    return sample


def run_scenario_for_stock_list(
    stock_pairs: list[tuple[str, str]],
    scenario: dict[str, Any],
    start_year: int,
    hold_weeks: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for idx, (code, name) in enumerate(stock_pairs, start=1):
        print(f"[{scenario['name']}] [{idx}/{len(stock_pairs)}] {code} {name}", flush=True)
        result = backtest_stock_v3_1(
            code=code,
            name=name,
            start_year=start_year,
            hold_weeks=hold_weeks,
            enforce_weekly_context=scenario["enforce_weekly_context"],
            enforce_historical_high_filter=scenario["enforce_historical_high_filter"],
            enforce_ma250_filter=scenario["enforce_ma250_filter"],
            enforce_volume_pattern=scenario["enforce_volume_pattern"],
            enforce_entry_confirmation=scenario["enforce_entry_confirmation"],
        )
        result["scenario"] = scenario["name"]
        results.append(result)

    return results


def flatten_buy_points(stock_level_results: list[dict[str, Any]], scenario_name: str) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    for stock_result in stock_level_results:
        code = safe_str(stock_result.get("code"))
        name = safe_str(stock_result.get("name"))
        buy_points = stock_result.get("buy_points") or []

        for bp in buy_points:
            item = {
                "scenario": scenario_name,
                "code": code,
                "name": name,
                "buy_date": safe_str(bp.get("buy_date") or bp.get("date")),
                "detection_date": safe_str(bp.get("detection_date")),
                "signal_type": safe_str(bp.get("signal_type")),
                "buy_price": safe_float(bp.get("buy_price")),
                "max_return": safe_float(bp.get("max_return")) / 100.0,
                "current_return": safe_float(bp.get("current_return")) / 100.0,
                "status": safe_str(bp.get("status")),
                "entry_ok": bp.get("entry_ok"),
                "entry_reason": bp.get("entry_reason"),
                "entry_signal_type": bp.get("entry_signal_type"),
                "entry_score": bp.get("entry_score"),
                "entry_min_score_required": bp.get("entry_min_score_required"),
                "entry_score_max": bp.get("entry_score_max"),
                "entry_failed_checks": bp.get("entry_failed_checks") or [],
                "entry_body_rebound_pct": bp.get("entry_body_rebound_pct"),
                "entry_close_from_low_pct": bp.get("entry_close_from_low_pct"),
                "entry_close_above_prev_close_pct": bp.get("entry_close_above_prev_close_pct"),
                "entry_volume_ratio": bp.get("entry_volume_ratio"),
                "historical_high_ok": bp.get("historical_high_ok"),
                "historical_high_reason": bp.get("historical_high_reason"),
                "historical_high_price": bp.get("historical_high_price"),
                "buy_close_price": bp.get("buy_close_price"),
                "current_to_historical_high_ratio": bp.get("current_to_historical_high_ratio"),
                "min_current_to_historical_high_ratio": bp.get("min_current_to_historical_high_ratio"),
                "ma250_ok": bp.get("ma250_ok"),
                "ma250_reason": bp.get("ma250_reason"),
                "ma250_value": bp.get("ma250_value"),
                "current_to_ma250_ratio": bp.get("current_to_ma250_ratio"),
                "min_current_to_ma250_ratio": bp.get("min_current_to_ma250_ratio"),
                "volume_pattern_ok": bp.get("volume_pattern_ok"),
                "volume_pattern_reason": bp.get("volume_pattern_reason"),
                "volume_pattern_anchor_date": bp.get("volume_pattern_anchor_date"),
                "volume_pattern_anchor_volume": bp.get("volume_pattern_anchor_volume"),
                "volume_pattern_anchor_idx": bp.get("volume_pattern_anchor_idx"),
                "volume_pattern_anchor_spike_ratio": bp.get("volume_pattern_anchor_spike_ratio"),
                "volume_pattern_bearish_violation_date": bp.get("volume_pattern_bearish_violation_date"),
                "volume_pattern_bearish_violation_ratio": bp.get("volume_pattern_bearish_violation_ratio"),
                "weekly_uptrend_context_ok": bp.get("weekly_uptrend_context_ok"),
                "weekly_uptrend_reason": bp.get("weekly_uptrend_reason"),
                "weekly_pullback_bars": bp.get("weekly_pullback_bars"),
                "weekly_context_window": bp.get("weekly_context_window"),
                "weekly_context_half_below_ratio": bp.get("weekly_context_half_below_ratio"),
                "weekly_pre_pullback_high": bp.get("weekly_pre_pullback_high"),
                "weekly_current_close": bp.get("weekly_current_close"),
                "weekly_slow_volume_ok": bp.get("weekly_slow_volume_ok"),
                "weekly_slow_volume_reason": bp.get("weekly_slow_volume_reason"),
                "volume_gate_ok": bp.get("volume_gate_ok"),
                "volume_gate_reason": bp.get("volume_gate_reason"),
                "intraday_second_buy_ok": bp.get("intraday_second_buy_ok"),
                "intraday_second_buy_reason": bp.get("intraday_second_buy_reason"),
            }
            flattened.append(item)

    return flattened


def build_signal_key(signal: dict[str, Any]) -> tuple[str, str, str]:
    return (
        safe_str(signal.get("code")),
        safe_str(signal.get("detection_date") or signal.get("buy_date")),
        safe_str(signal.get("signal_type")),
    )


def resolve_primary_rejection_reason(signal: dict[str, Any]) -> str:
    if signal.get("ma250_ok") is False:
        return safe_str(signal.get("ma250_reason"), "ma250_failed")
    if signal.get("volume_pattern_ok") is False:
        return safe_str(signal.get("volume_pattern_reason"), "volume_pattern_failed")
    if signal.get("entry_ok") is False:
        return safe_str(signal.get("entry_reason"), "entry_failed")
    return "unknown"


def compute_summary(flattened_signals: list[dict[str, Any]], stock_level_results: list[dict[str, Any]]) -> dict[str, Any]:
    max_returns = [safe_float(x.get("max_return")) for x in flattened_signals if x.get("status") == "已实现"]
    current_returns = [safe_float(x.get("current_return")) for x in flattened_signals if x.get("current_return") is not None]

    signal_type_breakdown = Counter(safe_str(x.get("signal_type"), "unknown") for x in flattened_signals)
    entry_reason_breakdown = Counter(safe_str(x.get("entry_reason"), "unknown") for x in flattened_signals)

    valid_stocks = sum(1 for x in stock_level_results if (x.get("total_buy_points", 0) > 0 or not x.get("error")))
    stock_count = len(stock_level_results)

    return {
        "stock_count": stock_count,
        "valid_stocks": valid_stocks,
        "total_signals": len(flattened_signals),
        "avg_return": mean_or_zero(max_returns),
        "median_return": median_or_zero(max_returns),
        "max_return": max(max_returns) if max_returns else 0.0,
        "win_rate_gt_0": mean_or_zero([1.0 if v > 0 else 0.0 for v in max_returns]),
        "win_rate_gt_5pct": mean_or_zero([1.0 if v > 0.05 else 0.0 for v in max_returns]),
        "win_rate_gt_10pct": mean_or_zero([1.0 if v > 0.10 else 0.0 for v in max_returns]),
        "non_positive_max_return_count": sum(1 for v in max_returns if v <= 0),
        "negative_current_return_count": sum(1 for v in current_returns if v < 0),
        "signal_type_breakdown": dict(signal_type_breakdown),
        "entry_reason_breakdown": dict(entry_reason_breakdown),
    }


def build_rejection_report(rejected_signals: list[dict[str, Any]]) -> dict[str, Any]:
    max_returns = [safe_float(x.get("max_return")) for x in rejected_signals if x.get("status") == "已实现"]
    current_returns = [safe_float(x.get("current_return")) for x in rejected_signals if x.get("current_return") is not None]

    primary_reasons = Counter(resolve_primary_rejection_reason(x) for x in rejected_signals)
    signal_types = Counter(safe_str(x.get("signal_type"), "unknown") for x in rejected_signals)
    entry_scores = Counter(str(x.get("entry_score")) for x in rejected_signals)

    examples = []
    for x in rejected_signals[:10]:
        examples.append(
            {
                "code": x.get("code"),
                "buy_date": x.get("buy_date"),
                "detection_date": x.get("detection_date"),
                "signal_type": x.get("signal_type"),
                "primary_rejection_reason": resolve_primary_rejection_reason(x),
                "entry_score": x.get("entry_score"),
                "max_return": x.get("max_return"),
                "current_return": x.get("current_return"),
            }
        )

    return {
        "rejected_total": len(rejected_signals),
        "rejection_reason_breakdown": dict(primary_reasons),
        "rejected_signal_type_breakdown": dict(signal_types),
        "rejected_entry_score_breakdown": dict(entry_scores),
        "rejected_avg_max_return": mean_or_zero(max_returns),
        "rejected_median_max_return": median_or_zero(max_returns),
        "rejected_non_positive_max_return_count": sum(1 for v in max_returns if v <= 0),
        "rejected_negative_current_return_count": sum(1 for v in current_returns if v < 0),
        "rejected_examples": examples,
    }


def compute_delta(baseline_summary: dict[str, Any], confirmed_summary: dict[str, Any]) -> dict[str, Any]:
    numeric_keys = [
        "stock_count",
        "valid_stocks",
        "total_signals",
        "avg_return",
        "median_return",
        "max_return",
        "win_rate_gt_0",
        "win_rate_gt_5pct",
        "win_rate_gt_10pct",
        "non_positive_max_return_count",
        "negative_current_return_count",
    ]
    delta = {}
    for k in numeric_keys:
        delta[k] = safe_float(confirmed_summary.get(k)) - safe_float(baseline_summary.get(k))
    return delta


def compare_scenarios(
    baseline_signals: list[dict[str, Any]],
    confirmed_signals: list[dict[str, Any]],
    baseline_summary: dict[str, Any],
    confirmed_summary: dict[str, Any],
) -> dict[str, Any]:
    baseline_map = {build_signal_key(x): x for x in baseline_signals}
    confirmed_map = {build_signal_key(x): x for x in confirmed_signals}

    baseline_keys = set(baseline_map.keys())
    confirmed_keys = set(confirmed_map.keys())

    unexpected_confirmed_only_keys = sorted(list(confirmed_keys - baseline_keys))
    rejected_keys = sorted(list(baseline_keys - confirmed_keys))
    rejected_signals = [baseline_map[k] for k in rejected_keys]

    comparison_invalid = len(unexpected_confirmed_only_keys) > 0

    return {
        "comparison_invalid": comparison_invalid,
        "baseline_signal_keys_count": len(baseline_keys),
        "confirmed_signal_keys_count": len(confirmed_keys),
        "unexpected_confirmed_only_keys": [list(k) for k in unexpected_confirmed_only_keys],
        "unexpected_confirmed_only_signals": [confirmed_map[k] for k in unexpected_confirmed_only_keys[:20]],
        "rejected_keys_count": len(rejected_keys),
        "delta_confirmed_minus_baseline": compute_delta(baseline_summary, confirmed_summary),
        "rejection_report": build_rejection_report(rejected_signals),
    }


def main() -> None:
    args = parse_args()

    sample_stock_pairs = load_sample_stocks(
        sample_size=args.sample_size,
        seed=args.seed,
        max_stocks=args.max_stocks,
    )
    sample_stocks = [code for code, _ in sample_stock_pairs]

    meta = Meta(
        strategy="v3.1_ab_compare",
        seed=args.seed,
        sample_size=args.sample_size,
        max_stocks=args.max_stocks,
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
        sample_stocks=sample_stocks,
        generated_at=date_now_iso(),
    )

    print(f"sample stocks: {len(sample_stock_pairs)}", flush=True)

    baseline_stock_results = run_scenario_for_stock_list(
        stock_pairs=sample_stock_pairs,
        scenario=SCENARIO_BASELINE,
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
    )
    confirmed_stock_results = run_scenario_for_stock_list(
        stock_pairs=sample_stock_pairs,
        scenario=SCENARIO_CONFIRMED,
        start_year=args.start_year,
        hold_weeks=args.hold_weeks,
    )

    baseline_signals = flatten_buy_points(baseline_stock_results, "baseline")
    confirmed_signals = flatten_buy_points(confirmed_stock_results, "confirmed")

    baseline_summary = compute_summary(baseline_signals, baseline_stock_results)
    confirmed_summary = compute_summary(confirmed_signals, confirmed_stock_results)

    comparison = compare_scenarios(
        baseline_signals=baseline_signals,
        confirmed_signals=confirmed_signals,
        baseline_summary=baseline_summary,
        confirmed_summary=confirmed_summary,
    )

    result = {
        "meta": asdict(meta),
        "scenarios": {
            "baseline": {
                "config": deepcopy(SCENARIO_BASELINE),
                "summary": baseline_summary,
                "detailed": baseline_signals,
                "stock_level_results": baseline_stock_results,
            },
            "confirmed": {
                "config": deepcopy(SCENARIO_CONFIRMED),
                "summary": confirmed_summary,
                "detailed": confirmed_signals,
                "stock_level_results": confirmed_stock_results,
            },
        },
        "comparison": comparison,
    }

    output_path = PROJECT_ROOT / args.output
    ensure_parent_dir(output_path)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"saved: {output_path}", flush=True)
    print(
        json.dumps(
            {
                "baseline_summary": baseline_summary,
                "confirmed_summary": confirmed_summary,
                "comparison_invalid": comparison["comparison_invalid"],
                "unexpected_confirmed_only_keys_count": len(comparison["unexpected_confirmed_only_keys"]),
                "rejected_keys_count": comparison["rejected_keys_count"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
