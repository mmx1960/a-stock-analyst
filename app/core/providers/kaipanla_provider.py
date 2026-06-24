from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests
import urllib3

from app.core.storage.duckdb_store import DuckDBStore

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class KaipanlaDateMismatchError(RuntimeError):
    """Raised when Kaipanla returns a different trade date than requested."""


class KaipanlaProvider:
    """开盘啦 App 数据源：市场情绪、涨停原因板块、连板梯队。

    参考 https://github.com/jinhao2003/kaipanla-crawler 的公开 App API 调用方式，
    只封装本项目需要的核心接口，并把数据归一化到 DuckDB。
    """

    HISTORY_URL = "https://apphis.longhuvip.com/w1/api/index.php"
    REALTIME_URL = "https://apphwhq.longhuvip.com/w1/api/index.php"
    VERSION = "5.21.0.2"
    API_VERSION = "w42"

    def __init__(self, timeout: int = 30, min_interval: float = 0.5, store: Optional[DuckDBStore] = None):
        self.timeout = timeout
        self.min_interval = min_interval
        self.store = store or DuckDBStore()
        self._last_request_at = 0.0
        self._session = requests.Session()
        # 开盘啦接口经常被本机 HTTP(S)_PROXY / macOS 系统代理污染，
        # requests 即使传 proxies={} 也可能继续读取环境代理。
        # 这里必须关闭 trust_env，保证历史补库直连 longhuvip。
        self._session.trust_env = False

    def _headers(self, host: str) -> dict[str, str]:
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SHARK PRS-A0 Build/PQ3A.190605.01141736)",
            "Host": host,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.time()

    def _post(self, url: str, data: dict[str, Any], *, timeout: Optional[int] = None) -> dict[str, Any]:
        self._throttle()
        host = "apphwhq.longhuvip.com" if url == self.REALTIME_URL else "apphis.longhuvip.com"
        payload = {
            "PhoneOSNew": "1",
            "DeviceID": str(uuid.uuid4()),
            "VerSion": self.VERSION,
            "apiv": self.API_VERSION,
        }
        payload.update(data)
        response = self._session.post(
            url,
            data=payload,
            headers=self._headers(host),
            verify=False,
            proxies={},
            timeout=timeout or self.timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _normalize_date(value: Optional[str]) -> str:
        if not value:
            return datetime.now().strftime("%Y-%m-%d")
        value = str(value)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return value[:10]

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def get_daily_market_sentiment(self, trade_date: Optional[str] = None) -> dict[str, Any]:
        trade_date = self._normalize_date(trade_date)
        stats = self._post(self.HISTORY_URL, {"a": "HisZhangFuDetail", "c": "HisHomeDingPan", "Day": trade_date})
        index = self._post(self.HISTORY_URL, {"a": "GetZsReal", "c": "StockL2History", "Day": trade_date})
        ladder_expr = self._post(self.HISTORY_URL, {"a": "ZhangTingExpression", "c": "HisHomeDingPan", "Day": trade_date})
        withdrawal = self._post(self.HISTORY_URL, {"a": "SharpWithdrawal", "c": "HisHomeDingPan", "Day": trade_date})

        info = stats.get("info", {}) if stats else {}
        sh_index = None
        for item in index.get("StockList", []) if index else []:
            if item.get("StockID") == "SH000001":
                sh_index = item
                break
        ladder_info = ladder_expr.get("info", []) if ladder_expr else []
        return {
            "trade_date": self._normalize_date(stats.get("date", trade_date) if stats else trade_date),
            "up_count": self._safe_int(info.get("SZJS")),
            "down_count": self._safe_int(info.get("XDJS")),
            "flat_count": self._safe_int(info.get("0")),
            "limit_up_count": self._safe_int(info.get("ZT")),
            "actual_limit_up_count": self._safe_int(info.get("SJZT")),
            "limit_down_count": self._safe_int(info.get("DT")),
            "actual_limit_down_count": self._safe_int(info.get("SJDT")),
            "rise_fall_ratio": self._safe_float(info.get("ZBL")),
            "yesterday_rise_fall_ratio": self._safe_float(info.get("yestRase")),
            "sh_index": self._safe_float((sh_index or {}).get("last_px")),
            "sh_change_pct": str((sh_index or {}).get("increase_rate", "")),
            "sh_amount": self._safe_float((sh_index or {}).get("turnover")),
            "first_board_count": self._safe_int(ladder_info[0] if len(ladder_info) > 0 else 0),
            "second_board_count": self._safe_int(ladder_info[1] if len(ladder_info) > 1 else 0),
            "third_board_count": self._safe_int(ladder_info[2] if len(ladder_info) > 2 else 0),
            "fourth_plus_board_count": self._safe_int(ladder_info[3] if len(ladder_info) > 3 else 0),
            "consecutive_board_rate": self._safe_float(ladder_info[4] if len(ladder_info) > 4 else 0),
            "sharp_withdrawal_count": self._safe_int(withdrawal.get("num", 0) if withdrawal else 0),
            "source": "kaipanla",
            "raw_json": self._json({"stats": stats, "index": index, "ladder_expr": ladder_expr, "withdrawal": withdrawal}),
        }

    def get_limit_up_sectors(self, trade_date: Optional[str] = None, index: int = 0) -> dict[str, Any]:
        trade_date = self._normalize_date(trade_date)
        result = self._post(
            self.REALTIME_URL,
            {
                "a": "GetPlateInfo_w38",
                "st": "100",
                "c": "DailyLimitResumption",
                "Index": str(index),
                "Day": trade_date,
            },
        )
        if not result or result.get("errcode") != "0":
            logger.warning("kaipanla limit-up sectors failed: %s", result)
            return {"summary": {"trade_date": trade_date}, "sectors": [], "raw": result or {}}
        response_date = self._normalize_date(result.get("date") or result.get("Date") or result.get("Day") or trade_date)
        if response_date != trade_date:
            raise KaipanlaDateMismatchError(
                f"kaipanla limit-up sectors date mismatch: requested {trade_date}, got {response_date}"
            )
        nums = result.get("nums", {}) or {}
        summary = {
            "trade_date": response_date,
            "up_count": self._safe_int(nums.get("SZJS")),
            "down_count": self._safe_int(nums.get("XDJS")),
            "limit_up_count": self._safe_int(nums.get("ZT")),
            "limit_down_count": self._safe_int(nums.get("DT")),
            "rise_fall_ratio": self._safe_float(nums.get("ZBL")),
            "yesterday_rise_fall_ratio": self._safe_float(nums.get("yestRase")),
        }
        sectors = []
        for sector_data in result.get("list", []) or []:
            sector = {
                "sector_code": str(sector_data.get("ZSCode") or ""),
                "sector_name": str(sector_data.get("ZSName") or ""),
                "stock_count": self._safe_int(sector_data.get("num")),
                "stocks": [],
                "raw": sector_data,
            }
            for stock in sector_data.get("StockList", []) or []:
                if len(stock) < 19:
                    continue
                sector["stocks"].append(self._parse_limit_up_stock(stock))
            sectors.append(sector)
        return {"summary": summary, "sectors": sectors, "raw": result}

    def _parse_limit_up_stock(self, stock: list[Any]) -> dict[str, Any]:
        seal_time = self._format_kpl_time(stock[14] if len(stock) > 14 else "")
        return {
            "code": str(stock[0]),
            "name": str(stock[1]),
            "limit_up_price": self._safe_float(stock[4]),
            "turnover": 0.0,
            "circulating_market_cap": self._safe_float(stock[8]),
            "consecutive_days": self._safe_int(stock[9]),
            "consecutive_count": self._safe_int(stock[10]),
            "concept_tags": str(stock[11] or ""),
            "seal_amount": self._safe_float(stock[12]),
            "main_net_inflow": self._safe_float(stock[13]),
            "first_limit_up_time": seal_time,
            "total_market_cap": self._safe_float(stock[15]),
            "reason": str(stock[16] or ""),
            "theme": str(stock[17] or ""),
            "is_first_board": self._safe_int(stock[18]),
            "raw": stock,
        }

    def _format_kpl_time(self, raw: Any) -> str:
        if raw in (None, ""):
            return ""
        try:
            value = float(raw)
            hour = int(value)
            minute = int(round((value - hour) * 60))
            if minute >= 60:
                hour += minute // 60
                minute %= 60
            if 0 <= hour <= 23:
                return f"{hour:02d}:{minute:02d}:00"
        except (TypeError, ValueError):
            pass
        return str(raw)

    def get_historical_board_stocks(self, trade_date: str, *, max_board_type: int = 5) -> list[dict[str, Any]]:
        trade_date = self._normalize_date(trade_date)
        rows: list[dict[str, Any]] = []
        for board_type in range(1, max_board_type + 1):
            result = self._post(
                self.HISTORY_URL,
                {
                    "Order": "0",
                    "a": "DailyLimitPerformance",
                    "st": "2000",
                    "c": "HisHomeDingPan",
                    "Index": "0",
                    "PidType": str(board_type),
                    "Type": "4",
                    "Day": trade_date,
                },
            )
            if not result or result.get("errcode") != "0":
                logger.warning("kaipanla historical board stocks failed %s board=%s: %s", trade_date, board_type, result)
                continue
            info = result.get("info") or []
            stock_list = info[0] if info and isinstance(info[0], list) else []
            for stock in stock_list:
                parsed = self._parse_historical_board_stock(stock, board_type=board_type, trade_date=trade_date)
                if parsed:
                    rows.append(parsed)
        return rows

    def _parse_historical_board_stock(self, stock: list[Any], *, board_type: int, trade_date: str) -> dict[str, Any] | None:
        if not isinstance(stock, list) or len(stock) < 23:
            return None
        return {
            "trade_date": trade_date,
            "code": str(stock[0]),
            "name": str(stock[1]),
            "board_type": int(board_type),
            "timestamp": stock[4] if len(stock) > 4 else None,
            "reason": str(stock[5] or "") if len(stock) > 5 else "",
            "turnover": self._safe_float(stock[6] if len(stock) > 6 else 0),
            "circulating_market_cap": self._safe_float(stock[7] if len(stock) > 7 else 0),
            "main_buy": self._safe_float(stock[8] if len(stock) > 8 else 0),
            "main_sell": self._safe_float(stock[9] if len(stock) > 9 else 0),
            "main_net_inflow": self._safe_float(stock[10] if len(stock) > 10 else 0),
            "seal_amount": self._safe_float(stock[11] if len(stock) > 11 else 0),
            "concept_tags": str(stock[12] or "") if len(stock) > 12 else "",
            "total_market_cap": self._safe_float(stock[13] if len(stock) > 13 else 0),
            "amplitude": self._safe_float(stock[14] if len(stock) > 14 else 0),
            "consecutive_days": self._safe_int(stock[15] if len(stock) > 15 else board_type, board_type),
            "tips": str(stock[18] or "") if len(stock) > 18 else "",
            "sector_code": str(stock[19] or "") if len(stock) > 19 else "",
            "sector_limit_up_count": self._safe_int(stock[20] if len(stock) > 20 else 0),
            "limit_up_price": self._safe_float(stock[21] if len(stock) > 21 else 0),
            "limit_up_pct": self._safe_float(stock[22] if len(stock) > 22 else 0),
            "raw": stock,
        }

    def normalize_sector_strength_frame(self, trade_date: str, stocks: list[dict[str, Any]]) -> pd.DataFrame:
        trade_date = self._normalize_date(trade_date)
        if not stocks:
            return pd.DataFrame()
        grouped: dict[str, dict[str, Any]] = {}
        for stock in stocks:
            sector_code = str(stock.get("sector_code") or "")
            if not sector_code:
                continue
            sector_name = str(stock.get("reason") or stock.get("concept_tags") or sector_code).split("、")[0].split(",")[0].strip() or sector_code
            row = grouped.setdefault(
                sector_code,
                {
                    "trade_date": trade_date,
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "limit_up_count": 0,
                    "max_consecutive_days": 0,
                    "stock_count": 0,
                    "turnover": 0.0,
                    "main_net_inflow": 0.0,
                    "main_buy": 0.0,
                    "main_sell": 0.0,
                    "seal_amount": 0.0,
                    "source": "kaipanla_daily_limit_performance",
                    "raw_json": "[]",
                    "_raw": [],
                },
            )
            row["limit_up_count"] += 1
            row["stock_count"] += 1
            row["max_consecutive_days"] = max(int(row["max_consecutive_days"]), self._safe_int(stock.get("consecutive_days")))
            row["turnover"] += self._safe_float(stock.get("turnover"))
            row["main_net_inflow"] += self._safe_float(stock.get("main_net_inflow"))
            row["main_buy"] += self._safe_float(stock.get("main_buy"))
            row["main_sell"] += self._safe_float(stock.get("main_sell"))
            row["seal_amount"] += self._safe_float(stock.get("seal_amount"))
            row["_raw"].append(stock)

        rows = []
        for row in grouped.values():
            strength_score = min(100.0, row["limit_up_count"] * 8.0 + row["max_consecutive_days"] * 12.0 + min(20.0, row["seal_amount"] / 1e8 * 4.0))
            capital_score = max(0.0, min(100.0, row["main_net_inflow"] / 1e8 * 8.0 + row["turnover"] / 1e9 * 3.0 + row["seal_amount"] / 1e8 * 3.0))
            row["strength_score"] = round(strength_score, 2)
            row["capital_score"] = round(capital_score, 2)
            row["raw_json"] = self._json(row.pop("_raw"))
            rows.append(row)
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        return frame

    def _recent_weekday_window(self, end_date: str, lookback_days: int) -> list[str]:
        end = datetime.strptime(self._normalize_date(end_date), "%Y-%m-%d")
        dates: list[str] = []
        current = end
        while len(dates) < max(1, int(lookback_days)):
            if current.weekday() < 5:
                dates.append(current.strftime("%Y-%m-%d"))
            current -= timedelta(days=1)
        return list(reversed(dates))

    def get_plate_interval_strength(
        self,
        sector_code: str,
        *,
        end_date: str,
        lookback_days: int = 5,
        sector_name: str = "",
    ) -> dict[str, Any] | None:
        """Fetch 开盘啦“板块/尾盘抢筹/区间强度” by summing daily QJ values.

        The App table's multi-day range (e.g. 2026/06/12-2026/06/18) matches
        the sum of per-trading-day ``GetPlate_Info_QJ`` values for each plate:
        ``List[1]`` strength, ``List[3]`` net amount, and ``List[2]`` turnover.
        """
        sector_code = str(sector_code or "").strip()
        if not sector_code:
            return None
        trade_dates = self._recent_weekday_window(end_date, lookback_days)
        daily_rows: list[dict[str, Any]] = []
        total_strength = 0.0
        total_turnover = 0.0
        total_net_amount = 0.0
        last_rank: int | None = None
        actual_dates: list[str] = []
        for trade_date in trade_dates:
            result = self._post(
                self.HISTORY_URL,
                {
                    "a": "GetPlate_Info_QJ",
                    "c": "ZhiShuRanking",
                    "Date": trade_date,
                    "PlateID": sector_code,
                },
            )
            if not result or result.get("errcode") != "0":
                logger.warning("kaipanla plate interval strength failed %s %s: %s", sector_code, trade_date, result)
                continue
            values = result.get("List") or []
            if not isinstance(values, list) or len(values) < 4:
                continue
            actual_date = self._normalize_date(result.get("Date") or trade_date)
            rank = self._safe_int(values[0], 0)
            strength = self._safe_float(values[1])
            turnover = self._safe_float(values[2])
            net_amount = self._safe_float(values[3])
            total_strength += max(0.0, strength)
            total_turnover += turnover
            total_net_amount += net_amount
            last_rank = rank or last_rank
            actual_dates.append(actual_date)
            daily_rows.append(
                {
                    "trade_date": actual_date,
                    "rank": rank,
                    "strength": strength,
                    "turnover": turnover,
                    "net_amount": net_amount,
                    "raw": result,
                }
            )
        if not daily_rows:
            return None
        return {
            "trade_date": self._normalize_date(end_date),
            "sector_code": sector_code,
            "sector_name": sector_name or sector_code,
            "limit_up_count": 0,
            "max_consecutive_days": 0,
            "stock_count": len(daily_rows),
            "turnover": total_turnover,
            "main_net_inflow": total_net_amount,
            "main_buy": 0.0,
            "main_sell": 0.0,
            "seal_amount": 0.0,
            "strength_score": round(total_strength, 2),
            "capital_score": round(total_net_amount / 1e8, 2),
            "source": "kaipanla_plate_info_qj_interval",
            "raw_json": self._json(
                {
                    "lookback_days": lookback_days,
                    "actual_dates": actual_dates,
                    "last_rank": last_rank,
                    "daily_rows": daily_rows,
                }
            ),
        }

    def sync_sector_strength(
        self,
        trade_date: str,
        *,
        sector_codes: Optional[list[str]] = None,
        sector_names: Optional[dict[str, str]] = None,
        lookback_days: int = 5,
    ) -> pd.DataFrame:
        """Sync App “区间强度/区间净额/区间成交” rows into DuckDB."""
        sector_codes = [str(code).strip() for code in (sector_codes or []) if str(code).strip()]
        sector_names = sector_names or {}
        rows = []
        for code in sector_codes:
            row = self.get_plate_interval_strength(
                code,
                end_date=trade_date,
                lookback_days=lookback_days,
                sector_name=sector_names.get(code, ""),
            )
            if row:
                rows.append(row)
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
            self.store.upsert_kaipanla_sector_strength(frame)
        return frame

    def get_sector_all_stocks(
        self,
        plate_id: str,
        *,
        trade_date: Optional[str] = None,
        order: int = 1,
        page_size: int = 30,
        max_pages: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch 开盘啦板块完整股票池 via ``ZhiShuStockList_W8`` pagination."""
        normalized_date = self._normalize_date(trade_date)
        plate_id = str(plate_id or "").strip()
        if not plate_id:
            return {"trade_date": normalized_date, "plate_id": "", "stocks": [], "total_count": 0, "pages_fetched": 0}

        all_stocks: list[list[Any]] = []
        seen_codes: set[str] = set()
        core_stock_codes: list[str] = []
        core_count = 0
        total_count_from_api = 0
        page = 0
        page_size = max(1, int(page_size))

        while True:
            if max_pages is not None and page >= max_pages:
                break
            index = page * page_size
            result = self._post(
                self.HISTORY_URL,
                {
                    "Order": str(order),
                    "TSZB": "0",
                    "a": "ZhiShuStockList_W8",
                    "st": str(page_size),
                    "c": "ZhiShuRanking",
                    "old": "1",
                    "IsZZ": "0",
                    "Token": "0daffc...ff02",
                    "Index": str(index),
                    "Date": normalized_date,
                    "Type": "6",
                    "IsKZZType": "0",
                    "UserID": "4315515",
                    "PlateID": plate_id,
                    "TSZB_Type": "0",
                    "filterType": "0",
                },
            )
            if not result or result.get("errcode") != "0":
                logger.warning("kaipanla sector stocks failed plate=%s date=%s index=%s: %s", plate_id, normalized_date, index, result)
                break

            stocks = result.get("list") or []
            count = self._safe_int(result.get("Count"), 0)
            if page == 0:
                core_stock_codes = [str(code) for code in (result.get("Stocks") or [])]
                core_count = count
            elif page == 1:
                total_count_from_api = count

            if not stocks:
                break
            for stock in stocks:
                if not isinstance(stock, list) or not stock:
                    continue
                code = str(stock[0] or "").strip()
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                all_stocks.append(stock)
            page += 1
            if len(stocks) < page_size:
                break

        return {
            "trade_date": normalized_date,
            "plate_id": plate_id,
            "stocks": all_stocks,
            "stock_codes": sorted(seen_codes),
            "core_stock_codes": core_stock_codes,
            "core_count": core_count,
            "total_count": len(all_stocks),
            "total_count_from_api": total_count_from_api or core_count,
            "pages_fetched": page,
        }

    def get_market_limit_up_ladder(self, trade_date: Optional[str] = None) -> dict[str, Any]:
        is_realtime = trade_date is None
        if is_realtime:
            url = self.REALTIME_URL
            payload = {"a": "GetYTFP_SCTD", "c": "FuPanLa"}
            display_date = datetime.now().strftime("%Y-%m-%d")
        else:
            url = self.HISTORY_URL
            display_date = self._normalize_date(trade_date)
            payload = {"a": "GetYTFP_SCTD", "c": "FuPanLa", "Date": display_date}
        result = self._post(url, payload)
        if not result or result.get("errcode") != "0":
            logger.warning("kaipanla ladder failed: %s", result)
            return {"date": display_date, "is_realtime": is_realtime, "ladder": {}, "broken_stocks": [], "height_marks": [], "statistics": {}, "raw": result or {}}
        ladder: dict[int, list[dict[str, Any]]] = {}
        broken_stocks = []
        height_marks = []
        for group in result.get("List", []) or []:
            tip = str(group.get("Tip", "1"))
            for stock_data in group.get("Stocks", []) or []:
                stock = {
                    "code": str(stock_data.get("StockID", "")),
                    "name": str(stock_data.get("Name", "")),
                    "tips": str(stock_data.get("Tips", "")),
                    "raw": stock_data,
                }
                if tip == "0":
                    stock["consecutive_days"] = 0
                    stock["is_broken"] = True
                    broken_stocks.append(stock)
                elif tip == "9":
                    stock["consecutive_days"] = 0
                    stock["is_height_mark"] = True
                    height_marks.append(stock)
                else:
                    days = self._safe_int(tip, 1)
                    stock["consecutive_days"] = days
                    ladder.setdefault(days, []).append(stock)
        return {
            "date": self._normalize_date(result.get("Date", display_date)),
            "is_realtime": is_realtime,
            "ladder": ladder,
            "broken_stocks": broken_stocks,
            "height_marks": height_marks,
            "statistics": {
                "total_limit_up": sum(len(items) for items in ladder.values()),
                "max_consecutive": max(ladder.keys()) if ladder else 0,
                "ladder_distribution": {str(k): len(v) for k, v in ladder.items()},
            },
            "raw": result,
        }

    def normalize_market_sentiment_frame(self, data: dict[str, Any]) -> pd.DataFrame:
        if not data:
            return pd.DataFrame()
        frame = pd.DataFrame([data])
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        return frame

    def normalize_limit_up_frames(self, payload: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not payload:
            return pd.DataFrame(), pd.DataFrame()
        trade_date = self._normalize_date((payload.get("summary") or {}).get("trade_date"))
        sector_rows = []
        stock_rows = []
        for sector in payload.get("sectors", []) or []:
            sector_code = sector.get("sector_code", "")
            sector_name = sector.get("sector_name", "")
            sector_rows.append({
                "trade_date": trade_date,
                "sector_code": sector_code,
                "sector_name": sector_name,
                "stock_count": self._safe_int(sector.get("stock_count")),
                "source": "kaipanla",
                "raw_json": self._json(sector.get("raw", sector)),
            })
            for stock in sector.get("stocks", []) or []:
                row = {
                    "trade_date": trade_date,
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "code": stock.get("code", ""),
                    "name": stock.get("name", ""),
                    "limit_up_price": stock.get("limit_up_price", 0.0),
                    "turnover": stock.get("turnover", 0.0),
                    "circulating_market_cap": stock.get("circulating_market_cap", 0.0),
                    "total_market_cap": stock.get("total_market_cap", 0.0),
                    "consecutive_days": stock.get("consecutive_days", 0),
                    "consecutive_count": stock.get("consecutive_count", 0),
                    "concept_tags": stock.get("concept_tags", ""),
                    "theme": stock.get("theme", ""),
                    "reason": stock.get("reason", ""),
                    "seal_amount": stock.get("seal_amount", 0.0),
                    "main_net_inflow": stock.get("main_net_inflow", 0.0),
                    "first_limit_up_time": stock.get("first_limit_up_time", ""),
                    "is_first_board": stock.get("is_first_board", 0),
                    "source": "kaipanla",
                    "raw_json": self._json(stock.get("raw", stock)),
                }
                stock_rows.append(row)
        sectors_df = pd.DataFrame(sector_rows)
        stocks_df = pd.DataFrame(stock_rows)
        for frame in (sectors_df, stocks_df):
            if not frame.empty:
                frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        return sectors_df, stocks_df

    def normalize_ladder_frame(self, payload: dict[str, Any]) -> pd.DataFrame:
        if not payload:
            return pd.DataFrame()
        trade_date = self._normalize_date(payload.get("date"))
        rows = []
        for days, stocks in (payload.get("ladder") or {}).items():
            for stock in stocks:
                rows.append(self._ladder_row(trade_date, stock, int(days), False, False))
        for stock in payload.get("broken_stocks", []) or []:
            rows.append(self._ladder_row(trade_date, stock, self._safe_int(stock.get("consecutive_days")), True, False))
        for stock in payload.get("height_marks", []) or []:
            rows.append(self._ladder_row(trade_date, stock, self._safe_int(stock.get("consecutive_days")), False, True))
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        return frame

    def _ladder_row(self, trade_date: str, stock: dict[str, Any], days: int, is_broken: bool, is_height_mark: bool) -> dict[str, Any]:
        return {
            "trade_date": trade_date,
            "code": stock.get("code") or stock.get("stock_code") or "",
            "name": stock.get("name") or stock.get("stock_name") or "",
            "consecutive_days": days,
            "tips": stock.get("tips", ""),
            "is_broken": bool(is_broken),
            "is_height_mark": bool(is_height_mark),
            "source": "kaipanla",
            "raw_json": self._json(stock.get("raw", stock)),
        }

    def sync_trade_date(self, trade_date: Optional[str] = None, *, include_ladder: bool = True) -> dict[str, Any]:
        trade_date = self._normalize_date(trade_date)
        market = self.get_daily_market_sentiment(trade_date)
        market_df = self.normalize_market_sentiment_frame(market)
        self.store.upsert_kaipanla_market_sentiment(market_df)

        limit_up = self.get_limit_up_sectors(trade_date)
        sectors_df, stocks_df = self.normalize_limit_up_frames(limit_up)
        self.store.upsert_kaipanla_limit_up(sectors_df, stocks_df)

        ladder_rows = 0
        if include_ladder:
            ladder = self.get_market_limit_up_ladder(trade_date)
            ladder_df = self.normalize_ladder_frame(ladder)
            self.store.upsert_kaipanla_limit_up_ladder(ladder_df)
            ladder_rows = len(ladder_df)

        return {
            "trade_date": trade_date,
            "market_rows": len(market_df),
            "sector_rows": len(sectors_df),
            "stock_rows": len(stocks_df),
            "ladder_rows": ladder_rows,
        }

    def get_cached_hot_stock_map(self, trade_date: Optional[str] = None, *, min_consecutive_days: int = 0) -> dict[str, dict[str, Any]]:
        trade_date = trade_date or self.store.get_latest_kaipanla_limit_up_trade_date() or self.store.get_latest_kaipanla_trade_date()
        df = self.store.get_kaipanla_limit_up_stocks(trade_date=trade_date, min_consecutive_days=min_consecutive_days)
        if df is None or df.empty:
            return {}
        stock_map: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            code = str(row.get("code") or "")
            if not code:
                continue
            consecutive_days = self._safe_int(row.get("consecutive_days"))
            seal_amount = self._safe_float(row.get("seal_amount"))
            sector_count = self._safe_int(row.get("stock_count"))
            score = min(100.0, 45.0 + consecutive_days * 12.0 + min(25.0, seal_amount / 1e8 * 5.0) + min(10.0, sector_count))
            candidate = {
                "theme": row.get("reason") or row.get("theme") or row.get("sector_name") or "开盘啦涨停",
                "theme_heat_score": round(score, 2),
                "theme_limit_up_count": None,
                "theme_max_limit_up_days": consecutive_days,
                "stock_name": row.get("name"),
                "stock_limit_up_days": consecutive_days,
                "stock_first_limit_up_time": row.get("first_limit_up_time"),
                "stock_sealed_amount": seal_amount,
                "stock_break_board_count": 0,
                "kaipanla_trade_date": str(row.get("trade_date"))[:10],
                "kaipanla_sector_code": row.get("sector_code"),
                "kaipanla_sector_name": row.get("sector_name"),
                "kaipanla_reason": row.get("reason"),
                "kaipanla_concept_tags": row.get("concept_tags"),
            }
            current = stock_map.get(code)
            if current is None or candidate["theme_heat_score"] > current["theme_heat_score"]:
                stock_map[code] = candidate
        return stock_map


kaipanla_provider = KaipanlaProvider()
