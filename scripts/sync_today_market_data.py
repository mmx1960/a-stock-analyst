from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data_provider import data_provider
from app.core.storage.duckdb_store import DuckDBStore


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_codes(*, store: Any, codes: list[str], from_db_stock_list: bool, limit: int = 0, offset: int = 0) -> list[str]:
    resolved = [str(code).strip().zfill(6) for code in codes if str(code).strip()]
    if not resolved and from_db_stock_list:
        stock_basic = store.get_stock_basic()
        if stock_basic is not None and not stock_basic.empty and "code" in stock_basic.columns:
            resolved = stock_basic["code"].astype(str).str.zfill(6).tolist()
    if offset > 0:
        resolved = resolved[offset:]
    if limit > 0:
        resolved = resolved[:limit]
    return resolved


def normalize_quote(code: str, quote: dict[str, Any] | None, *, source: str = "data_provider") -> dict[str, Any] | None:
    if not quote:
        return None
    normalized_code = str(quote.get("code") or code).strip().zfill(6)
    now = datetime.now()
    raw_json = json.dumps(quote, ensure_ascii=False, default=str)
    amount = quote.get("amount") or quote.get("turnover") or quote.get("成交额")
    return {
        "code": normalized_code,
        "name": quote.get("name") or quote.get("名称") or normalized_code,
        "price": _safe_float(quote.get("price") or quote.get("latest_price") or quote.get("最新价")),
        "change_pct": _safe_float(quote.get("change_pct") or quote.get("涨跌幅")),
        "volume": _safe_float(quote.get("volume") or quote.get("成交量")),
        "amount": _safe_float(amount),
        "turnover": _safe_float(amount),
        "turnover_rate": _safe_float(quote.get("turnover_rate") or quote.get("换手率")),
        "market_cap": _safe_float(quote.get("market_cap") or quote.get("总市值")),
        "circulating_market_cap": _safe_float(quote.get("circulating_market_cap") or quote.get("流通市值")),
        "source": quote.get("source") or source,
        "raw_json": raw_json,
        "trade_dt": quote.get("trade_dt") or quote.get("updated_at") or now,
        "updated_at": now,
    }


def sync_realtime_quotes(
    *,
    store: Any,
    codes: list[str],
    throttle: float = 0.0,
    provider: Any = data_provider,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    empty_codes: list[str] = []
    for idx, code in enumerate(codes, start=1):
        quote = provider.get_realtime_quote(code)
        normalized = normalize_quote(code, quote)
        if normalized is None:
            empty_codes.append(code)
            print(f"[{idx}/{len(codes)}] skip {code}: empty quote")
        else:
            rows.append(normalized)
            print(f"[{idx}/{len(codes)}] quote {code} price={normalized.get('price')} amount={normalized.get('amount')}")
        if throttle > 0:
            time.sleep(throttle)
    if rows:
        store.upsert_realtime_quote_snapshot(pd.DataFrame(rows))
    return {"requested": len(codes), "synced": len(rows), "empty": len(empty_codes), "empty_codes": empty_codes}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步当日市场数据到 DuckDB")
    parser.add_argument("--include", default="quote", help="同步类型，当前支持 quote")
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--from-db-stock-list", action="store_true", help="从本地 stock_basic 读取股票池")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--throttle", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    includes = {item.strip() for item in args.include.split(",") if item.strip()}
    unsupported = includes - {"quote"}
    if unsupported:
        raise SystemExit(f"unsupported include values: {sorted(unsupported)}")

    store = DuckDBStore()
    codes = resolve_codes(
        store=store,
        codes=args.codes,
        from_db_stock_list=args.from_db_stock_list,
        limit=args.limit,
        offset=args.offset,
    )
    if not codes:
        print("no codes resolved")
        return

    summary = sync_realtime_quotes(store=store, codes=codes, throttle=args.throttle)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
