from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.bigamap_provider import BigAmapProvider
from app.core.providers.kaipanla_provider import KaipanlaProvider
from scripts.sync_kaipanla_data import DEFAULT_SECTOR_STRENGTH_BOARDS, _parse_sector_codes


def _normalize_date(value: str) -> str:
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def _weekdays(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(_normalize_date(start_date), "%Y-%m-%d")
    end = datetime.strptime(_normalize_date(end_date), "%Y-%m-%d")
    dates: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _resolve_boards(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    codes, names = _parse_sector_codes(args.sector_codes)
    if args.sector_codes:
        return codes, names
    if args.use_bigmamap_boards:
        try:
            payload = BigAmapProvider(timeout=args.timeout, min_interval=args.min_interval).get_board_rankings()
            boards: dict[str, str] = {}
            for day in payload.get("daily_rankings", []) or []:
                for board in day.get("boards", []) or []:
                    code = str(board.get("board_code") or "").strip()
                    name = str(board.get("board_name") or code).strip()
                    if code:
                        boards[code] = name
            if boards:
                return list(boards.keys()), boards
        except Exception as exc:
            print(f"warn: failed to load BigAmap boards, fallback defaults: {exc}")
    return codes, names


def _daily_qj(provider: KaipanlaProvider, sector_code: str, trade_date: str) -> dict[str, Any] | None:
    result = provider._post(
        provider.HISTORY_URL,
        {
            "a": "GetPlate_Info_QJ",
            "c": "ZhiShuRanking",
            "Date": trade_date,
            "PlateID": sector_code,
        },
    )
    if not result or result.get("errcode") != "0":
        return None
    values = result.get("List") or []
    if not isinstance(values, list) or len(values) < 4:
        return None
    return {
        "trade_date": provider._normalize_date(result.get("Date") or trade_date),
        "rank": provider._safe_int(values[0], 0),
        "strength": provider._safe_float(values[1]),
        "turnover": provider._safe_float(values[2]),
        "net_amount": provider._safe_float(values[3]),
        "raw": result,
    }


def _interval_row(provider: KaipanlaProvider, code: str, name: str, end_date: str, daily_rows: list[dict[str, Any]], lookback: int) -> dict[str, Any] | None:
    if not daily_rows:
        return None
    return {
        "trade_date": end_date,
        "sector_code": code,
        "sector_name": name or code,
        "limit_up_count": 0,
        "max_consecutive_days": 0,
        "stock_count": len(daily_rows),
        "turnover": sum(float(row.get("turnover") or 0) for row in daily_rows),
        "main_net_inflow": sum(float(row.get("net_amount") or 0) for row in daily_rows),
        "main_buy": 0.0,
        "main_sell": 0.0,
        "seal_amount": 0.0,
        "strength_score": round(sum(max(0.0, float(row.get("strength") or 0)) for row in daily_rows), 2),
        "capital_score": round(sum(float(row.get("net_amount") or 0) for row in daily_rows) / 1e8, 2),
        "source": "kaipanla_plate_info_qj_interval",
        "raw_json": provider._json({"lookback_days": lookback, "daily_rows": daily_rows}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="批量同步开盘啦尾盘抢筹区间强度历史数据")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--lookback", type=int, default=1, help="区间强度滚动工作日窗口，默认 1；策略 Top10 使用单日历史统计口径")
    parser.add_argument("--sector-codes", nargs="*", default=[], help="板块代码列表，支持 801660:通信 或逗号分隔")
    parser.add_argument("--use-bigmamap-boards", action="store_true", default=False, help="可选：用 BigAmap 当前 30 个板块作为板块池；默认不用外部板块池")
    parser.add_argument("--limit-sectors", type=int, default=0, help="调试用：只同步前 N 个板块")
    parser.add_argument("--flush-every", type=int, default=20, help="每 N 个交易日落库一次")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--min-interval", type=float, default=0.05)
    args = parser.parse_args()

    provider = KaipanlaProvider(timeout=args.timeout, min_interval=args.min_interval)
    sector_codes, sector_names = _resolve_boards(args)
    if args.limit_sectors > 0:
        sector_codes = sector_codes[: args.limit_sectors]
    if not sector_codes:
        raise SystemExit("no sector codes resolved")

    dates = _weekdays(args.start_date, args.end_date)
    if not dates:
        raise SystemExit("no dates resolved")

    daily_by_code: dict[str, dict[str, dict[str, Any]]] = {code: {} for code in sector_codes}
    pending_rows: list[dict[str, Any]] = []
    total_rows = 0
    started = time.time()

    print(f"sync sector_strength dates={dates[0]}..{dates[-1]} weekdays={len(dates)} sectors={len(sector_codes)} lookback={args.lookback}")
    for date_idx, trade_date in enumerate(dates, start=1):
        fetched = 0
        for code in sector_codes:
            row = _daily_qj(provider, code, trade_date)
            if row:
                daily_by_code[code][trade_date] = row
                fetched += 1
        window_dates = dates[max(0, date_idx - args.lookback):date_idx]
        for code in sector_codes:
            window_rows = [daily_by_code[code][d] for d in window_dates if d in daily_by_code[code]]
            interval = _interval_row(provider, code, sector_names.get(code, code), trade_date, window_rows, args.lookback)
            if interval:
                pending_rows.append(interval)
        if pending_rows and (date_idx % args.flush_every == 0 or date_idx == len(dates)):
            frame = pd.DataFrame(pending_rows)
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
            provider.store.upsert_kaipanla_sector_strength(frame)
            total_rows += len(frame)
            pending_rows.clear()
        if date_idx == 1 or date_idx % 20 == 0 or date_idx == len(dates):
            elapsed = time.time() - started
            print(f"[{date_idx}/{len(dates)}] {trade_date} fetched={fetched}/{len(sector_codes)} total_rows={total_rows} elapsed={elapsed:.1f}s")

    print(f"done rows={total_rows} dates={len(dates)} sectors={len(sector_codes)}")


if __name__ == "__main__":
    main()
