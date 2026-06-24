from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.providers.kaipanla_provider import KaipanlaProvider
from app.core.storage.duckdb_store import DuckDBStore
from scripts.sync_kaipanla_data import DEFAULT_SECTOR_STRENGTH_BOARDS, _parse_sector_codes


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


def _pick_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def _membership_rows_from_cons(
    frame: pd.DataFrame,
    *,
    sector_code: str,
    sector_name: str,
    sector_type: str,
    source: str,
) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    code_col = _pick_column(frame, ["代码", "股票代码", "code", "证券代码"])
    name_col = _pick_column(frame, ["名称", "股票名称", "name", "证券简称"])
    if not code_col:
        return []
    rows = []
    for _, row in frame.iterrows():
        code = _normalize_code(row.get(code_col))
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "name": str(row.get(name_col) or "") if name_col else "",
                "sector_code": str(sector_code or sector_name),
                "sector_name": str(sector_name),
                "sector_type": sector_type,
                "source": source,
                "is_current": True,
                "raw_json": _json(row.to_dict()),
            }
        )
    return rows


def _sync_akshare_em(kind: str, *, limit_boards: int = 0, throttle: float = 1.0) -> pd.DataFrame:
    import akshare as ak

    if kind == "industry":
        name_func = ak.stock_board_industry_name_em
        cons_func = ak.stock_board_industry_cons_em
        sector_type = "industry"
    elif kind == "concept":
        name_func = ak.stock_board_concept_name_em
        cons_func = ak.stock_board_concept_cons_em
        sector_type = "concept"
    else:
        raise ValueError(f"unsupported akshare kind: {kind}")

    boards = name_func()
    if boards is None or boards.empty:
        return pd.DataFrame()
    if limit_boards > 0:
        boards = boards.head(limit_boards)

    board_name_col = _pick_column(boards, ["板块名称", "概念名称", "name", "板块"])
    board_code_col = _pick_column(boards, ["板块代码", "代码", "code"])
    if not board_name_col:
        raise ValueError(f"cannot find board name column from {boards.columns.tolist()}")

    all_rows: list[dict[str, Any]] = []
    for position, (_, board) in enumerate(boards.reset_index(drop=True).iterrows(), start=1):
        sector_name = _clean_text(board.get(board_name_col))
        sector_code = _clean_text(board.get(board_code_col)) if board_code_col else sector_name
        sector_code = sector_code or sector_name
        if not sector_name:
            continue
        try:
            cons = cons_func(symbol=sector_name)
            rows = _membership_rows_from_cons(
                cons,
                sector_code=sector_code,
                sector_name=sector_name,
                sector_type=sector_type,
                source=f"akshare_em_{kind}",
            )
            all_rows.extend(rows)
            print(f"[{position}/{len(boards)}] {kind} {sector_name} rows={len(rows)}")
        except Exception as exc:
            print(f"[{position}/{len(boards)}] {kind} {sector_name} failed={type(exc).__name__}: {exc}")
        if throttle > 0:
            time.sleep(throttle)
    return pd.DataFrame(all_rows)


def _sync_kaipanla_history(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_rows: int = 0,
    store: DuckDBStore,
) -> pd.DataFrame:
    clauses = []
    params: list[Any] = []
    if start_date:
        clauses.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("trade_date <= ?")
        params.append(end_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_sql = f"LIMIT {int(limit_rows)}" if limit_rows > 0 else ""
    with store._connect() as con:
        frame = con.execute(
            f"""
            SELECT code, name, sector_code, sector_name, theme, concept_tags, reason, trade_date, raw_json
            FROM kaipanla_limit_up_stocks
            {where}
            ORDER BY trade_date DESC
            {limit_sql}
            """,
            params,
        ).df()
    if frame.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        code = _normalize_code(row.get("code"))
        if not code:
            continue
        raw = row.to_dict()
        sector_name = str(row.get("sector_name") or "").strip()
        if sector_name:
            rows.append(
                {
                    "code": code,
                    "name": str(row.get("name") or ""),
                    "sector_code": str(row.get("sector_code") or sector_name),
                    "sector_name": sector_name,
                    "sector_type": "hotspot",
                    "source": "kaipanla_limit_up_history",
                    "is_current": True,
                    "raw_json": _json(raw),
                }
            )
        for key in ("theme", "concept_tags", "reason"):
            text = str(row.get(key) or "").strip()
            for part in text.replace("，", "、").replace(",", "、").split("、"):
                name = part.strip()
                if not name or len(name) > 30:
                    continue
                rows.append(
                    {
                        "code": code,
                        "name": str(row.get("name") or ""),
                        "sector_code": name,
                        "sector_name": name,
                        "sector_type": "concept",
                        "source": "kaipanla_limit_up_history",
                        "is_current": True,
                        "raw_json": _json(raw),
                    }
                )
    return pd.DataFrame(rows).drop_duplicates(subset=["code", "sector_code", "sector_type", "source"])


def _sync_kaipanla_sector_constituents(
    *,
    trade_date: str,
    sector_codes: list[str],
    max_pages: int = 0,
    throttle: float = 0.2,
) -> pd.DataFrame:
    provider = KaipanlaProvider(timeout=30, min_interval=throttle)
    if not sector_codes:
        sector_codes = list(DEFAULT_SECTOR_STRENGTH_BOARDS.keys())
    _, sector_names = _parse_sector_codes([])
    all_rows: list[dict[str, Any]] = []
    try:
        for position, sector_code in enumerate(sector_codes, start=1):
            sector_name = sector_names.get(sector_code, sector_code)
            payload = provider.get_sector_all_stocks(
                sector_code,
                trade_date=trade_date,
                max_pages=max_pages if max_pages > 0 else None,
            )
            rows = []
            for stock in payload.get("stocks") or []:
                if not isinstance(stock, list) or len(stock) < 2:
                    continue
                code = _normalize_code(stock[0])
                if not code:
                    continue
                rows.append(
                    {
                        "code": code,
                        "name": _clean_text(stock[1]),
                        "sector_code": sector_code,
                        "sector_name": sector_name,
                        "sector_type": "kaipanla_sector",
                        "source": "kaipanla_sector_constituents",
                        "is_current": True,
                        "raw_json": _json(
                            {
                                "trade_date": trade_date,
                                "sector_code": sector_code,
                                "sector_name": sector_name,
                                "stock": stock,
                                "total_count_from_api": payload.get("total_count_from_api"),
                                "core_count": payload.get("core_count"),
                            }
                        ),
                    }
                )
            all_rows.extend(rows)
            print(
                f"[{position}/{len(sector_codes)}] kaipanla_sector {sector_name} "
                f"rows={len(rows)} api_total={payload.get('total_count_from_api')} pages={payload.get('pages_fetched')}"
            )
    finally:
        provider._session.close()
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows).drop_duplicates(subset=["code", "sector_code", "sector_type", "source"])


def _sync_cninfo_industry(
    *,
    store: DuckDBStore,
    limit_stocks: int = 0,
    offset: int = 0,
    throttle: float = 0.5,
) -> pd.DataFrame:
    import akshare as ak

    stocks = store.get_stock_basic()
    if stocks is None or stocks.empty:
        raise ValueError("stock_basic is empty; run scripts/sync_stock_list.py first")
    stocks = stocks.sort_values("code").iloc[max(0, offset):]
    if limit_stocks > 0:
        stocks = stocks.head(limit_stocks)

    today = datetime.now().strftime("%Y%m%d")
    all_rows: list[dict[str, Any]] = []
    for position, (_, stock) in enumerate(stocks.iterrows(), start=1):
        code = _normalize_code(stock.get("code"))
        name = _clean_text(stock.get("name"))
        if not code:
            continue
        try:
            changes = ak.stock_industry_change_cninfo(symbol=code, start_date="19900101", end_date=today)
            if changes is None or changes.empty:
                print(f"[{position}/{len(stocks)}] cninfo {code} empty")
                continue
            latest_by_standard = changes.sort_values("变更日期").drop_duplicates(subset=["分类标准"], keep="last")
            rows = []
            for _, row in latest_by_standard.iterrows():
                standard = _clean_text(row.get("分类标准")) or "巨潮行业"
                sector_name = next(
                    (
                        value
                        for value in (
                            _clean_text(row.get("行业次类")),
                            _clean_text(row.get("行业中类")),
                            _clean_text(row.get("行业大类")),
                            _clean_text(row.get("行业门类")),
                        )
                        if value
                    ),
                    "",
                )
                if not sector_name:
                    continue
                rows.append(
                    {
                        "code": code,
                        "name": name or _clean_text(row.get("新证券简称")),
                        "sector_code": _clean_text(row.get("行业编码")) or sector_name,
                        "sector_name": sector_name,
                        "sector_type": "industry",
                        "source": f"cninfo_{standard}",
                        "is_current": True,
                        "raw_json": _json(row.to_dict()),
                    }
                )
            all_rows.extend(rows)
            print(f"[{position}/{len(stocks)}] cninfo {code} rows={len(rows)}")
        except Exception as exc:
            print(f"[{position}/{len(stocks)}] cninfo {code} failed={type(exc).__name__}: {exc}")
        if throttle > 0:
            time.sleep(throttle)
    return pd.DataFrame(all_rows)


def _extract_ths_stock_table(html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return pd.DataFrame()
    for table in tables:
        columns = [str(col) for col in table.columns]
        if "代码" in columns and "名称" in columns:
            return table
    return pd.DataFrame()


def _extract_ths_total_pages(html: str) -> int:
    match = re.search(r'<span class="page_info">\s*\d+\s*/\s*(\d+)\s*</span>', html)
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


def _sync_ths_concept(
    *,
    limit_boards: int = 0,
    offset_boards: int = 0,
    limit_pages: int = 0,
    throttle: float = 0.5,
) -> pd.DataFrame:
    import akshare as ak

    boards = ak.stock_board_concept_name_ths()
    if boards is None or boards.empty:
        return pd.DataFrame()
    if offset_boards > 0:
        boards = boards.iloc[offset_boards:]
    if limit_boards > 0:
        boards = boards.head(limit_boards)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    all_rows: list[dict[str, Any]] = []
    for board_position, (_, board) in enumerate(boards.reset_index(drop=True).iterrows(), start=1):
        sector_name = _clean_text(board.get("name"))
        sector_code = _clean_text(board.get("code")) or sector_name
        if not sector_code or not sector_name:
            continue
        total_pages = 1
        board_rows = 0
        empty_pages = 0
        page = 1
        while page <= total_pages:
            if limit_pages > 0 and page > limit_pages:
                break
            url = (
                f"https://q.10jqka.com.cn/gn/detail/code/{sector_code}/"
                if page == 1
                else f"https://q.10jqka.com.cn/gn/detail/code/{sector_code}/page/{page}/"
            )
            try:
                response = session.get(
                    url,
                    timeout=20,
                    headers={"Referer": f"https://q.10jqka.com.cn/gn/detail/code/{sector_code}/"},
                )
                response.raise_for_status()
                html = response.text
                if page == 1:
                    total_pages = _extract_ths_total_pages(html)
                table = _extract_ths_stock_table(html)
                rows = _membership_rows_from_cons(
                    table,
                    sector_code=sector_code,
                    sector_name=sector_name,
                    sector_type="concept",
                    source="ths_concept_page",
                )
                all_rows.extend(rows)
                board_rows += len(rows)
                empty_pages = empty_pages + 1 if not rows else 0
                print(f"[{board_position}/{len(boards)}] ths {sector_name} page={page}/{total_pages} rows={len(rows)}")
                if empty_pages >= 2:
                    print(f"[{board_position}/{len(boards)}] ths {sector_name} stop_after_empty_pages={empty_pages}")
                    break
            except Exception as exc:
                print(f"[{board_position}/{len(boards)}] ths {sector_name} page={page} failed={type(exc).__name__}: {exc}")
                break
            page += 1
            if throttle > 0:
                time.sleep(throttle)
        print(f"[{board_position}/{len(boards)}] ths {sector_name} total_rows={board_rows}")
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows).drop_duplicates(subset=["code", "sector_code", "sector_type", "source"])


def main() -> None:
    parser = argparse.ArgumentParser(description="同步股票-板块归属到 DuckDB stock_sector_membership")
    parser.add_argument(
        "--sources",
        nargs="*",
        default=["kaipanla_history", "cninfo_industry", "ths_concept", "akshare_em_industry", "akshare_em_concept"],
        help="可选：kaipanla_history kaipanla_sector_constituents cninfo_industry ths_concept akshare_em_industry akshare_em_concept",
    )
    parser.add_argument("--start-date", default="", help="kaipanla_history 起始日期")
    parser.add_argument("--end-date", default="", help="kaipanla_history 截止日期")
    parser.add_argument("--limit-boards", type=int, default=0, help="调试用：每类板块源只同步前 N 个板块")
    parser.add_argument("--offset-boards", type=int, default=0, help="同花顺概念板块偏移量")
    parser.add_argument("--limit-pages", type=int, default=0, help="调试用：同花顺每个概念只同步前 N 页")
    parser.add_argument("--limit-stocks", type=int, default=0, help="调试用：巨潮行业只同步前 N 只股票")
    parser.add_argument("--offset", type=int, default=0, help="巨潮行业股票池偏移量")
    parser.add_argument("--limit-history-rows", type=int, default=0, help="调试用：只投影前 N 条开盘啦历史个股")
    parser.add_argument("--trade-date", default=datetime.now().strftime("%Y-%m-%d"), help="kaipanla_sector_constituents 查询日期")
    parser.add_argument("--sector-codes", nargs="*", default=[], help="开盘啦板块代码列表，支持 801660:通信 或逗号分隔；默认常用热点板块")
    parser.add_argument("--throttle", type=float, default=1.0, help="AKShare / 开盘啦板块成分请求间隔秒数")
    args = parser.parse_args()

    store = DuckDBStore()
    total = 0
    started = datetime.now()

    for source in args.sources:
        frame = pd.DataFrame()
        try:
            if source == "kaipanla_history":
                frame = _sync_kaipanla_history(
                    start_date=args.start_date or None,
                    end_date=args.end_date or None,
                    limit_rows=args.limit_history_rows,
                    store=store,
                )
            elif source == "kaipanla_sector_constituents":
                sector_codes, _ = _parse_sector_codes(args.sector_codes)
                frame = _sync_kaipanla_sector_constituents(
                    trade_date=args.trade_date,
                    sector_codes=sector_codes,
                    max_pages=args.limit_pages,
                    throttle=args.throttle,
                )
            elif source == "akshare_em_industry":
                frame = _sync_akshare_em("industry", limit_boards=args.limit_boards, throttle=args.throttle)
            elif source == "akshare_em_concept":
                frame = _sync_akshare_em("concept", limit_boards=args.limit_boards, throttle=args.throttle)
            elif source == "cninfo_industry":
                frame = _sync_cninfo_industry(
                    store=store,
                    limit_stocks=args.limit_stocks,
                    offset=args.offset,
                    throttle=args.throttle,
                )
            elif source == "ths_concept":
                frame = _sync_ths_concept(
                    limit_boards=args.limit_boards,
                    offset_boards=args.offset_boards,
                    limit_pages=args.limit_pages,
                    throttle=args.throttle,
                )
            else:
                print(f"skip unknown source={source}")
                continue
            store.upsert_stock_sector_membership(frame)
            total += len(frame)
            print(f"source={source} upsert_rows={len(frame)}")
        except Exception as exc:
            print(f"source={source} failed={type(exc).__name__}: {exc}")

    elapsed = (datetime.now() - started).total_seconds()
    print(f"done total_rows={total} elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    main()
