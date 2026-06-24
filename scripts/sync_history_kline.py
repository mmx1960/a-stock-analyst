from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.baostock_provider import BaostockProvider
from app.core.storage.duckdb_store import DuckDBStore


def normalize_daily_frame(df: pd.DataFrame, code: str, adjust: str) -> pd.DataFrame:
    frame = df.copy()
    if "trade_date" not in frame.columns:
        if "date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["date"]).dt.date
        elif "日期" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["日期"]).dt.date
    rename_map = {
        "成交额": "amount",
        "turnover": "amount",
        "成交量": "volume",
        "换手率": "turnover_rate",
        "涨跌幅": "change_pct",
    }
    frame = frame.rename(columns=rename_map)
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover_rate", "change_pct"]:
        if col not in frame.columns:
            frame[col] = None
    frame["code"] = code
    frame["adjust"] = adjust
    frame["source"] = frame.get("source", "composite")
    frame["updated_at"] = datetime.now()
    return frame[["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "change_pct", "adjust", "source", "updated_at"]]


def normalize_minute_frame(df: pd.DataFrame, code: str, period: str) -> pd.DataFrame:
    frame = df.copy()
    if "trade_dt" not in frame.columns:
        if "datetime" in frame.columns:
            frame["trade_dt"] = pd.to_datetime(frame["datetime"])
        elif "date" in frame.columns:
            frame["trade_dt"] = pd.to_datetime(frame["date"])
        elif "时间" in frame.columns:
            frame["trade_dt"] = pd.to_datetime(frame["时间"])
    rename_map = {
        "成交额": "amount",
        "turnover": "amount",
        "成交量": "volume",
        "vol": "volume",
    }
    frame = frame.rename(columns=rename_map)
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col not in frame.columns:
            frame[col] = None
    frame["code"] = code
    frame["period"] = str(period)
    frame["source"] = frame.get("source", "composite")
    frame["updated_at"] = datetime.now()
    if "trade_dt" not in frame.columns:
        raise ValueError("minute kline missing trade_dt/datetime/date column")
    frame["trade_dt"] = pd.to_datetime(frame["trade_dt"])
    columns = ["code", "period", "trade_dt", "open", "high", "low", "close", "volume", "amount", "source", "updated_at"]
    return frame.loc[:, columns].copy()


def resolve_codes(args: Any, store: Any) -> list[str]:
    codes = [c for c in args.codes if c]
    if codes:
        return codes[: args.limit] if args.limit > 0 else codes

    if args.from_db_stock_list:
        df = store.get_stock_basic()
        if df is None or df.empty:
            return []
        codes = df["code"].astype(str).tolist()
        if args.offset > 0:
            codes = codes[args.offset :]
        return codes[: args.limit] if args.limit > 0 else codes

    return []


def main():
    parser = argparse.ArgumentParser(description="同步历史日线/分钟线到 DuckDB")
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--from-db-stock-list", action="store_true", help="从本地 stock_basic 读取股票池")
    parser.add_argument("--start-date", default="20200101")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--adjust", default="hfq")
    parser.add_argument("--period", default="daily", choices=["daily", "5", "15", "30", "60"], help="K线周期；daily 写 daily_kline，分钟周期写 minute_kline")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--skip-if-exists", action="store_true", help="若本地已有指定区间数据则跳过")
    parser.add_argument("--refresh-incomplete-minute", action="store_true", help="分钟线本地覆盖不足时强制用 baostock 重拉并 upsert")
    parser.add_argument("--min-minute-rows", type=int, default=0, help="配合 --refresh-incomplete-minute；本地分钟行数低于该值则重拉")
    args = parser.parse_args()

    from app.core.providers.composite_provider import CompositeProvider

    provider = CompositeProvider()
    baostock_provider = BaostockProvider(auto_logout=False)
    store = DuckDBStore()

    codes = resolve_codes(args, store)
    if not codes:
        print("no codes resolved")
        return

    try:
        for idx, code in enumerate(codes, start=1):
            if args.period == "daily":
                if args.skip_if_exists and store.has_daily_kline(
                    code=code,
                    start_date=args.start_date,
                    end_date=args.end_date or None,
                    adjust=args.adjust,
                ):
                    print(f"[{idx}/{len(codes)}] skip {code}: daily exists")
                    continue

                df = provider.get_daily_bars(code=code, start_date=args.start_date, end_date=args.end_date or None, adjust=args.adjust)
                if df is None or df.empty:
                    print(f"[{idx}/{len(codes)}] skip {code}: daily empty")
                    continue
                normalized = normalize_daily_frame(df, code=code, adjust=args.adjust)
                store.upsert_daily_kline(normalized)
                print(f"[{idx}/{len(codes)}] synced {code} daily rows={len(normalized)}")
                continue

            local_minute = store.get_minute_kline(
                code=code,
                period=args.period,
                start_date=args.start_date,
                end_date=args.end_date or None,
            )
            local_rows = 0 if local_minute is None else len(local_minute)
            refresh_incomplete = args.refresh_incomplete_minute and args.min_minute_rows > 0 and local_rows < args.min_minute_rows
            if args.skip_if_exists and local_rows > 0 and not refresh_incomplete:
                print(f"[{idx}/{len(codes)}] skip {code}: {args.period}m exists rows={local_rows}")
                continue
            if refresh_incomplete:
                df = baostock_provider.get_minute_bars(code=code, period=args.period, start_date=args.start_date, end_date=args.end_date or None)
            else:
                df = provider.get_minute_bars(code=code, period=args.period, start_date=args.start_date, end_date=args.end_date or None)
            if df is None or df.empty:
                print(f"[{idx}/{len(codes)}] skip {code}: {args.period}m empty local_rows={local_rows}")
                continue
            normalized = normalize_minute_frame(df, code=code, period=args.period)
            store.upsert_minute_kline(normalized)
            print(f"[{idx}/{len(codes)}] synced {code} {args.period}m rows={len(normalized)} local_rows={local_rows}")
    finally:
        baostock_provider.close()


if __name__ == "__main__":
    main()
