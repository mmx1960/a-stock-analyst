from __future__ import annotations

import pandas as pd

from app.core.sources.tdx_sector_source import TdxSectorSource, normalize_tdx_industry_rows
from app.core.storage.sqlite_store import SQLiteStore


def test_normalize_tdx_industry_rows_keeps_three_hierarchy_levels() -> None:
    payload = pd.DataFrame(
        [
            {
                "industry_l1_code": "T01",
                "industry_l1": "信息技术",
                "industry_l2_code": "T0102",
                "industry_l2": "软件服务",
                "industry_l3_code": "T010203",
                "industry_l3": "基础软件",
            }
        ]
    )

    rows = normalize_tdx_industry_rows(payload, code="1", name="测试股")

    assert [row["sector_type"] for row in rows] == [
        "tdx_industry_l1",
        "tdx_industry_l2",
        "tdx_industry_l3",
    ]
    assert [row["sector_name"] for row in rows] == ["信息技术", "软件服务", "基础软件"]
    assert all(row["code"] == "000001" for row in rows)
    assert all(row["source"] == "tdx" for row in rows)


def test_tdx_sector_source_fetches_membership_from_stock_basic(tmp_path) -> None:
    store = SQLiteStore(db_path=tmp_path / "ashare.sqlite3")
    store.upsert_stock_basic(pd.DataFrame([{"code": "1", "name": "测试股", "source": "unit"}]))
    source = TdxSectorSource(
        store=store,
        fetcher=lambda code: {
            "一级行业": "信息技术",
            "二级行业": "软件服务",
            "三级行业": "基础软件",
        },
        throttle=0,
    )

    frame = source.fetch_stock_sector_membership()

    assert len(frame) == 3
    assert set(frame["sector_type"]) == {
        "tdx_industry_l1",
        "tdx_industry_l2",
        "tdx_industry_l3",
    }
