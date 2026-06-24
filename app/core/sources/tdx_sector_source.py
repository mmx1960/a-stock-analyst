from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from app.core.storage.sqlite_store import SQLiteStore

TdxIndustryFetcher = Callable[[str], Any]

TDX_INDUSTRY_LEVELS: tuple[tuple[int, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        1,
        "tdx_industry_l1",
        ("tdx_industry_l1", "industry_l1", "level1", "一级行业", "一级分类", "门类"),
        ("tdx_industry_l1_code", "industry_l1_code", "level1_code", "一级行业代码", "一级分类代码", "门类代码"),
    ),
    (
        2,
        "tdx_industry_l2",
        ("tdx_industry_l2", "industry_l2", "level2", "二级行业", "二级分类", "大类"),
        ("tdx_industry_l2_code", "industry_l2_code", "level2_code", "二级行业代码", "二级分类代码", "大类代码"),
    ),
    (
        3,
        "tdx_industry_l3",
        ("tdx_industry_l3", "industry_l3", "level3", "三级行业", "三级分类", "细分行业", "中类"),
        ("tdx_industry_l3_code", "industry_l3_code", "level3_code", "三级行业代码", "三级分类代码", "细分行业代码", "中类代码"),
    ),
)

_ROW_LEVEL_NAME_KEYS = (
    "sector_name",
    "industry_name",
    "blockname",
    "block_name",
    "name",
    "行业名称",
    "板块名称",
)
_ROW_LEVEL_CODE_KEYS = (
    "sector_code",
    "industry_code",
    "blockcode",
    "block_code",
    "code",
    "行业代码",
    "板块代码",
)


def normalize_tdx_industry_rows(payload: Any, *, code: str, name: str = "") -> list[dict[str, Any]]:
    """Normalize 通达信 industry hierarchy into one membership row per level."""

    frame = _payload_to_frame(payload)
    if frame.empty:
        return []

    normalized_code = _normalize_code(code)
    rows: list[dict[str, Any]] = []
    for _, raw_row in frame.iterrows():
        raw = raw_row.to_dict()
        row_level = _extract_row_level(raw)
        if row_level in {1, 2, 3}:
            sector_type = f"tdx_industry_l{row_level}"
            sector_name = _first_text(raw, _ROW_LEVEL_NAME_KEYS)
            if sector_name:
                rows.append(
                    _build_row(
                        code=normalized_code,
                        name=name or _first_text(raw, ("stock_name", "证券简称", "股票名称")),
                        level=row_level,
                        sector_type=sector_type,
                        sector_name=sector_name,
                        sector_code=_first_text(raw, _ROW_LEVEL_CODE_KEYS),
                        raw=raw,
                    )
                )

        for level, sector_type, name_keys, code_keys in TDX_INDUSTRY_LEVELS:
            sector_name = _first_text(raw, name_keys)
            if not sector_name:
                continue
            rows.append(
                _build_row(
                    code=normalized_code,
                    name=name or _first_text(raw, ("stock_name", "证券简称", "股票名称")),
                    level=level,
                    sector_type=sector_type,
                    sector_name=sector_name,
                    sector_code=_first_text(raw, code_keys),
                    raw=raw,
                )
            )
    return _dedupe_rows(rows)


class TdxSectorSource:
    """通达信股票行业归属 source.

    It stores each available industry hierarchy level separately:
    `tdx_industry_l1`, `tdx_industry_l2`, and `tdx_industry_l3`.
    """

    name = "tdx"

    def __init__(
        self,
        *,
        store: SQLiteStore | None = None,
        fetcher: TdxIndustryFetcher | None = None,
        throttle: float = 0.2,
    ):
        self.store = store or SQLiteStore()
        self.fetcher = fetcher or self._fetch_tdx_industry
        self.throttle = throttle

    def fetch_sector_strength(self, trade_date: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_stock_sector_membership(
        self,
        trade_date: str | None = None,
        *,
        limit: int = 0,
        offset: int = 0,
        **_: Any,
    ) -> pd.DataFrame:
        stocks = self.store.get_stock_basic()
        if stocks is None or stocks.empty:
            return pd.DataFrame()
        stocks = stocks.sort_values("code").iloc[max(0, int(offset)) :]
        if limit and limit > 0:
            stocks = stocks.head(int(limit))

        rows: list[dict[str, Any]] = []
        for _, stock in stocks.iterrows():
            stock_code = _normalize_code(stock.get("code"))
            if not stock_code:
                continue
            payload = self.fetcher(stock_code)
            rows.extend(
                normalize_tdx_industry_rows(
                    payload,
                    code=stock_code,
                    name=_clean_text(stock.get("name")),
                )
            )
            if self.throttle > 0:
                time.sleep(self.throttle)
        return pd.DataFrame(rows)

    @staticmethod
    def _fetch_tdx_industry(code: str) -> Any:
        from app.core.providers.mootdx_provider import MootdxProvider

        return MootdxProvider().get_stock_industry_hierarchy(code)


def _payload_to_frame(payload: Any) -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, pd.Series):
        return pd.DataFrame([payload.to_dict()])
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, tuple):
        return pd.DataFrame(list(payload))
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


def _build_row(
    *,
    code: str,
    name: str,
    level: int,
    sector_type: str,
    sector_name: str,
    sector_code: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    resolved_sector_code = sector_code or f"tdx:{level}:{sector_name}"
    return {
        "code": code,
        "name": name,
        "sector_code": resolved_sector_code,
        "sector_name": sector_name,
        "sector_type": sector_type,
        "source": "tdx",
        "is_current": True,
        "raw_json": json.dumps(raw, ensure_ascii=False, default=str),
    }


def _extract_row_level(raw: dict[str, Any]) -> int:
    sector_type = _clean_text(raw.get("sector_type") or raw.get("industry_type"))
    for level in (1, 2, 3):
        if sector_type.endswith(f"_l{level}") or sector_type == f"tdx_industry_l{level}":
            return level
    for key in ("level", "industry_level", "sector_level", "层级", "级别"):
        text = _clean_text(raw.get(key))
        if not text:
            continue
        for level in (1, 2, 3):
            if text in {str(level), f"L{level}", f"l{level}", f"{level}级", f"{level}级行业"}:
                return level
    return 0


def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _clean_text(raw.get(key))
        if value:
            return value
    return ""


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
        return datetime.now().strftime("%Y%m%d")
    if len(text) >= 10 and "-" in text:
        return text[:10].replace("-", "")
    return text[:8]
