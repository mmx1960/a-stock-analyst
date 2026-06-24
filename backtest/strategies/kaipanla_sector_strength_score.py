from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from app.core.storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class SectorStrengthWeights:
    appearance: float = 8.0
    rank: float = 10.0
    recency: float = 3.0
    stock_match: float = 12.0
    sector_stock_count: float = 2.0
    limit_up_days: float = 3.0
    market_heat: float = 1.0
    plate_strength: float = 0.0009
    capital_strength: float = 0.08
    turnover: float = 0.000000000003
    plate_limit_up: float = 2.0
    plate_consecutive: float = 2.0


DEFAULT_WEIGHTS = SectorStrengthWeights()

INVALID_SECTOR_NAMES = {
    "",
    "未知板块",
    "local_daily_kline_limit_up",
    "杰哥龙头低吸",
    "D神趋势回踩",
}
SECTOR_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("机器人", "机器人概念", "人形机器人", "物理AI", "工业机器人", "机器视觉"),
    ("半导体", "芯片", "芯片概念", "集成电路", "MCU芯片", "存储芯片", "汽车芯片", "第三代半导体", "半导体设备", "HBM"),
    ("算力", "算力概念", "算力租赁", "东数西算", "东数西算(算力)", "数据中心", "数据中心(AIDC)", "AIDC", "服务器"),
    ("AI应用", "AI智能体", "AI视频", "AI语料", "AI眼镜", "AI 眼镜", "AI手机", "多模态AI", "智谱AI", "中国AI 50", "AI医疗", "AI审核", "AI应用分发"),
    ("通信", "通信设备", "通信服务", "光通信", "5G", "CPO", "6G"),
    ("元器件", "电子元器件", "电子元件", "元件", "PCB", "印制电路板", "消费电子"),
    ("商业航天", "航天航空", "航空装备", "卫星导航"),
    ("有色金属", "有色", "小金属", "工业金属", "金属新材", "稀土永磁"),
    ("并购重组", "资产重组"),
    ("医药", "中药", "中药Ⅱ", "化学制药", "生物制品", "医疗器械", "医疗服务", "创新药", "减肥药"),
    ("非金属材料", "非金属材", "装修建材", "水泥"),
)
_SECTOR_ALIAS_LOOKUP: dict[str, set[str]] = {
    alias: set(group)
    for group in SECTOR_ALIAS_GROUPS
    for alias in group
}
LOW_CONFIDENCE_SECTOR_SOURCES: set[str] = set()
FALLBACK_SOURCE_PRIORITY = (
    {"kaipanla_sector_constituents"},
    {"kaipanla_limit_up_history"},
    {"ths_concept_page"},
    {"akshare_em_industry", "akshare_em_concept"},
    {"cninfo_"},
)

EMPTY_SECTOR_STRENGTH_SCORE: dict[str, Any] = {
    "kaipanla_strength_score": 0.0,
    "kaipanla_strength_grade": "NO_DATA",
    "kaipanla_lookback_days": 0,
    "kaipanla_available_trade_days": 0,
    "kaipanla_candidate_sectors": [],
    "kaipanla_sector_appearances": 0,
    "kaipanla_best_sector_rank": None,
    "kaipanla_avg_sector_rank": None,
    "kaipanla_stock_match_days": 0,
    "kaipanla_max_limit_up_days": 0,
    "kaipanla_market_heat_score": 0.0,
    "kaipanla_market_heat_days": 0,
    "kaipanla_score_breakdown": {"reason": "sector_strength_scoring_disabled"},
}


def _normalize_code(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _normalize_date(value: Any) -> str:
    return str(pd.to_datetime(value).date())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalize_sector_name(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    for token in ("（", "("):
        if token in text:
            text = text.split(token, 1)[0].strip()
    return text


def expand_sector_aliases(sectors: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    expanded: set[str] = set()
    for sector in sectors:
        text = normalize_sector_name(sector)
        if not text or text in INVALID_SECTOR_NAMES:
            continue
        expanded.add(text)
        if text.endswith("概念"):
            expanded.add(text.removesuffix("概念"))
        else:
            expanded.add(f"{text}概念")
        for suffix in ("板块", "行业"):
            if text.endswith(suffix):
                expanded.add(text.removesuffix(suffix))
        expanded.update(_SECTOR_ALIAS_LOOKUP.get(text, set()))
    return {item for item in expanded if item and item not in INVALID_SECTOR_NAMES}


def _sector_match_mask(values: Any, candidate_sectors: set[str]) -> pd.Series:
    expanded = expand_sector_aliases(candidate_sectors)
    return values.astype(str).map(normalize_sector_name).isin(expanded)


def load_kaipanla_plate_strength_window(
    *,
    buy_date: str,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    """Load Kaipanla plate interval strength including buy date and previous trade days."""
    store = store or DuckDBStore()
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=max((lookback_trade_days + 1) * 3, 24))
    frame = store.get_kaipanla_sector_strength(_normalize_date(start_ts), _normalize_date(buy_ts))
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    trade_dates = pd.Series(frame["trade_date"]).drop_duplicates().sort_values(ascending=False).head(lookback_trade_days + 1)
    recent = frame[frame["trade_date"].isin(trade_dates)].copy()
    return recent.sort_index(ascending=True)


def score_plate_strength_and_capital(
    *,
    plate_window: pd.DataFrame,
    candidate_sectors: set[str],
    lookback_trade_days: int = 10,
    weights: SectorStrengthWeights = DEFAULT_WEIGHTS,
) -> dict[str, Any] | None:
    if plate_window is None or plate_window.empty or not candidate_sectors:
        return None
    matched = plate_window[_sector_match_mask(plate_window["sector_name"], candidate_sectors)].copy()
    if matched.empty:
        return None

    trade_dates = sorted(matched["trade_date"].drop_duplicates(), reverse=True)
    date_position = {str(pd.to_datetime(date).date()): idx for idx, date in enumerate(trade_dates)}
    total_days = max(1, min(lookback_trade_days + 1, len(plate_window["trade_date"].drop_duplicates())))

    daily_rows: list[dict[str, Any]] = []
    total_weight = 0.0
    weighted_strength = 0.0
    weighted_capital = 0.0
    weighted_turnover = 0.0
    weighted_limit_up = 0.0
    best_strength = 0.0
    best_capital = 0.0
    best_turnover = 0.0

    for _, row in matched.iterrows():
        date_key = str(pd.to_datetime(row["trade_date"]).date())
        recency_rank = date_position.get(date_key, total_days)
        recency_weight = max(0.2, (total_days - recency_rank) / total_days)
        strength = max(0.0, _safe_float(row.get("strength_score")))
        capital = max(0.0, _safe_float(row.get("capital_score")))
        turnover = max(0.0, _safe_float(row.get("turnover")))
        limit_up_count = max(0, _safe_int(row.get("limit_up_count")))
        max_consecutive_days = max(0, _safe_int(row.get("max_consecutive_days")))

        total_weight += recency_weight
        weighted_strength += strength * recency_weight
        weighted_capital += capital * recency_weight
        weighted_turnover += turnover * recency_weight
        weighted_limit_up += (limit_up_count * weights.plate_limit_up + max_consecutive_days * weights.plate_consecutive) * recency_weight
        best_strength = max(best_strength, strength)
        best_capital = max(best_capital, capital)
        best_turnover = max(best_turnover, turnover)
        daily_rows.append(
            {
                "trade_date": date_key,
                "sector_code": row.get("sector_code"),
                "sector_name": row.get("sector_name"),
                "strength_score": round(strength, 2),
                "capital_score": round(capital, 2),
                "main_net_inflow": round(_safe_float(row.get("main_net_inflow")), 2),
                "turnover": round(turnover, 2),
                "recency_weight": round(recency_weight, 4),
            }
        )

    avg_strength = weighted_strength / total_weight if total_weight else 0.0
    avg_capital = weighted_capital / total_weight if total_weight else 0.0
    avg_turnover = weighted_turnover / total_weight if total_weight else 0.0
    avg_limit_up = weighted_limit_up / total_weight if total_weight else 0.0
    strength_component = min(45.0, avg_strength * weights.plate_strength)
    capital_component = min(25.0, avg_capital * weights.capital_strength)
    turnover_component = min(12.0, avg_turnover * weights.turnover)
    recency_component = min(8.0, len(trade_dates) / max(1, lookback_trade_days + 1) * 8.0)
    limit_up_component = min(10.0, avg_limit_up)
    raw_score = strength_component + capital_component + turnover_component + recency_component + limit_up_component
    final_score = round(min(100.0, raw_score), 2)
    if final_score >= 75:
        grade = "A"
    elif final_score >= 55:
        grade = "B"
    elif final_score >= 35:
        grade = "C"
    else:
        grade = "D"

    return {
        "kaipanla_strength_score": final_score,
        "kaipanla_strength_grade": grade,
        "kaipanla_sector_appearances": int(len(matched)),
        "kaipanla_available_trade_days": int(plate_window["trade_date"].nunique()),
        "kaipanla_plate_strength_days": int(matched["trade_date"].nunique()),
        "kaipanla_best_plate_strength": round(best_strength, 2),
        "kaipanla_best_capital_score": round(best_capital, 2),
        "kaipanla_best_turnover": round(best_turnover, 2),
        "kaipanla_matched_plate_strength_rows": daily_rows[:20],
        "kaipanla_score_breakdown": {
            "reason": "matched_kaipanla_plate_strength_and_capital",
            "strength_component": round(strength_component, 2),
            "capital_component": round(capital_component, 2),
            "turnover_component": round(turnover_component, 2),
            "recency_component": round(recency_component, 2),
            "limit_up_component": round(limit_up_component, 2),
        },
    }


def load_kaipanla_strength_window(
    *,
    buy_date: str,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    """Load ranked Kaipanla sector rows before the buy date.

    Rank is derived from each day's sector order by stock_count because the cached
    table stores daily limit-up sector groups rather than an explicit strength rank.
    """
    store = store or DuckDBStore()
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=max(lookback_trade_days * 3, 20))
    with store._connect() as con:
        frame = con.execute(
            """
            WITH ranked_sectors AS (
                SELECT
                    trade_date,
                    sector_code,
                    sector_name,
                    stock_count,
                    row_number() OVER (
                        PARTITION BY trade_date
                        ORDER BY stock_count DESC, sector_code
                    ) AS sector_rank
                FROM kaipanla_limit_up_sectors
                WHERE trade_date < ? AND trade_date >= ?
            ), recent_trade_dates AS (
                SELECT DISTINCT trade_date
                FROM ranked_sectors
                ORDER BY trade_date DESC
                LIMIT ?
            )
            SELECT rs.*
            FROM ranked_sectors rs
            JOIN recent_trade_dates d USING (trade_date)
            ORDER BY rs.trade_date DESC, rs.sector_rank
            """,
            [_normalize_date(buy_ts), _normalize_date(start_ts), int(lookback_trade_days)],
        ).df()
    return frame


def load_kaipanla_stock_sector_window(
    *,
    code: str,
    buy_date: str,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    store = store or DuckDBStore()
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=max(lookback_trade_days * 3, 20))
    code = _normalize_code(code)
    with store._connect() as con:
        frame = con.execute(
            """
            SELECT
                trade_date,
                sector_code,
                sector_name,
                code,
                name,
                reason,
                theme,
                concept_tags,
                consecutive_days,
                consecutive_count,
                seal_amount,
                first_limit_up_time
            FROM kaipanla_limit_up_stocks
            WHERE code = ? AND trade_date < ? AND trade_date >= ?
            ORDER BY trade_date DESC, consecutive_days DESC, seal_amount DESC
            """,
            [code, _normalize_date(buy_ts), _normalize_date(start_ts)],
        ).df()
    return frame


def load_kaipanla_market_heat_window(
    *,
    buy_date: str,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    store = store or DuckDBStore()
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=max(lookback_trade_days * 3, 20))
    with store._connect() as con:
        frame = con.execute(
            """
            WITH recent_market AS (
                SELECT
                    trade_date,
                    limit_up_count,
                    actual_limit_up_count,
                    first_board_count,
                    second_board_count,
                    third_board_count,
                    fourth_plus_board_count,
                    rise_fall_ratio,
                    row_number() OVER (ORDER BY trade_date DESC) AS recency_rank
                FROM kaipanla_market_sentiment
                WHERE trade_date < ? AND trade_date >= ?
                ORDER BY trade_date DESC
                LIMIT ?
            )
            SELECT *
            FROM recent_market
            ORDER BY trade_date DESC
            """,
            [_normalize_date(buy_ts), _normalize_date(start_ts), int(lookback_trade_days)],
        ).df()
    return frame


def score_market_heat(market_window: pd.DataFrame, weights: SectorStrengthWeights = DEFAULT_WEIGHTS) -> dict[str, Any]:
    if market_window is None or market_window.empty:
        return {
            "kaipanla_market_heat_score": 0.0,
            "kaipanla_market_heat_days": 0,
            "kaipanla_score_breakdown": {"reason": "missing_market_sentiment_history"},
        }

    score = 0.0
    daily_scores = []
    total_days = max(1, len(market_window))
    for _, row in market_window.iterrows():
        recency_rank = max(1, _safe_int(row.get("recency_rank"), total_days))
        recency_weight = (total_days - recency_rank + 1) / total_days
        limit_up_count = max(_safe_int(row.get("actual_limit_up_count")), _safe_int(row.get("limit_up_count")))
        first_board = _safe_int(row.get("first_board_count"))
        second_board = _safe_int(row.get("second_board_count"))
        third_board = _safe_int(row.get("third_board_count"))
        fourth_plus = _safe_int(row.get("fourth_plus_board_count"))
        ladder_score = min(35.0, second_board * 3.0 + third_board * 5.0 + fourth_plus * 8.0)
        daily_score = min(45.0, limit_up_count * 0.45) + min(20.0, first_board * 0.2) + ladder_score
        weighted = daily_score * recency_weight
        score += weighted
        daily_scores.append(round(weighted, 2))

    final_score = round(min(100.0, score / total_days * weights.market_heat), 2)
    return {
        "kaipanla_market_heat_score": final_score,
        "kaipanla_market_heat_days": int(total_days),
        "kaipanla_score_breakdown": {
            "market_heat_score": final_score,
            "market_heat_daily_scores": daily_scores,
        },
    }


def load_stock_sector_membership(
    *,
    code: str,
    store: DuckDBStore | None = None,
) -> pd.DataFrame:
    store = store or DuckDBStore()
    return store.get_stock_sector_membership(_normalize_code(code))


def infer_candidate_sectors(
    stock_window: pd.DataFrame,
    explicit_sector: str | None = None,
    membership: pd.DataFrame | None = None,
) -> set[str]:
    membership_by_priority: list[set[str]] = [set() for _ in FALLBACK_SOURCE_PRIORITY]
    other_membership_sectors: set[str] = set()
    if membership is not None and not membership.empty:
        for _, row in membership.iterrows():
            value = row.get("sector_name")
            text = str(value).strip() if value is not None and not pd.isna(value) else ""
            source = str(row.get("source") or "").strip()
            if not text or text in INVALID_SECTOR_NAMES:
                continue
            if source in LOW_CONFIDENCE_SECTOR_SOURCES:
                continue
            placed = False
            for idx, source_group in enumerate(FALLBACK_SOURCE_PRIORITY):
                if source in source_group or any(source.startswith(prefix) for prefix in source_group if prefix.endswith("_")):
                    membership_by_priority[idx].add(normalize_sector_name(text))
                    placed = True
                    break
            if not placed:
                other_membership_sectors.add(normalize_sector_name(text))

    if membership_by_priority and membership_by_priority[0]:
        return membership_by_priority[0]

    kaipanla_sectors: set[str] = set()
    if stock_window is not None and not stock_window.empty:
        for _, row in stock_window.iterrows():
            for key in ("sector_name", "theme"):
                value = row.get(key)
                text = str(value).strip() if value is not None and not pd.isna(value) else ""
                if text and text not in INVALID_SECTOR_NAMES:
                    kaipanla_sectors.add(normalize_sector_name(text))
    if kaipanla_sectors:
        return kaipanla_sectors

    for sector_group in membership_by_priority[1:]:
        if sector_group:
            return sector_group
    if other_membership_sectors:
        return other_membership_sectors
    explicit = str(explicit_sector).strip() if explicit_sector else ""
    if explicit and explicit not in INVALID_SECTOR_NAMES:
        return {normalize_sector_name(explicit)}
    return set()


def score_sector_strength(
    *,
    code: str,
    buy_date: str,
    sector_name: str | None = None,
    lookback_trade_days: int = 10,
    weights: SectorStrengthWeights = DEFAULT_WEIGHTS,
    store: DuckDBStore | None = None,
) -> dict[str, Any]:
    store = store or DuckDBStore()
    code = _normalize_code(code)
    stock_window = load_kaipanla_stock_sector_window(
        code=code,
        buy_date=buy_date,
        lookback_trade_days=lookback_trade_days,
        store=store,
    )
    membership = load_stock_sector_membership(code=code, store=store)
    market_heat = score_market_heat(
        load_kaipanla_market_heat_window(
            buy_date=buy_date,
            lookback_trade_days=lookback_trade_days,
            store=store,
        ),
        weights=weights,
    )
    candidate_sectors = infer_candidate_sectors(stock_window, sector_name, membership)
    plate_window = load_kaipanla_plate_strength_window(
        buy_date=buy_date,
        lookback_trade_days=lookback_trade_days,
        store=store,
    )
    plate_score = score_plate_strength_and_capital(
        plate_window=plate_window,
        candidate_sectors=candidate_sectors,
        lookback_trade_days=lookback_trade_days,
        weights=weights,
    )
    if plate_score is not None:
        return {
            **plate_score,
            "kaipanla_lookback_days": int(lookback_trade_days),
            "kaipanla_candidate_sectors": sorted(candidate_sectors),
            "stock_sector_membership_count": int(len(membership)) if membership is not None else 0,
            "kaipanla_best_sector_rank": None,
            "kaipanla_avg_sector_rank": None,
            "kaipanla_stock_match_days": int(stock_window["trade_date"].nunique()) if not stock_window.empty else 0,
            "kaipanla_max_limit_up_days": int(stock_window["consecutive_days"].max()) if not stock_window.empty else 0,
            "kaipanla_market_heat_score": float(market_heat.get("kaipanla_market_heat_score") or 0),
            "kaipanla_market_heat_days": int(market_heat.get("kaipanla_market_heat_days") or 0),
        }

    ranked = load_kaipanla_strength_window(
        buy_date=buy_date,
        lookback_trade_days=lookback_trade_days,
        store=store,
    )
    available_trade_days = int(ranked["trade_date"].nunique()) if not ranked.empty else 0
    if not candidate_sectors or ranked.empty:
        market_heat_score = float(market_heat.get("kaipanla_market_heat_score") or 0)
        grade = "MARKET_ONLY" if market_heat_score > 0 else "NO_DATA"
        return {
            "kaipanla_strength_score": market_heat_score,
            "kaipanla_strength_grade": grade,
            "kaipanla_lookback_days": int(lookback_trade_days),
            "kaipanla_available_trade_days": available_trade_days,
            "kaipanla_candidate_sectors": sorted(candidate_sectors),
            "stock_sector_membership_count": int(len(membership)) if membership is not None else 0,
            "kaipanla_sector_appearances": 0,
            "kaipanla_best_sector_rank": None,
            "kaipanla_avg_sector_rank": None,
            "kaipanla_stock_match_days": int(stock_window["trade_date"].nunique()) if not stock_window.empty else 0,
            "kaipanla_max_limit_up_days": int(stock_window["consecutive_days"].max()) if not stock_window.empty else 0,
            "kaipanla_market_heat_score": market_heat_score,
            "kaipanla_market_heat_days": int(market_heat.get("kaipanla_market_heat_days") or 0),
            "kaipanla_score_breakdown": {
                "reason": "missing_candidate_sector_or_strength_history",
                **market_heat.get("kaipanla_score_breakdown", {}),
            },
        }

    matched = ranked[_sector_match_mask(ranked["sector_name"], candidate_sectors)].copy()
    if not matched.empty:
        matched["sector_rank"] = pd.Series(pd.to_numeric(matched["sector_rank"], errors="coerce"), index=matched.index)
        matched["stock_count"] = pd.Series(pd.to_numeric(matched["stock_count"], errors="coerce"), index=matched.index).fillna(0)
        matched = matched[(matched["sector_rank"] <= 30) & (matched["stock_count"] >= 2)].copy()
    appearances = int(len(matched))
    best_rank = int(matched["sector_rank"].min()) if appearances else None
    avg_rank = float(matched["sector_rank"].mean()) if appearances else None

    trade_dates = sorted(ranked["trade_date"].drop_duplicates(), reverse=True)
    date_position = {str(pd.to_datetime(date).date()): idx for idx, date in enumerate(trade_dates)}
    appearance_score = min(40.0, appearances * weights.appearance)
    rank_score = 0.0
    recency_score = 0.0
    sector_stock_count_score = 0.0
    for _, row in matched.iterrows():
        rank = max(1, _safe_int(row.get("sector_rank"), 99))
        rank_score += weights.rank / math.sqrt(rank)
        date_key = str(pd.to_datetime(row["trade_date"]).date())
        recency_score += max(0.0, weights.recency * (lookback_trade_days - date_position.get(date_key, lookback_trade_days)) / lookback_trade_days)
        sector_stock_count_score += min(4.0, math.log1p(_safe_float(row.get("stock_count"))) * weights.sector_stock_count)

    stock_match_days = int(stock_window["trade_date"].nunique()) if not stock_window.empty else 0
    max_limit_up_days = int(stock_window["consecutive_days"].max()) if not stock_window.empty else 0
    stock_match_score = min(24.0, stock_match_days * weights.stock_match)
    limit_up_days_score = min(12.0, max_limit_up_days * weights.limit_up_days)
    market_heat_score = float(market_heat.get("kaipanla_market_heat_score") or 0)
    raw_score = appearance_score + rank_score + recency_score + sector_stock_count_score + stock_match_score + limit_up_days_score + market_heat_score * 0.25
    final_score = round(min(45.0, raw_score), 2)

    if final_score >= 75:
        grade = "A"
    elif final_score >= 55:
        grade = "B"
    elif final_score >= 35:
        grade = "C"
    elif final_score > 0:
        grade = "D"
    else:
        grade = "NO_MATCH"

    return {
        "kaipanla_strength_score": final_score,
        "kaipanla_strength_grade": grade,
        "kaipanla_lookback_days": int(lookback_trade_days),
        "kaipanla_available_trade_days": available_trade_days,
        "kaipanla_candidate_sectors": sorted(candidate_sectors),
        "stock_sector_membership_count": int(len(membership)) if membership is not None else 0,
        "kaipanla_sector_appearances": appearances,
        "kaipanla_best_sector_rank": best_rank,
        "kaipanla_avg_sector_rank": round(avg_rank, 2) if avg_rank is not None else None,
        "kaipanla_stock_match_days": stock_match_days,
        "kaipanla_max_limit_up_days": max_limit_up_days,
        "kaipanla_market_heat_score": market_heat_score,
        "kaipanla_market_heat_days": int(market_heat.get("kaipanla_market_heat_days") or 0),
        "kaipanla_score_breakdown": {
            "reason": "legacy_limit_up_rank_fallback_capped_without_plate_strength",
            "raw_score_before_cap": round(raw_score, 2),
            "legacy_score_cap": 45.0,
            "appearance_score": round(appearance_score, 2),
            "rank_score": round(rank_score, 2),
            "recency_score": round(recency_score, 2),
            "sector_stock_count_score": round(sector_stock_count_score, 2),
            "stock_match_score": round(stock_match_score, 2),
            "limit_up_days_score": round(limit_up_days_score, 2),
            "market_heat_score": market_heat_score,
        },
    }


def score_sector_strength_safe(
    *,
    code: str,
    buy_date: str,
    sector_name: str | None = None,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> dict[str, Any]:
    """Score sector strength without letting cache/query failures break a backtest."""
    try:
        return score_sector_strength(
            code=code,
            buy_date=buy_date,
            sector_name=sector_name,
            lookback_trade_days=lookback_trade_days,
            store=store,
        )
    except Exception as exc:
        fallback = dict(EMPTY_SECTOR_STRENGTH_SCORE)
        fallback["kaipanla_lookback_days"] = int(lookback_trade_days)
        fallback["kaipanla_strength_grade"] = "ERROR"
        fallback["kaipanla_score_breakdown"] = {
            "reason": "sector_strength_scoring_error",
            "error": str(exc),
        }
        return fallback


def enrich_buy_points_with_sector_strength(
    report: dict[str, Any],
    *,
    lookback_trade_days: int = 10,
    store: DuckDBStore | None = None,
) -> list[dict[str, Any]]:
    store = store or DuckDBStore()
    rows: list[dict[str, Any]] = []
    for stock in report.get("detailed", []):
        code = _normalize_code(stock.get("code"))
        name = stock.get("name")
        for point in stock.get("buy_points", []):
            buy_date = point.get("buy_date")
            if not buy_date:
                continue
            score = score_sector_strength(
                code=code,
                buy_date=buy_date,
                lookback_trade_days=lookback_trade_days,
                store=store,
            )
            row = {
                "code": code,
                "name": name,
                **point,
                **score,
            }
            rows.append(row)
    return rows


def summarize_strength_buckets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"bucket_summary": [], "recommended_filters": []}
    frame = pd.DataFrame(rows)
    if "max_return" not in frame.columns:
        return {"bucket_summary": [], "recommended_filters": []}
    if "status" not in frame.columns:
        frame["status"] = "已实现"
    frame["max_return"] = pd.to_numeric(frame["max_return"], errors="coerce")
    frame["score"] = pd.to_numeric(frame.get("kaipanla_strength_score", 0), errors="coerce").fillna(0)
    bins = [-0.01, 0, 35, 55, 75, 100]
    labels = ["NO_MATCH", "D_1_34", "C_35_54", "B_55_74", "A_75_100"]
    frame["score_bucket"] = pd.cut(frame["score"], bins=bins, labels=labels, include_lowest=True)
    bucket_rows = []
    for bucket, group in frame.groupby("score_bucket", observed=False):
        realized = group[group["status"] == "已实现"].copy() if "status" in group.columns else group.copy()
        returns = realized["max_return"].dropna()
        if returns.empty:
            bucket_rows.append({"bucket": str(bucket), "count": int(len(group)), "realized_count": 0})
            continue
        bucket_rows.append({
            "bucket": str(bucket),
            "count": int(len(group)),
            "realized_count": int(len(returns)),
            "avg_return": round(float(returns.mean()), 2),
            "median_return": round(float(returns.median()), 2),
            "win_rate_10pct": round(float((returns > 10).mean() * 100), 2),
            "loss_rate": round(float((returns < 0).mean() * 100), 2),
            "max_return": round(float(returns.max()), 2),
        })

    recommendations = []
    for threshold in (35, 55, 75):
        selected = frame[frame["score"] >= threshold]
        returns = selected[selected["status"] == "已实现"]["max_return"].dropna() if "status" in selected.columns else selected["max_return"].dropna()
        if returns.empty:
            continue
        recommendations.append({
            "min_score": threshold,
            "signal_count": int(len(selected)),
            "avg_return": round(float(returns.mean()), 2),
            "median_return": round(float(returns.median()), 2),
            "win_rate_10pct": round(float((returns > 10).mean() * 100), 2),
        })
    return {
        "bucket_summary": bucket_rows,
        "recommended_filters": recommendations,
    }
