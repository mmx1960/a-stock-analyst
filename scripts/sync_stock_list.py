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


def main():
    parser = argparse.ArgumentParser(description="同步股票列表到 DuckDB")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    provider = CompositeProvider()
    store = DuckDBStore()

    stocks = provider.get_stock_list() or []
    if args.limit > 0:
        stocks = stocks[: args.limit]

    if not stocks:
        print("no stocks fetched")
        return

    rows = []
    now = datetime.now()
    for item in stocks:
        rows.append(
            {
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "market": item.get("market", "A"),
                "exchange": item.get("exchange", ""),
                "list_date": item.get("list_date"),
                "status": item.get("status", "active"),
                "updated_at": now,
                "source": item.get("source", item.get("source_main", "composite")),
            }
        )

    df = pd.DataFrame(rows)
    store.upsert_stock_basic(df)
    print(f"synced stock_basic rows={len(df)}")


if __name__ == "__main__":
    main()
