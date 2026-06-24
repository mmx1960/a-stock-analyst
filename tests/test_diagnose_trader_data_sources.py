from __future__ import annotations

import duckdb

from scripts.diagnose_trader_data_sources import diagnose


def _create_fake_db(path) -> None:
    con = duckdb.connect(str(path))
    con.execute("create table stock_basic(code varchar)")
    con.execute("insert into stock_basic select lpad(i::varchar, 6, '0') from range(0, 5316) t(i)")
    con.execute("create table daily_kline(code varchar, trade_date date, adjust varchar)")
    con.execute("insert into daily_kline select lpad(i::varchar, 6, '0'), date '2026-06-18', 'hfq' from range(0, 5204) t(i)")
    con.execute("create table minute_kline(code varchar, trade_dt timestamp, period varchar)")
    con.execute("insert into minute_kline select lpad(i::varchar, 6, '0'), timestamp '2026-06-18 15:00:00', '30' from range(0, 5200) t(i)")
    con.execute("create table realtime_quote_snapshot(code varchar, updated_at timestamp)")
    con.execute("insert into realtime_quote_snapshot select lpad(i::varchar, 6, '0'), timestamp '2026-06-21 09:00:00' from range(0, 4004) t(i)")
    con.execute("create table stock_sector_membership(code varchar, sector_name varchar, is_current bool)")
    con.execute("insert into stock_sector_membership select lpad(i::varchar, 6, '0'), '行业', true from range(0, 4062) t(i)")
    con.execute("create table kaipanla_sector_strength(sector_name varchar, trade_date date)")
    con.execute("insert into kaipanla_sector_strength select '板块' || (i % 9)::varchar, date '2026-06-19' - (i % 450)::int from range(0, 4043) t(i)")
    con.execute("create table kaipanla_market_sentiment(trade_date date)")
    con.execute("insert into kaipanla_market_sentiment select date '2026-06-19' - i::int from range(0, 605) t(i)")
    con.execute("create table kaipanla_limit_up_sectors(sector_name varchar, trade_date date)")
    con.execute("insert into kaipanla_limit_up_sectors select '题材' || i::varchar, date '2026-06-18' from range(0, 16) t(i)")
    con.execute("create table kaipanla_limit_up_stocks(code varchar, trade_date date)")
    con.execute("insert into kaipanla_limit_up_stocks select lpad(i::varchar, 6, '0'), date '2026-06-18' from range(0, 101) t(i)")
    con.close()


def test_diagnose_trader_data_sources_flags_d_shen_and_jie_ge_gaps(tmp_path) -> None:
    db_path = tmp_path / "fake.duckdb"
    _create_fake_db(db_path)

    result = diagnose(db_path)

    assert result["summary"]["high_gaps"] == 4
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["daily_hfq"]["status"] == "OK"
    assert checks["minute_30"]["status"] == "OK"
    assert checks["kaipanla_sector_strength"]["status"] == "HIGH_GAP"
    assert checks["kaipanla_limit_up_stocks"]["status"] == "HIGH_GAP"
    assert "板块资金一致性" in result["d_shen_verdict"]
    assert "涨停梯队历史太少" in result["jie_ge_verdict"]
