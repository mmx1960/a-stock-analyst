from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.kaipanla_provider import KaipanlaDateMismatchError, KaipanlaProvider


DEFAULT_SECTOR_STRENGTH_BOARDS = {
    "801660": "通信",
    "801001": "芯片",
    "801159": "机器人概念",
    "801807": "算力",
    "801694": "非金属材料",
    "801045": "医药",
    "803023": "AI应用",
    "801445": "元器件",
    "801088": "有色金属",
    "801250": "并购重组",
    "801235": "化工",
    "801843": "商业航天",
}


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


def _parse_sector_codes(values: list[str]) -> tuple[list[str], dict[str, str]]:
    if not values:
        return list(DEFAULT_SECTOR_STRENGTH_BOARDS.keys()), dict(DEFAULT_SECTOR_STRENGTH_BOARDS)
    codes: list[str] = []
    names: dict[str, str] = {}
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                code, name = part.split(":", 1)
                code = code.strip()
                if code:
                    codes.append(code)
                    names[code] = name.strip() or code
            else:
                codes.append(part)
    return codes, names


def _sync_sector_strength(provider: KaipanlaProvider, trade_date: str, args: argparse.Namespace) -> int:
    sector_codes, sector_names = _parse_sector_codes(args.sector_codes)
    frame = provider.sync_sector_strength(
        trade_date,
        sector_codes=sector_codes,
        sector_names=sector_names,
        lookback_days=args.sector_strength_lookback,
    )
    return len(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description="同步开盘啦 App 数据到 DuckDB")
    parser.add_argument("--dates", nargs="*", default=[], help="指定交易日，格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--end-date", default="", help="recent-days 模式的结束日期，默认今天")
    parser.add_argument("--recent-days", type=int, default=1, help="未指定 dates 时同步最近 N 个自然/交易日，默认 1")
    parser.add_argument("--skip-weekends", action="store_true", default=True, help="recent-days 模式跳过周末")
    parser.add_argument("--include-ladder", action="store_true", default=False, help="同步连板梯队；当前历史接口可能返回 1020，默认关闭")
    parser.add_argument("--market-only", action="store_true", default=False, help="只同步历史市场情绪，不同步涨停原因板块/个股")
    parser.add_argument("--sector-strength-only", action="store_true", default=False, help="只同步板块尾盘抢筹区间强度/区间净额/区间成交")
    parser.add_argument("--include-sector-strength", action="store_true", default=False, help="同步常规数据后额外同步板块尾盘抢筹区间强度")
    parser.add_argument("--max-board-type", type=int, default=5, help="兼容旧参数：历史连板聚合最高连板类型，当前区间强度路径不使用")
    parser.add_argument("--sector-strength-lookback", type=int, default=1, help="区间强度统计最近 N 个工作日，默认 1；策略 Top10 使用单日历史统计口径")
    parser.add_argument("--sector-codes", nargs="*", default=[], help="板块代码列表，支持 801660:通信 或逗号分隔；默认同步常用热点板块")
    parser.add_argument("--timeout", type=int, default=30, help="单请求超时秒数，默认 30")
    parser.add_argument("--min-interval", type=float, default=0.5, help="请求间隔秒数，默认 0.5")
    args = parser.parse_args()

    provider = KaipanlaProvider(timeout=args.timeout, min_interval=args.min_interval)
    dates = _resolve_dates(args)
    if not dates:
        print("no dates resolved")
        return

    for idx, trade_date in enumerate(dates, start=1):
        sector_strength_rows = 0
        if args.sector_strength_only:
            result = {"market_rows": 0, "sector_rows": 0, "stock_rows": 0, "ladder_rows": 0}
            sector_strength_rows = _sync_sector_strength(provider, trade_date, args)
        elif args.market_only:
            market = provider.get_daily_market_sentiment(trade_date)
            market_df = provider.normalize_market_sentiment_frame(market)
            provider.store.upsert_kaipanla_market_sentiment(market_df)
            result = {"market_rows": len(market_df), "sector_rows": 0, "stock_rows": 0, "ladder_rows": 0}
        else:
            try:
                result = provider.sync_trade_date(trade_date, include_ladder=args.include_ladder)
            except KaipanlaDateMismatchError as exc:
                market = provider.get_daily_market_sentiment(trade_date)
                market_df = provider.normalize_market_sentiment_frame(market)
                provider.store.upsert_kaipanla_market_sentiment(market_df)
                print(f"[{idx}/{len(dates)}] {trade_date} skip_limit_up={exc}")
                result = {"market_rows": len(market_df), "sector_rows": 0, "stock_rows": 0, "ladder_rows": 0}
            if args.include_sector_strength:
                sector_strength_rows = _sync_sector_strength(provider, trade_date, args)
        print(
            f"[{idx}/{len(dates)}] {trade_date} "
            f"market={result['market_rows']} sectors={result['sector_rows']} "
            f"stocks={result['stock_rows']} ladder={result['ladder_rows']} "
            f"sector_strength={sector_strength_rows}"
        )


if __name__ == "__main__":
    main()
