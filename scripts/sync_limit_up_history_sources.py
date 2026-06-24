from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.storage.duckdb_store import DuckDBStore


def _normalize_date(value: str) -> str:
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def _compact_date(value: str) -> str:
    return _normalize_date(value).replace("-", "")


def _weekdays(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(_normalize_date(start_date), "%Y-%m-%d")
    end = datetime.strptime(_normalize_date(end_date), "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "") or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "") or pd.isna(value):
            return default
        text = str(value)
        if "/" in text:
            # 东方财富 涨停统计 常见格式：8/4，这里优先取后半段作为近期连板参考。
            text = text.split("/")[-1]
        return int(float(text))
    except Exception:
        return default


def _stock_limit_pct(code: str, name: str = "") -> float:
    code = str(code or "")
    name = str(name or "")
    if "ST" in name.upper() or "退" in name:
        return 0.05
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _is_limit_up(close: float, prev_close: float, limit_pct: float) -> bool:
    if prev_close <= 0 or close <= 0:
        return False
    change = close / prev_close - 1
    # 留出价格四舍五入和复权误差；ST/主板/创业科创/北交分别按接近涨停判断。
    tolerance = 0.002 if limit_pct <= 0.10 else 0.004
    return change >= limit_pct - tolerance


def _fetch_akshare_zt_pool(trade_date: str) -> pd.DataFrame:
    import akshare as ak

    try:
        df = ak.stock_zt_pool_em(date=_compact_date(trade_date))
    except Exception as exc:
        print(f"warn: akshare stock_zt_pool_em failed {trade_date}: {exc}")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df.copy()


def _rows_from_akshare(df: pd.DataFrame, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip().zfill(6)
        if not code or code == "000000":
            continue
        sector_name = str(row.get("所属行业", "") or "未知行业").strip() or "未知行业"
        sector_code = f"akshare_industry:{sector_name}"
        rows.append(
            {
                "trade_date": pd.to_datetime(trade_date).date(),
                "sector_code": sector_code,
                "sector_name": sector_name,
                "code": code,
                "name": str(row.get("名称", "") or ""),
                "limit_up_price": _safe_float(row.get("最新价")),
                "turnover": _safe_float(row.get("成交额")),
                "circulating_market_cap": _safe_float(row.get("流通市值")),
                "total_market_cap": _safe_float(row.get("总市值")),
                "consecutive_days": _safe_int(row.get("连板数"), 1),
                "consecutive_count": _safe_int(row.get("连板数"), 1),
                "concept_tags": sector_name,
                "theme": sector_name,
                "reason": "",
                "seal_amount": _safe_float(row.get("封板资金")),
                "main_net_inflow": 0.0,
                "first_limit_up_time": str(row.get("首次封板时间", "") or ""),
                "is_first_board": 1 if _safe_int(row.get("连板数"), 1) <= 1 else 0,
                "source": "akshare_stock_zt_pool_em",
                "raw_json": json.dumps(row.to_dict(), ensure_ascii=False, default=str),
            }
        )
    stocks = pd.DataFrame(rows)
    sectors = _sectors_from_stocks(stocks)
    return sectors, stocks


def _load_local_limit_up_candidates(store: DuckDBStore, trade_date: str) -> pd.DataFrame:
    with store._connect() as con:
        return con.execute(
            """
            with d as (
                select
                    k.code,
                    s.name,
                    k.trade_date,
                    k.close,
                    k.amount,
                    k.turnover_rate,
                    lag(k.close) over (partition by k.code order by k.trade_date) as prev_close
                from daily_kline k
                left join stock_basic s on s.code = k.code
                where k.adjust = 'hfq'
                  and k.trade_date <= ?
            )
            select *
            from d
            where trade_date = ?
              and prev_close is not null
            """,
            [pd.to_datetime(trade_date).date(), pd.to_datetime(trade_date).date()],
        ).df()


def _load_membership(store: DuckDBStore, codes: list[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    with store._connect() as con:
        con.register("codes_df", pd.DataFrame({"code": codes}))
        return con.execute(
            """
            select m.*
            from stock_sector_membership m
            join codes_df c using(code)
            where is_current = true
            """
        ).df()


def _rows_from_local_daily(store: DuckDBStore, trade_date: str, max_sectors_per_stock: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = _load_local_limit_up_candidates(store, trade_date)
    if daily.empty:
        return pd.DataFrame(), pd.DataFrame()
    daily["code"] = daily["code"].astype(str).str.zfill(6)
    daily["limit_pct"] = daily.apply(lambda r: _stock_limit_pct(r["code"], r.get("name", "")), axis=1)
    daily["is_limit_up"] = daily.apply(lambda r: _is_limit_up(float(r["close"]), float(r["prev_close"]), float(r["limit_pct"])), axis=1)
    limit_daily = daily[daily["is_limit_up"]].copy()
    if limit_daily.empty:
        return pd.DataFrame(), pd.DataFrame()

    memberships = _load_membership(store, limit_daily["code"].tolist())
    membership_by_code: dict[str, list[dict[str, Any]]] = {}
    if not memberships.empty:
        memberships["code"] = memberships["code"].astype(str).str.zfill(6)
        for code, group in memberships.groupby("code"):
            # 杰哥视角：优先概念/热点，其次行业；避免一只票爆出几十个概念导致表膨胀。
            group = group.copy()
            group["priority"] = group["sector_type"].map({"hotspot": 0, "concept": 1, "industry": 2}).fillna(3)
            selected = group.sort_values(["priority", "source", "sector_name"]).head(max_sectors_per_stock)
            membership_by_code[code] = selected.to_dict("records")

    rows: list[dict[str, Any]] = []
    for _, row in limit_daily.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row.get("name") or "")
        sectors = membership_by_code.get(code) or [
            {
                "sector_code": "local:unknown",
                "sector_name": "未知板块",
                "sector_type": "unknown",
                "source": "local_daily_limit_up",
            }
        ]
        for sector in sectors:
            sector_name = str(sector.get("sector_name") or "未知板块")
            sector_code = str(sector.get("sector_code") or f"local:{sector_name}")
            rows.append(
                {
                    "trade_date": pd.to_datetime(trade_date).date(),
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "code": code,
                    "name": name,
                    "limit_up_price": _safe_float(row.get("close")),
                    "turnover": _safe_float(row.get("amount")),
                    "circulating_market_cap": 0.0,
                    "total_market_cap": 0.0,
                    "consecutive_days": 1,
                    "consecutive_count": 1,
                    "concept_tags": sector_name,
                    "theme": sector_name,
                    "reason": "local_daily_kline_limit_up",
                    "seal_amount": 0.0,
                    "main_net_inflow": 0.0,
                    "first_limit_up_time": "",
                    "is_first_board": 1,
                    "source": "local_daily_kline_limit_up",
                    "raw_json": json.dumps(
                        {
                            "close": row.get("close"),
                            "prev_close": row.get("prev_close"),
                            "limit_pct": row.get("limit_pct"),
                            "change_pct": (float(row.get("close")) / float(row.get("prev_close")) - 1) * 100,
                            "sector_source": sector.get("source"),
                            "sector_type": sector.get("sector_type"),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )
    stocks = pd.DataFrame(rows)
    sectors = _sectors_from_stocks(stocks)
    return sectors, stocks


def _sectors_from_stocks(stocks: pd.DataFrame) -> pd.DataFrame:
    if stocks is None or stocks.empty:
        return pd.DataFrame()
    grouped = stocks.groupby(["trade_date", "sector_code", "sector_name", "source"], dropna=False).agg(stock_count=("code", "nunique")).reset_index()
    grouped["raw_json"] = grouped.apply(
        lambda r: json.dumps({"source": r["source"], "stock_count": int(r["stock_count"])}, ensure_ascii=False), axis=1
    )
    return grouped


def sync_one_day(store: DuckDBStore, trade_date: str, source_order: list[str], max_sectors_per_stock: int) -> dict[str, Any]:
    used_source = ""
    sectors = pd.DataFrame()
    stocks = pd.DataFrame()
    for source in source_order:
        if source == "akshare_zt_pool":
            ak_df = _fetch_akshare_zt_pool(trade_date)
            if not ak_df.empty:
                sectors, stocks = _rows_from_akshare(ak_df, trade_date)
                used_source = source
                break
        elif source == "local_daily":
            sectors, stocks = _rows_from_local_daily(store, trade_date, max_sectors_per_stock=max_sectors_per_stock)
            if not stocks.empty:
                used_source = source
                break
        elif source == "wencai":
            # 预留：当前环境未安装 pywencai，且问财网页版常需要动态/风控；不作为阻塞路径。
            continue
    if not stocks.empty:
        store.upsert_kaipanla_limit_up(sectors, stocks)
    return {
        "trade_date": trade_date,
        "source": used_source or "empty",
        "sector_rows": 0 if sectors is None or sectors.empty else len(sectors),
        "stock_rows": 0 if stocks is None or stocks.empty else len(stocks),
        "unique_codes": 0 if stocks is None or stocks.empty else int(stocks["code"].nunique()),
    }


def sync_bulk_local_daily(store: DuckDBStore, start_date: str, end_date: str, max_sectors_per_stock: int = 5) -> dict[str, Any]:
    """用 DuckDB 一次性从本地日线推导区间涨停股，并关联股票-板块归属。

    这条路径是开盘啦/同花顺/问财历史接口不可用时的确定性兜底：
    涨停股来自真实日线 close/prev_close，板块来自已落库的 stock_sector_membership。
    """
    with store._connect() as con:
        con.execute(
            """
            create temporary table bulk_limit_daily as
            with base as (
                select
                    k.code,
                    coalesce(s.name, '') as name,
                    k.trade_date,
                    k.close,
                    k.amount,
                    lag(k.close) over (partition by k.code order by k.trade_date) as prev_close
                from daily_kline k
                left join stock_basic s on s.code = k.code
                where k.adjust = 'hfq'
                  and k.trade_date <= ?
            ), scored as (
                select
                    *,
                    case
                        when upper(name) like '%ST%' or name like '%退%' then 0.05
                        when starts_with(code, '300') or starts_with(code, '301') or starts_with(code, '688') then 0.20
                        when starts_with(code, '8') or starts_with(code, '4') or starts_with(code, '92') then 0.30
                        else 0.10
                    end as limit_pct
                from base
                where trade_date between ? and ?
                  and prev_close is not null
                  and prev_close > 0
                  and close > 0
            )
            select *
            from scored
            where (close / prev_close - 1) >= limit_pct - case when limit_pct <= 0.10 then 0.002 else 0.004 end
            """,
            [pd.to_datetime(end_date).date(), pd.to_datetime(start_date).date(), pd.to_datetime(end_date).date()],
        )
        con.execute(
            """
            create temporary table bulk_membership_ranked as
            select
                m.*,
                row_number() over (
                    partition by m.code
                    order by
                        case m.sector_type when 'hotspot' then 0 when 'concept' then 1 when 'industry' then 2 else 3 end,
                        m.source,
                        m.sector_name
                ) as rn
            from stock_sector_membership m
            join (select distinct code from bulk_limit_daily) d using(code)
            where m.is_current = true
            """
        )
        stocks = con.execute(
            """
            select
                d.trade_date,
                coalesce(m.sector_code, 'local:unknown') as sector_code,
                coalesce(m.sector_name, '未知板块') as sector_name,
                d.code,
                d.name,
                d.close as limit_up_price,
                coalesce(d.amount, 0) as turnover,
                0.0 as circulating_market_cap,
                0.0 as total_market_cap,
                1 as consecutive_days,
                1 as consecutive_count,
                coalesce(m.sector_name, '未知板块') as concept_tags,
                coalesce(m.sector_name, '未知板块') as theme,
                'local_daily_kline_limit_up' as reason,
                0.0 as seal_amount,
                0.0 as main_net_inflow,
                '' as first_limit_up_time,
                1 as is_first_board,
                'local_daily_kline_limit_up' as source,
                json_object(
                    'close', d.close,
                    'prev_close', d.prev_close,
                    'limit_pct', d.limit_pct,
                    'change_pct', (d.close / d.prev_close - 1) * 100,
                    'sector_source', m.source,
                    'sector_type', m.sector_type
                ) as raw_json
            from bulk_limit_daily d
            left join bulk_membership_ranked m on m.code = d.code and m.rn <= ?
            """,
            [max_sectors_per_stock],
        ).df()
    sectors = _sectors_from_stocks(stocks)
    store.upsert_kaipanla_limit_up(sectors, stocks)
    return {
        "source": "bulk_local_daily",
        "sector_rows": 0 if sectors is None or sectors.empty else len(sectors),
        "stock_rows": 0 if stocks is None or stocks.empty else len(stocks),
        "unique_dates": 0 if stocks is None or stocks.empty else int(stocks["trade_date"].nunique()),
        "unique_codes": 0 if stocks is None or stocks.empty else int(stocks["code"].nunique()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="同步历史涨停生态：同花顺/问财优先预留，AKShare 涨停池优先，本地日线涨停兜底")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--sources", nargs="+", default=["akshare_zt_pool", "local_daily"], choices=["akshare_zt_pool", "wencai", "local_daily"])
    parser.add_argument("--bulk-local-daily", action="store_true", help="一次性用本地日线推导全区间涨停股，适合大区间历史回补")
    parser.add_argument("--max-sectors-per-stock", type=int, default=5)
    parser.add_argument("--throttle", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = DuckDBStore()
    if args.bulk_local_daily:
        result = sync_bulk_local_daily(store, args.start_date, args.end_date, args.max_sectors_per_stock)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    dates = _weekdays(args.start_date, args.end_date)
    if not dates:
        raise SystemExit("no dates resolved")
    print(f"sync limit-up history dates={dates[0]}..{dates[-1]} days={len(dates)} sources={args.sources}")
    results = []
    for idx, trade_date in enumerate(dates, start=1):
        if args.dry_run:
            result = {"trade_date": trade_date, "source": "dry_run", "sector_rows": 0, "stock_rows": 0, "unique_codes": 0}
        else:
            result = sync_one_day(store, trade_date, args.sources, args.max_sectors_per_stock)
        results.append(result)
        print(f"[{idx}/{len(dates)}] {trade_date} source={result['source']} sectors={result['sector_rows']} stocks={result['stock_rows']} codes={result['unique_codes']}")
        if args.throttle > 0:
            time.sleep(args.throttle)
    summary = pd.DataFrame(results).groupby("source").agg(days=("trade_date", "count"), stock_rows=("stock_rows", "sum"), unique_day_codes=("unique_codes", "sum")).reset_index()
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
