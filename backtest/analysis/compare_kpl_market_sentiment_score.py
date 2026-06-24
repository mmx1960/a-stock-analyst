from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

DB_PATH = Path("data/ashare.duckdb")
BACKTEST_PATH = Path("backtest/results_v6/attack_third_buy_full_universe_2024.checkpoint.json")
OUT_DIR = Path("backtest/results_v6/kpl_market_sentiment_compare")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _flatten_signals(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text())
    rows: list[dict[str, Any]] = []
    for stock in payload.get("detailed", []):
        for signal in stock.get("buy_points", []):
            rows.append(
                {
                    "code": str(stock.get("code", "")).zfill(6),
                    "name": stock.get("name", ""),
                    **signal,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["buy_date"] = pd.to_datetime(frame["buy_date"]).dt.date
    return frame


def _score_market(row: pd.Series) -> tuple[float, str, dict[str, float]]:
    limit_up_avg = _safe_float(row.get("limit_up_count_avg"))
    actual_limit_up_avg = _safe_float(row.get("actual_limit_up_count_avg"))
    first_board_avg = _safe_float(row.get("first_board_count_avg"))
    high_board_avg = _safe_float(row.get("high_board_count_avg"))
    consecutive_rate_avg = _safe_float(row.get("consecutive_board_rate_avg"))
    sharp_withdrawal_avg = _safe_float(row.get("sharp_withdrawal_count_avg"))
    up_down_ratio = _safe_float(row.get("up_down_ratio_avg"))

    breakdown = {
        "limit_up_heat": min(22.0, limit_up_avg / 90.0 * 22.0),
        "actual_limit_up_heat": min(18.0, actual_limit_up_avg / 75.0 * 18.0),
        "first_board_heat": min(12.0, first_board_avg / 60.0 * 12.0),
        "high_board_heat": min(18.0, high_board_avg / 12.0 * 18.0),
        "consecutive_rate": min(14.0, consecutive_rate_avg / 20.0 * 14.0),
        "breadth": min(10.0, up_down_ratio / 1.4 * 10.0),
        "withdrawal_penalty": -min(16.0, sharp_withdrawal_avg / 25.0 * 16.0),
    }
    score = round(max(0.0, min(100.0, sum(breakdown.values()))), 2)
    if score >= 55:
        grade = "A"
    elif score >= 45:
        grade = "B"
    elif score >= 35:
        grade = "C"
    else:
        grade = "D"
    return score, grade, breakdown


def _load_market_features(signals: pd.DataFrame, lookback_trade_days: int = 10) -> pd.DataFrame:
    min_date = signals["buy_date"].min()
    max_date = signals["buy_date"].max()
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        market = con.execute(
            """
            SELECT
                trade_date,
                up_count,
                down_count,
                limit_up_count,
                actual_limit_up_count,
                first_board_count,
                second_board_count,
                third_board_count,
                fourth_plus_board_count,
                consecutive_board_rate,
                sharp_withdrawal_count
            FROM kaipanla_market_sentiment
            WHERE trade_date <= ? AND trade_date >= ?
            ORDER BY trade_date
            """,
            [str(max_date), str(min_date - pd.Timedelta(days=60))],
        ).df()
    market["trade_date"] = pd.to_datetime(market["trade_date"]).dt.date
    market["high_board_count"] = market["second_board_count"] + market["third_board_count"] + market["fourth_plus_board_count"]
    market["up_down_ratio"] = market["up_count"] / market["down_count"].clip(lower=1)

    rows: list[dict[str, Any]] = []
    for buy_date in sorted(signals["buy_date"].unique()):
        window = market[market["trade_date"] < buy_date].tail(lookback_trade_days)
        item: dict[str, Any] = {"buy_date": buy_date, "kpl_market_days": int(len(window))}
        for column in [
            "limit_up_count",
            "actual_limit_up_count",
            "first_board_count",
            "high_board_count",
            "consecutive_board_rate",
            "sharp_withdrawal_count",
            "up_down_ratio",
        ]:
            item[f"{column}_avg"] = round(float(window[column].mean()), 4) if not window.empty else 0.0
        score, grade, breakdown = _score_market(pd.Series(item))
        item["kpl_market_score"] = score
        item["kpl_market_grade"] = grade
        item["kpl_market_score_breakdown"] = json.dumps(breakdown, ensure_ascii=False)
        rows.append(item)
    return pd.DataFrame(rows)


def _bucket_summary(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    data = frame.copy()
    data["score_bucket"] = pd.cut(
        data[score_column],
        bins=[-0.01, 35, 45, 55, 100],
        labels=["D_<35", "C_35_44", "B_45_54", "A_55+"],
        include_lowest=True,
    )
    return (
        data.groupby("score_bucket", observed=False)
        .agg(
            signals=("code", "count"),
            realized=("status", lambda s: int((s == "已实现").sum())),
            avg_return=("max_return", "mean"),
            median_return=("max_return", "median"),
            win_rate=("max_return", lambda s: float((s > 0).mean())),
            p75_return=("max_return", lambda s: float(s.quantile(0.75))),
            avg_days=("days", "mean"),
        )
        .round(4)
        .reset_index()
    )


def _threshold_summary(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    rows = []
    baseline_avg = float(frame["max_return"].mean())
    baseline_win = float((frame["max_return"] > 0).mean())
    for threshold in range(30, 66, 5):
        selected = frame[frame[score_column] >= threshold]
        if selected.empty:
            continue
        rows.append(
            {
                "threshold": threshold,
                "signals": len(selected),
                "coverage": len(selected) / len(frame),
                "avg_return": selected["max_return"].mean(),
                "median_return": selected["max_return"].median(),
                "win_rate": (selected["max_return"] > 0).mean(),
                "avg_lift": selected["max_return"].mean() - baseline_avg,
                "win_lift": (selected["max_return"] > 0).mean() - baseline_win,
            }
        )
    return pd.DataFrame(rows).round(4)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    signals = _flatten_signals(BACKTEST_PATH)
    features = _load_market_features(signals)
    merged = signals.merge(features, on="buy_date", how="left")
    merged.to_csv(OUT_DIR / "merged_signals.csv", index=False)

    bucket = _bucket_summary(merged, "kpl_market_score")
    threshold = _threshold_summary(merged, "kpl_market_score")
    yearly = (
        merged.groupby(["buy_year", "kpl_market_grade"], observed=False)
        .agg(signals=("code", "count"), avg_return=("max_return", "mean"), median_return=("max_return", "median"), win_rate=("max_return", lambda s: float((s > 0).mean())))
        .round(4)
        .reset_index()
    )

    bucket.to_csv(OUT_DIR / "bucket_summary.csv", index=False)
    threshold.to_csv(OUT_DIR / "threshold_summary.csv", index=False)
    yearly.to_csv(OUT_DIR / "yearly_grade_summary.csv", index=False)

    print("merged", len(merged), "->", OUT_DIR / "merged_signals.csv")
    print("\nBUCKET")
    print(bucket.to_string(index=False))
    print("\nTHRESHOLD")
    print(threshold.to_string(index=False))
    print("\nYEARLY_GRADE")
    print(yearly.to_string(index=False))


if __name__ == "__main__":
    main()
