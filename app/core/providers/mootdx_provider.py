from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from app.core.config import MOOTDX_CONFIG
from app.core.providers.base import BaseMarketDataProvider

try:
    from mootdx.quotes import Quotes
except Exception:  # pragma: no cover
    Quotes = None


class MootdxProvider(BaseMarketDataProvider):
    """mootdx 主行情 / K线 provider。"""

    DAILY_FREQ_MAP = {"daily": 9, "weekly": 5, "monthly": 6}
    MINUTE_FREQ_MAP = {"1": 8, "5": 0, "15": 1, "30": 2, "60": 3}
    ALLOWED_A_SHARE_PREFIXES = (
        "000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "830", "831", "832", "833", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879",
    )
    EXCLUDED_NAME_KEYWORDS = (
        "指数", "板块", "基金", "LOF", "ETF", "ＥＴＦ", "ＬＯＦ", "转债", "债", "回购", "ABS", "REITS", "权证", "成交", "Ｂ股", "B股", "国债", "期权", "分级", "货币", "沪港通", "深港通", "债券",
    )

    def __init__(self):
        self.host = MOOTDX_CONFIG["host"]
        self.port = MOOTDX_CONFIG["port"]
        self.bestip_timeout = MOOTDX_CONFIG["bestip_timeout"]
        self.client = None
        if Quotes is not None:
            try:
                self.client = Quotes.factory(market="std", multithread=True, timeout=self.bestip_timeout)
            except Exception:
                self.client = None

    @staticmethod
    def normalize_code(code: str) -> str:
        code = str(code).strip()
        if code.startswith(("sh", "sz", "bj")):
            return code[2:]
        return code

    @staticmethod
    def _market_from_code(code: str) -> str:
        code = str(code)
        if code.startswith(("6", "9")):
            return "sh"
        if code.startswith(("0", "2", "3")):
            return "sz"
        if code.startswith(("4", "8")):
            return "bj"
        return "unknown"

    @classmethod
    def _is_a_share_code(cls, code: str) -> bool:
        code = str(code).zfill(6)
        return any(code.startswith(prefix) for prefix in cls.ALLOWED_A_SHARE_PREFIXES)

    @classmethod
    def _is_valid_stock_name(cls, name: str) -> bool:
        text = str(name or "").replace("\x00", "").strip()
        if not text:
            return False
        return not any(keyword in text for keyword in cls.EXCLUDED_NAME_KEYWORDS)

    def _ensure_client(self):
        if self.client is None:
            raise RuntimeError("mootdx client unavailable")
        return self.client

    def get_stock_list(self) -> list[dict]:
        client = self._ensure_client()
        df = client.stock_all()
        if df is None or df.empty:
            return []
        df = df.copy()
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["name"] = df["name"].astype(str).str.replace("\x00", "", regex=False).str.strip()
        df = df[df["code"].apply(self._is_a_share_code) & df["name"].apply(self._is_valid_stock_name)]
        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("code", "")).zfill(6)
            stocks.append(
                {
                    "code": code,
                    "name": str(row.get("name", "")).strip(),
                    "market": "A",
                    "exchange": self._market_from_code(code),
                    "list_date": None,
                    "status": "active",
                    "source": "mootdx",
                }
            )
        return stocks

    def get_realtime_quote(self, code: str) -> Optional[dict]:
        client = self._ensure_client()
        code = self.normalize_code(code)
        df = client.quotes([code])
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        price = float(row.get("price", 0) or 0)
        prev_close = float(row.get("last_close", 0) or 0)
        change_amt = price - prev_close
        change_pct = (change_amt / prev_close * 100) if prev_close else 0.0
        return {
            "code": code,
            "name": str(row.get("name", "")).strip() if "name" in row.index else "",
            "price": price,
            "change_pct": change_pct,
            "change_amt": change_amt,
            "volume": float(row.get("volume", 0) or 0),
            "turnover": float(row.get("amount", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "open": float(row.get("open", 0) or 0),
            "prev_close": prev_close,
            "bid1": float(row.get("bid1", 0) or 0),
            "ask1": float(row.get("ask1", 0) or 0),
            "bid_vol1": float(row.get("bid_vol1", 0) or 0),
            "ask_vol1": float(row.get("ask_vol1", 0) or 0),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_main": "mootdx",
        }

    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        client = self._ensure_client()
        code = self.normalize_code(code)
        start = self._normalize_ymd(start_date)
        end = self._normalize_ymd(end_date)
        if adjust == "hfq":
            try:
                df = client.get_k_data(code, start, end)
            except Exception:
                df = pd.DataFrame()
        else:
            df = client.bars(symbol=code, frequency=self.DAILY_FREQ_MAP["daily"], start=0, offset=800)
        if df is None or df.empty:
            return pd.DataFrame()
        frame = df.copy().reset_index(drop=True)
        if "date" not in frame.columns and "datetime" in frame.columns:
            frame["date"] = pd.to_datetime(frame["datetime"]).dt.date
        elif "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame["trade_date"] = pd.to_datetime(frame["date"]).dt.date
        frame["volume"] = pd.to_numeric(frame.get("volume", frame.get("vol")), errors="coerce")
        frame["amount"] = pd.to_numeric(frame.get("amount"), errors="coerce")
        frame["code"] = code
        frame["source"] = "mootdx"
        if start_date:
            frame = frame[frame["trade_date"] >= pd.to_datetime(self._normalize_compact_date(start_date)).date()]
        if end_date:
            frame = frame[frame["trade_date"] <= pd.to_datetime(self._normalize_compact_date(end_date)).date()]
        return frame.reset_index(drop=True)

    def get_minute_bars(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        client = self._ensure_client()
        code = self.normalize_code(code)
        freq = self.MINUTE_FREQ_MAP.get(str(period), 0)
        df = client.bars(symbol=code, frequency=freq, start=0, offset=800)
        if df is None or df.empty:
            return pd.DataFrame()
        frame = df.copy().reset_index(drop=True)
        if "datetime" in frame.columns:
            frame["datetime"] = pd.to_datetime(frame["datetime"])
        frame["code"] = code
        frame["period"] = str(period)
        frame["source"] = "mootdx"
        if start_date:
            frame = frame[frame["datetime"] >= pd.to_datetime(start_date)]
        if end_date:
            frame = frame[frame["datetime"] <= pd.to_datetime(end_date)]
        return frame.reset_index(drop=True)

    @staticmethod
    def _normalize_ymd(value: Optional[str]) -> str:
        if not value:
            return "1990-01-01"
        value = str(value)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return value[:10]

    @staticmethod
    def _normalize_compact_date(value: str) -> str:
        value = str(value)
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return value[:10]
