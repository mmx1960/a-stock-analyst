from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from app.core.storage.sqlite_store import SQLiteStore

CninfoFetcher = Callable[[str, str, str], pd.DataFrame]

CNINFO_LEVELS = [
    ("行业门类", "cninfo_industry_l1"),
    ("行业大类", "cninfo_industry_l2"),
    ("行业中类", "cninfo_industry_l3"),
    ("行业次类", "cninfo_industry_l4"),
]


def normalize_cninfo_industry_rows(frame: pd.DataFrame, *, code: str, name: str = "") -> list[dict[str, Any]]:
    """Normalize 巨潮 industry hierarchy into one row per level."""

    if frame is None or frame.empty:
        return []
    normalized_code = _normalize_code(code)
    rows: list[dict[str, Any]] = []
    latest_by_standard = frame.copy()
    if "变更日期" in latest_by_standard.columns:
        latest_by_standard = latest_by_standard.sort_values("变更日期")
    if "分类标准" in latest_by_standard.columns:
        latest_by_standard = latest_by_standard.drop_duplicates(subset=["分类标准"], keep="last")
    for _, raw_row in latest_by_standard.iterrows():
        raw = raw_row.to_dict()
        standard = _clean_text(raw_row.get("分类标准")) or "巨潮行业"
        base_code = _clean_text(raw_row.get("行业编码")) or standard
        for position, (column, sector_type) in enumerate(CNINFO_LEVELS, start=1):
            sector_name = _clean_text(raw_row.get(column))
            if not sector_name:
                continue
            rows.append(
                {
                    "code": normalized_code,
                    "name": name or _clean_text(raw_row.get("新证券简称")),
                    "sector_code": f"{base_code}:{position}:{sector_name}",
                    "sector_name": sector_name,
                    "sector_type": sector_type,
                    "source": f"cninfo_{standard}",
                    "is_current": True,
                    "raw_json": json.dumps(raw, ensure_ascii=False, default=str),
                }
            )
    return _dedupe_rows(rows)


class CninfoSectorSource:
    """巨潮股票行业归属 source.

    This source is intended for stock-sector membership, especially nested
    industry levels. It stores all available hierarchy levels instead of only
    the deepest industry name.
    """

    name = "cninfo"

    def __init__(
        self,
        *,
        store: SQLiteStore | None = None,
        fetcher: CninfoFetcher | None = None,
        throttle: float = 0.5,
    ):
        self.store = store or SQLiteStore()
        self.fetcher = fetcher or self._fetch_cninfo
        self.throttle = throttle

    def fetch_sector_strength(self, trade_date: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_stock_sector_membership(
        self,
        trade_date: str | None = None,
        *,
        limit: int = 0,
        offset: int = 0,
        start_date: str = "19900101",
        end_date: str | None = None,
    ) -> pd.DataFrame:
        stocks = self.store.get_stock_basic()
        if stocks is None or stocks.empty:
            return pd.DataFrame()
        stocks = stocks.sort_values("code").iloc[max(0, int(offset)) :]
        if limit and limit > 0:
            stocks = stocks.head(int(limit))
        end = _compact_date(end_date or trade_date or datetime.now().strftime("%Y%m%d"))
        rows: list[dict[str, Any]] = []
        for _, stock in stocks.iterrows():
            code = _normalize_code(stock.get("code"))
            if not code:
                continue
            frame = self.fetcher(code, _compact_date(start_date), end)
            rows.extend(
                normalize_cninfo_industry_rows(
                    frame,
                    code=code,
                    name=_clean_text(stock.get("name")),
                )
            )
            if self.throttle > 0:
                time.sleep(self.throttle)
        return pd.DataFrame(rows)

    @staticmethod
    def _fetch_cninfo(code: str, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        return ak.stock_industry_change_cninfo(symbol=code, start_date=start_date, end_date=end_date)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("code") or ""),
            str(row.get("sector_code") or ""),
            str(row.get("sector_type") or ""),
            str(row.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = text.replace("sh", "").replace("sz", "").replace("bj", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _compact_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and "-" in text:
        return text[:10].replace("-", "")
    return text[:8]
