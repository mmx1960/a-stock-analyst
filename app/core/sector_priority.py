from __future__ import annotations

from typing import Any

INDUSTRY_LEVEL_PRIORITY = {
    "tdx_industry_l3": 0,
    "tdx_industry_l2": 1,
    "tdx_industry_l1": 2,
    "industry_l3": 0,
    "industry_l2": 1,
    "industry_l1": 2,
}

SECTOR_TYPE_PRIORITY = {
    **INDUSTRY_LEVEL_PRIORITY,
    "kaipanla_sector": 3,
    "hotspot": 4,
    "concept": 5,
    "industry": 6,
    "akshare_em_industry": 6,
    "akshare_em_concept": 7,
    "cninfo_industry_l4": 8,
    "cninfo_industry_l3": 9,
    "cninfo_industry_l2": 10,
    "cninfo_industry_l1": 11,
}


def sector_type_priority(value: Any) -> int:
    text = str(value or "").strip()
    if text in SECTOR_TYPE_PRIORITY:
        return SECTOR_TYPE_PRIORITY[text]
    if text.startswith("tdx_industry_l"):
        return INDUSTRY_LEVEL_PRIORITY.get(text, 12)
    if text.startswith("cninfo_industry_l"):
        return SECTOR_TYPE_PRIORITY.get(text, 13)
    return 99


def is_tdx_industry_type(value: Any) -> bool:
    return str(value or "").strip() in {"tdx_industry_l1", "tdx_industry_l2", "tdx_industry_l3"}
