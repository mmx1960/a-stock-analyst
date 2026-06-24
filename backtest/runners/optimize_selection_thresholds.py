from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.optimization.threshold_optimizer import (
    ThresholdCandidate,
    default_threshold_candidates,
    evaluate_threshold_candidates,
)

DEFAULT_OUTPUT = Path("backtest/results_v6/selection_threshold_optimization.json")


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _parse_float_list(value: str, defaults: list[float]) -> list[float]:
    text = str(value or "").strip()
    if not text:
        return defaults
    return [float(part.strip()) for part in text.split(",") if part.strip()]


def build_candidates(args: argparse.Namespace) -> list[ThresholdCandidate]:
    sectors = _parse_float_list(args.sector_scores, [45.0, 55.0, 65.0, 75.0])
    finals = _parse_float_list(args.final_scores, [40.0, 45.0, 50.0, 55.0])
    if not sectors and not finals:
        return default_threshold_candidates()
    return [ThresholdCandidate(min_sector_score=sector, min_final_score=final) for sector in sectors for final in finals]


def load_trades(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = payload.get("trades", [])
    if not isinstance(trades, list):
        raise ValueError("input JSON field 'trades' must be a list")
    return trades


def optimize_from_file(args: argparse.Namespace) -> dict[str, Any]:
    trades = load_trades(args.input)
    candidates = build_candidates(args)
    result = evaluate_threshold_candidates(trades, candidates=candidates, min_samples=args.min_samples)
    return {
        "optimizer": "selection-threshold-optimization-runner-v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": args.input,
        "trade_count": len(trades),
        "sector_scores": [candidate.min_sector_score for candidate in candidates],
        "final_scores": [candidate.min_final_score for candidate in candidates],
        **result,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize selection workflow threshold candidates from selection backtest JSON")
    parser.add_argument("--input", required=True, help="selection backtest JSON path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sector-scores", default="45,55,65,75")
    parser.add_argument("--final-scores", default="40,45,50,55")
    parser.add_argument("--min-samples", type=int, default=5)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> Path:
    result = optimize_from_file(args)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(output_path)
    print(json.dumps({"trade_count": result["trade_count"], "recommendations": result["recommendations"]}, ensure_ascii=False, indent=2, default=_json_default))
    return output_path


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))


if __name__ == "__main__":
    main()
