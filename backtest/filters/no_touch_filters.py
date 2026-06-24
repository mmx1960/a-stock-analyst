from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from app.core.sector_priority import is_tdx_industry_type
from app.core.storage.duckdb_store import DuckDBStore

_MEMBERSHIP_CACHE: dict[str, set[str]] = {}
_TOP3_CACHE: dict[tuple[str, int], pd.DataFrame] = {}

NO_TOUCH_LOOKBACK_TRADE_DAYS = 10
ONE_WORD_BOARD_LOOKBACK_DAYS = NO_TOUCH_LOOKBACK_TRADE_DAYS
MIN_ONE_WORD_BOARD_COUNT = 1
ONE_WORD_RANGE_PCT = 0.003
LIMIT_UP_CHANGE_PCT = 9.5
TOP_BEARISH_LOOKBACK_DAYS = NO_TOUCH_LOOKBACK_TRADE_DAYS
TOP_BEARISH_NEAR_HIGH_RATIO = 0.95
TOP_BEARISH_MIN_TURNOVER_RATE = 40.0
TOP_BEARISH_VOLUME_RATIO = 2.0
HIGH_TURNOVER_LOOKBACK_DAYS = NO_TOUCH_LOOKBACK_TRADE_DAYS
HIGH_TURNOVER_MIN_RATE = 40.0
SECTOR_TOP3_LOOKBACK_TRADE_DAYS = 10


def _normalize_date(value: Any) -> str:
    return str(pd.to_datetime(value).date())


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _load_daily_window(code: str, buy_date: str, *, store: DuckDBStore) -> pd.DataFrame:
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=140)
    with store._connect() as con:
        return con.execute(
            """
            select trade_date, open, high, low, close, volume, amount, turnover_rate, change_pct
            from daily_kline
            where code=? and adjust='hfq' and trade_date < ? and trade_date >= ?
            order by trade_date
            """,
            [str(code).zfill(6), _normalize_date(buy_ts), _normalize_date(start_ts)],
        ).df()


def _normalize_daily(daily: pd.DataFrame | None, buy_date: str | None = None) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    frame = daily.copy()
    if "trade_date" not in frame.columns and "date" in frame.columns:
        frame["trade_date"] = frame["date"]
    if "trade_date" not in frame.columns:
        return pd.DataFrame()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if buy_date:
        frame = frame[frame["trade_date"] < pd.to_datetime(buy_date)]
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover_rate", "change_pct"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["trade_date", "open", "high", "low", "close"]).sort_values("trade_date").reset_index(drop=True)


def check_multiple_one_word_boards(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    recent = daily.tail(ONE_WORD_BOARD_LOOKBACK_DAYS).copy()
    if recent.empty:
        return True, {"no_touch_one_word_board_count": 0, "no_touch_one_word_lookback_trade_days": ONE_WORD_BOARD_LOOKBACK_DAYS}
    price_range_pct = (recent["high"] - recent["low"]) / recent["close"].replace(0, pd.NA)
    if "change_pct" in recent.columns:
        change_pct = recent["change_pct"].fillna(0)
    else:
        change_pct = recent["close"].pct_change().fillna(0) * 100
    one_word = (price_range_pct <= ONE_WORD_RANGE_PCT) & (change_pct >= LIMIT_UP_CHANGE_PCT)
    count = int(one_word.sum())
    return count < MIN_ONE_WORD_BOARD_COUNT, {
        "no_touch_one_word_board_count": count,
        "no_touch_one_word_board_dates": [str(pd.to_datetime(v).date()) for v in recent.loc[one_word, "trade_date"].tail(5).tolist()],
        "no_touch_one_word_lookback_trade_days": ONE_WORD_BOARD_LOOKBACK_DAYS,
    }


def check_top_bearish_high_turnover(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    recent = daily.tail(TOP_BEARISH_LOOKBACK_DAYS).copy()
    if recent.empty:
        return True, {"no_touch_top_bearish_found": False, "no_touch_top_bearish_lookback_trade_days": TOP_BEARISH_LOOKBACK_DAYS}
    high_ref = _safe_float(daily["high"].tail(60).max()) if "high" in daily.columns else 0.0
    avg_volume = recent["volume"].rolling(20, min_periods=5).mean()
    volume_ratio = recent["volume"] / avg_volume.replace(0, pd.NA)
    bearish = recent["close"] < recent["open"]
    near_top = recent["high"] >= high_ref * TOP_BEARISH_NEAR_HIGH_RATIO if high_ref > 0 else False
    turnover = recent["turnover_rate"].fillna(0) if "turnover_rate" in recent.columns else pd.Series([0] * len(recent), index=recent.index)
    hit = bearish & near_top & (turnover >= TOP_BEARISH_MIN_TURNOVER_RATE) & (volume_ratio >= TOP_BEARISH_VOLUME_RATIO)
    if bool(hit.any()):
        row = recent.loc[hit].iloc[-1]
        return False, {
            "no_touch_top_bearish_found": True,
            "no_touch_top_bearish_date": str(pd.to_datetime(row["trade_date"]).date()),
            "no_touch_top_bearish_turnover_rate": round(_safe_float(row.get("turnover_rate")), 4),
            "no_touch_top_bearish_volume_ratio": round(_safe_float(volume_ratio.loc[row.name]), 4),
            "no_touch_top_bearish_lookback_trade_days": TOP_BEARISH_LOOKBACK_DAYS,
        }
    return True, {"no_touch_top_bearish_found": False, "no_touch_top_bearish_lookback_trade_days": TOP_BEARISH_LOOKBACK_DAYS}


def check_high_turnover_before_buy(daily: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """Reject any stock with giant turnover before the buy date.

    This is the global user-level hard rule: 交易日前有巨量换手的股票不碰。
    It is intentionally stricter than the older "top bearish distribution" rule,
    which required a bearish candle near a recent high. If turnover_rate is not
    available, this filter cannot prove a violation and therefore passes while
    exposing that the data was missing in metadata.
    """
    recent = daily.tail(HIGH_TURNOVER_LOOKBACK_DAYS).copy()
    if recent.empty:
        return True, {
            "no_touch_high_turnover_found": False,
            "no_touch_high_turnover_lookback_trade_days": HIGH_TURNOVER_LOOKBACK_DAYS,
            "no_touch_high_turnover_min_rate": HIGH_TURNOVER_MIN_RATE,
            "no_touch_high_turnover_data_available": "turnover_rate" in daily.columns,
        }
    if "turnover_rate" not in recent.columns:
        return True, {
            "no_touch_high_turnover_found": False,
            "no_touch_high_turnover_reason": "turnover_rate_missing",
            "no_touch_high_turnover_lookback_trade_days": HIGH_TURNOVER_LOOKBACK_DAYS,
            "no_touch_high_turnover_min_rate": HIGH_TURNOVER_MIN_RATE,
            "no_touch_high_turnover_data_available": False,
        }
    turnover = pd.Series(pd.to_numeric(recent["turnover_rate"], errors="coerce"), index=recent.index).fillna(0)
    hit = turnover >= HIGH_TURNOVER_MIN_RATE
    if bool(hit.any()):
        row = recent.loc[hit].iloc[-1]
        return False, {
            "no_touch_high_turnover_found": True,
            "no_touch_high_turnover_date": str(pd.to_datetime(row["trade_date"]).date()),
            "no_touch_high_turnover_rate": round(_safe_float(row.get("turnover_rate")), 4),
            "no_touch_high_turnover_lookback_trade_days": HIGH_TURNOVER_LOOKBACK_DAYS,
            "no_touch_high_turnover_min_rate": HIGH_TURNOVER_MIN_RATE,
            "no_touch_high_turnover_data_available": True,
        }
    return True, {
        "no_touch_high_turnover_found": False,
        "no_touch_high_turnover_lookback_trade_days": HIGH_TURNOVER_LOOKBACK_DAYS,
        "no_touch_high_turnover_min_rate": HIGH_TURNOVER_MIN_RATE,
        "no_touch_high_turnover_data_available": True,
    }


def _load_candidate_sectors(code: str, *, store: DuckDBStore) -> set[str]:
    normalized = str(code).zfill(6)
    if normalized in _MEMBERSHIP_CACHE:
        return _MEMBERSHIP_CACHE[normalized]
    with store._connect() as con:
        frame = con.execute(
            """
            select distinct sector_name, sector_type
            from stock_sector_membership
            where code=? and coalesce(is_current, true)
              and sector_name is not null and sector_name <> ''
            order by
              case sector_type
                when 'tdx_industry_l3' then 0
                when 'tdx_industry_l2' then 1
                when 'tdx_industry_l1' then 2
                when 'kaipanla_sector' then 3
                when 'hotspot' then 4
                when 'concept' then 5
                when 'industry' then 6
                else 99
              end,
              sector_name
            """,
            [normalized],
        ).df()
    sectors = _preferred_candidate_sectors(frame)
    _MEMBERSHIP_CACHE[normalized] = sectors
    return sectors


def _preferred_candidate_sectors(frame: pd.DataFrame) -> set[str]:
    if frame is None or frame.empty:
        return set()
    tdx_by_level: dict[int, set[str]] = {1: set(), 2: set(), 3: set()}
    fallback: set[str] = set()
    for _, row in frame.iterrows():
        name = str(row.get("sector_name") or "").strip()
        sector_type = str(row.get("sector_type") or "").strip()
        if not name:
            continue
        if is_tdx_industry_type(sector_type):
            tdx_by_level[int(sector_type.rsplit("_l", 1)[1])].add(name)
        else:
            fallback.add(name)
    for level in (3, 2, 1):
        if tdx_by_level[level]:
            return tdx_by_level[level]
    return fallback


def check_sector_top3_history(
    code: str,
    buy_date: str,
    *,
    store: DuckDBStore,
    candidate_sectors: set[str] | None = None,
    lookback_trade_days: int = SECTOR_TOP3_LOOKBACK_TRADE_DAYS,
) -> tuple[bool, dict[str, Any]]:
    sectors = candidate_sectors if candidate_sectors is not None else _load_candidate_sectors(code, store=store)
    if not sectors:
        return False, {"no_touch_sector_top3_found": False, "no_touch_sector_reason": "no_stock_sector_membership"}
    buy_ts = pd.to_datetime(buy_date)
    start_ts = buy_ts - timedelta(days=max(lookback_trade_days * 3, 20))
    cache_key = (_normalize_date(buy_ts), lookback_trade_days)
    top3 = _TOP3_CACHE.get(cache_key)
    if top3 is None:
        with store._connect() as con:
            top3 = con.execute(
                """
                with ranked as (
                  select trade_date, sector_name, strength_score, capital_score, turnover,
                         row_number() over (
                           partition by trade_date
                           order by coalesce(strength_score,0) desc, coalesce(capital_score,0) desc, coalesce(turnover,0) desc
                         ) as rn
                  from kaipanla_sector_strength
                  where trade_date < ? and trade_date >= ?
                )
                select * from ranked where rn <= 3 order by trade_date desc, rn asc
                """,
                [_normalize_date(buy_ts), _normalize_date(start_ts)],
            ).df()
        _TOP3_CACHE[cache_key] = top3
    if top3.empty:
        return False, {"no_touch_sector_top3_found": False, "no_touch_sector_reason": "no_sector_strength_window"}
    trade_dates = pd.Series(pd.to_datetime(top3["trade_date"])).drop_duplicates().sort_values(ascending=False).head(lookback_trade_days)
    window = top3[pd.to_datetime(top3["trade_date"]).isin(trade_dates)].copy()
    matched = window[window["sector_name"].astype(str).isin(sectors)]
    if matched.empty:
        return False, {
            "no_touch_sector_top3_found": False,
            "no_touch_sector_reason": "sector_never_top3_before_buy",
            "no_touch_candidate_sector_count": len(sectors),
            "no_touch_top3_available_days": int(window["trade_date"].nunique()),
            "no_touch_sector_top3_lookback_trade_days": lookback_trade_days,
        }
    best = matched.iloc[0]
    return True, {
        "no_touch_sector_top3_found": True,
        "no_touch_sector_top3_date": str(pd.to_datetime(best["trade_date"]).date()),
        "no_touch_sector_top3_name": str(best.get("sector_name")),
        "no_touch_sector_top3_rank": int(best.get("rn")),
        "no_touch_candidate_sector_count": len(sectors),
        "no_touch_sector_top3_lookback_trade_days": lookback_trade_days,
    }


def check_no_touch_filters(
    *,
    code: str,
    buy_date: str,
    daily: pd.DataFrame | None = None,
    store: DuckDBStore | None = None,
    candidate_sectors: set[str] | None = None,
    enforce_sector_top3: bool = True,
) -> tuple[bool, dict[str, Any]]:
    store = store or DuckDBStore()
    daily_window = _normalize_daily(daily, buy_date=buy_date)
    if daily_window.empty:
        daily_window = _normalize_daily(_load_daily_window(code, buy_date, store=store))
    reasons: list[str] = []
    meta: dict[str, Any] = {"no_touch_filter_version": "v1"}

    ok_one_word, one_word_meta = check_multiple_one_word_boards(daily_window)
    meta.update(one_word_meta)
    if not ok_one_word:
        reasons.append("multiple_one_word_limit_up_boards_before_buy")

    ok_top_bearish, top_bearish_meta = check_top_bearish_high_turnover(daily_window)
    meta.update(top_bearish_meta)
    if not ok_top_bearish:
        reasons.append("top_heavy_bearish_high_turnover_before_buy")

    ok_high_turnover, high_turnover_meta = check_high_turnover_before_buy(daily_window)
    meta.update(high_turnover_meta)
    if not ok_high_turnover:
        reasons.append("high_turnover_before_buy")

    meta["no_touch_enforce_sector_top3"] = bool(enforce_sector_top3)
    if enforce_sector_top3:
        ok_sector, sector_meta = check_sector_top3_history(code, buy_date, store=store, candidate_sectors=candidate_sectors)
        meta.update(sector_meta)
        if not ok_sector:
            reasons.append("sector_never_top3_before_buy")
    else:
        meta.update({"no_touch_sector_top3_skipped": True, "no_touch_sector_reason": "sector_top3_not_required_for_strategy"})

    meta["no_touch_reasons"] = reasons
    meta["no_touch_ok"] = len(reasons) == 0
    return len(reasons) == 0, meta
