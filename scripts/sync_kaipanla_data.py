from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.kaipanla_provider import KaipanlaProvider


def _normalize_date(value: str) -> str:
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def _resolve_dates(args: argparse.Namespace) -> list[str]:
    if args.dates:
        return [_normalize_date(date) for date in args.dates]
    end = datetime.strptime(_normalize_date(args.end_date or datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d")
    dates = []
    current = end
    while len(dates) < args.recent_days:
        if not args.skip_weekends or current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return list(reversed(dates))


def main() -> None:
    parser = argparse.ArgumentParser(description="同步开盘啦 App 数据到 DuckDB")
    parser.add_argument("--dates", nargs="*", default=[], help="指定交易日，格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--end-date", default="", help="recent-days 模式的结束日期，默认今天")
    parser.add_argument("--recent-days", type=int, default=1, help="未指定 dates 时同步最近 N 个自然/交易日，默认 1")
    parser.add_argument("--skip-weekends", action="store_true", default=True, help="recent-days 模式跳过周末")
    parser.add_argument("--include-ladder", action="store_true", default=False, help="同步连板梯队；当前历史接口可能返回 1020，默认关闭")
    parser.add_argument("--timeout", type=int, default=30, help="单请求超时秒数，默认 30")
    parser.add_argument("--min-interval", type=float, default=0.5, help="请求间隔秒数，默认 0.5")
    args = parser.parse_args()

    provider = KaipanlaProvider(timeout=args.timeout, min_interval=args.min_interval)
    dates = _resolve_dates(args)
    if not dates:
        print("no dates resolved")
        return

    for idx, trade_date in enumerate(dates, start=1):
        result = provider.sync_trade_date(trade_date, include_ladder=args.include_ladder)
        print(
            f"[{idx}/{len(dates)}] {trade_date} "
            f"market={result['market_rows']} sectors={result['sector_rows']} "
            f"stocks={result['stock_rows']} ladder={result['ladder_rows']}"
        )


if __name__ == "__main__":
    main()
