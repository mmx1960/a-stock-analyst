from __future__ import annotations

import pandas as pd

from app.core.providers.mootdx_provider import MootdxProvider


class FakeBlockClient:
    def block(self, tofile="block.dat"):
        assert tofile in {"block.dat", "block_zs.dat"}
        return pd.DataFrame(
            [
                {
                    "blockcode": "T010203",
                    "blockname": "基础软件",
                    "block_type": "三级行业",
                    "codes": ["000001", "000002"],
                },
                {
                    "blockcode": "T999999",
                    "blockname": "其他行业",
                    "block_type": "三级行业",
                    "codes": ["000003"],
                },
            ]
        )


def test_mootdx_provider_extracts_industry_rows_from_block_api() -> None:
    provider = MootdxProvider.__new__(MootdxProvider)

    frame = provider._get_stock_industry_from_block_api(FakeBlockClient(), "000001")

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["sector_code"] == "T010203"
    assert row["sector_name"] == "基础软件"
    assert row["sector_type"] == "tdx_industry_l3"
