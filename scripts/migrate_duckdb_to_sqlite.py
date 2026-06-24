from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.storage.sqlite_store import SQLiteStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate existing DuckDB market data tables into SQLite")
    parser.add_argument("--duckdb-path", default=str(PROJECT_ROOT / "data" / "ashare.duckdb"))
    parser.add_argument("--sqlite-path", default=str(PROJECT_ROOT / "data" / "ashare.sqlite3"))
    parser.add_argument("--tables", nargs="*", default=["stock_basic", "daily_kline", "minute_kline", "realtime_quote_snapshot", "kaipanla_sector_strength", "stock_sector_membership", "kaipanla_limit_up_stocks"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        import duckdb
    except Exception as exc:
        raise SystemExit(f"duckdb package is required for migration: {exc}")

    duckdb_path = Path(args.duckdb_path)
    if not duckdb_path.exists():
        raise SystemExit(f"DuckDB file not found: {duckdb_path}")

    sqlite_store = SQLiteStore(db_path=Path(args.sqlite_path))
    con = duckdb.connect(str(duckdb_path), read_only=True)
    summary: dict[str, int] = {}
    try:
        for table in args.tables:
            try:
                frame = con.execute(f"SELECT * FROM {table}").df()
            except Exception as exc:
                print(f"skip {table}: {exc}")
                continue
            if frame.empty:
                summary[table] = 0
                continue
            if table == "stock_basic":
                sqlite_store.upsert_stock_basic(frame)
            elif table == "daily_kline":
                frame = frame.rename(columns={"trade_date": "trade_time"})
                frame["period"] = "d"
                sqlite_store.upsert_kline_bars(frame)
            elif table == "minute_kline":
                frame = frame.rename(columns={"trade_dt": "trade_time"})
                frame["adjust"] = ""
                sqlite_store.upsert_kline_bars(frame)
            elif table == "realtime_quote_snapshot":
                sqlite_store.upsert_realtime_quote_snapshot(frame)
            elif table == "kaipanla_sector_strength":
                sqlite_store.upsert_sector_strength(frame)
            elif table == "stock_sector_membership":
                sqlite_store.upsert_stock_sector_membership(frame)
            elif table == "kaipanla_limit_up_stocks":
                sqlite_store.upsert_limit_up_events(frame)
            else:
                print(f"skip unsupported table={table}")
                continue
            summary[table] = len(frame)
            print(f"migrated {table} rows={len(frame)}")
    finally:
        con.close()
    print(summary)


if __name__ == "__main__":
    main()
