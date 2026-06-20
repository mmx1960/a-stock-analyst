from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.composite_provider import CompositeProvider
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


def resolve_codes(args, store: DuckDBStore) -> list[str]:
    codes = [c for c in args.codes if c]
    if codes:
        return codes[: args.limit] if args.limit > 0 else codes

    if args.from_db_stock_list:
        df = store.get_stock_basic(limit=args.limit if args.limit > 0 else None)
        if df is None or df.empty:
            return []
        codes = df["code"].astype(str).tolist()
        if args.offset > 0:
            codes = codes[args.offset :]
        return codes

    return []


def main():
    parser = argparse.ArgumentParser(description="同步历史日线到 DuckDB")
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--from-db-stock-list", action="store_true", help="从本地 stock_basic 读取股票池")
    parser.add_argument("--start-date", default="20200101")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--adjust", default="hfq")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--skip-if-exists", action="store_true", help="若本地已有指定区间数据则跳过")
    args = parser.parse_args()

    provider = CompositeProvider()
    store = DuckDBStore()

    codes = resolve_codes(args, store)
    if not codes:
        print("no codes resolved")
        return

    for idx, code in enumerate(codes, start=1):
        if args.skip_if_exists and store.has_daily_kline(
            code=code,
            start_date=args.start_date,
            end_date=args.end_date or None,
            adjust=args.adjust,
        ):
            print(f"[{idx}/{len(codes)}] skip {code}: exists")
            continue

        df = provider.get_daily_bars(code=code, start_date=args.start_date, end_date=args.end_date or None, adjust=args.adjust)
        if df is None or df.empty:
            print(f"[{idx}/{len(codes)}] skip {code}: empty")
            continue
        normalized = normalize_daily_frame(df, code=code, adjust=args.adjust)
        store.upsert_daily_kline(normalized)
        print(f"[{idx}/{len(codes)}] synced {code} rows={len(normalized)}")


if __name__ == "__main__":
    main()
