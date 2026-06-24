from __future__ import annotations

import pandas as pd

from scripts.sync_limit_up_history_sources import _is_limit_up, _rows_from_akshare, _sectors_from_stocks, _stock_limit_pct


def test_stock_limit_pct_by_board_and_st():
    assert _stock_limit_pct("000001", "平安银行") == 0.10
    assert _stock_limit_pct("300001", "特锐德") == 0.20
    assert _stock_limit_pct("688001", "华兴源创") == 0.20
    assert _stock_limit_pct("430001", "北交样例") == 0.30
    assert _stock_limit_pct("000001", "ST测试") == 0.05


def test_is_limit_up_with_tolerance():
    assert _is_limit_up(11.0, 10.0, 0.10)
    assert _is_limit_up(10.98, 10.0, 0.10)
    assert not _is_limit_up(10.95, 10.0, 0.10)
    assert _is_limit_up(12.0, 10.0, 0.20)
    assert not _is_limit_up(11.9, 10.0, 0.20)


def test_rows_from_akshare_maps_limit_up_pool_to_existing_schema():
    raw = pd.DataFrame(
        [
            {
                "代码": "000811",
                "名称": "冰轮环境",
                "最新价": 40.17,
                "成交额": 170190288,
                "流通市值": 39302150000,
                "总市值": 39867840000,
                "封板资金": 341234067,
                "首次封板时间": "092500",
                "连板数": 2,
                "所属行业": "通用设备",
            },
            {
                "代码": "000889",
                "名称": "中嘉博创",
                "最新价": 4.02,
                "成交额": 185238794,
                "流通市值": 3496777000,
                "总市值": 3763890000,
                "封板资金": 55683030,
                "首次封板时间": "092500",
                "连板数": 1,
                "所属行业": "通信服务",
            },
        ]
    )

    sectors, stocks = _rows_from_akshare(raw, "2026-06-18")

    assert set(stocks["code"]) == {"000811", "000889"}
    assert set(stocks["source"]) == {"akshare_stock_zt_pool_em"}
    assert stocks.loc[stocks["code"] == "000811", "consecutive_days"].iloc[0] == 2
    assert set(sectors["sector_name"]) == {"通用设备", "通信服务"}
    assert sectors["stock_count"].sum() == 2


def test_sectors_from_stocks_groups_by_source_and_sector():
    stocks = pd.DataFrame(
        [
            {"trade_date": pd.to_datetime("2024-10-15").date(), "sector_code": "s1", "sector_name": "芯片", "source": "local_daily", "code": "000001"},
            {"trade_date": pd.to_datetime("2024-10-15").date(), "sector_code": "s1", "sector_name": "芯片", "source": "local_daily", "code": "000002"},
            {"trade_date": pd.to_datetime("2024-10-15").date(), "sector_code": "s2", "sector_name": "机器人", "source": "local_daily", "code": "000002"},
        ]
    )
    sectors = _sectors_from_stocks(stocks)
    assert len(sectors) == 2
    chip_count = sectors.loc[sectors["sector_name"] == "芯片", "stock_count"].iloc[0]
    assert chip_count == 2
