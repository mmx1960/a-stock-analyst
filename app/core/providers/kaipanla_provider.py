from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
import urllib3

from app.core.storage.duckdb_store import DuckDBStore

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


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
            return {"summary": {}, "sectors": [], "raw": result or {}}
        nums = result.get("nums", {}) or {}
        summary = {
            "trade_date": self._normalize_date(result.get("date", trade_date)),
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
