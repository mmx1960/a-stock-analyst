from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

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

    def get_stock_industry_hierarchy(self, code: str):
        """Fetch stock industry hierarchy from the underlying 通达信 API.

        mootdx/pytdx installations differ in the exact method exposed for this
        relatively low-frequency classification data, so this wrapper keeps the
        adapter boundary in one place and accepts any DataFrame/list/dict shape
        that the source normalizer can consume.
        """

        client = self._ensure_client()
        code = self.normalize_code(code)
        block_frame = self._get_stock_industry_from_block_api(client, code)
        if block_frame is not None and not block_frame.empty:
            return block_frame

        method_names = (
            "stock_industry_hierarchy",
            "get_stock_industry_hierarchy",
            "stock_industry",
            "get_stock_industry",
            "stock_block",
            "get_stock_block",
            "get_security_blocks",
            "industry",
            "get_industry",
        )
        tried: list[str] = []
        for method_name in method_names:
            method = getattr(client, method_name, None)
            if method is None:
                continue
            for args, kwargs in (
                ((code,), {}),
                ((), {"code": code}),
                ((), {"symbol": code}),
            ):
                tried.append(f"{method_name}{args or kwargs}")
                try:
                    result = method(*args, **kwargs)
                except TypeError:
                    continue
                if result is not None:
                    return result
        raise RuntimeError(
            "mootdx client does not expose a supported stock industry hierarchy API; "
            f"tried: {', '.join(tried) or ', '.join(method_names)}"
        )

    def _get_stock_industry_from_block_api(self, client: Any, code: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        block_method = getattr(client, "block", None)
        if block_method is not None:
            for block_file in ("block.dat", "block_zs.dat"):
                try:
                    payload = block_method(tofile=block_file)
                except TypeError:
                    try:
                        payload = block_method(block_file)
                    except TypeError:
                        continue
                rows.extend(self._extract_stock_industry_block_rows(payload, code=code, block_file=block_file))

        raw_client = getattr(client, "client", None)
        parse_method = getattr(raw_client, "get_and_parse_block_info", None)
        if parse_method is not None:
            for block_file in ("block.dat", "block_zs.dat"):
                try:
                    payload = parse_method(block_file)
                except TypeError:
                    continue
                rows.extend(self._extract_stock_industry_block_rows(payload, code=code, block_file=block_file))

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).drop_duplicates(subset=["sector_code", "sector_type"])

    @classmethod
    def _extract_stock_industry_block_rows(cls, payload: Any, *, code: str, block_file: str) -> list[dict[str, Any]]:
        frame = cls._payload_to_frame(payload)
        if frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            raw = row.to_dict()
            if not cls._block_row_contains_code(raw, code):
                continue
            level = cls._infer_industry_level(raw, block_file)
            if level not in {1, 2, 3}:
                continue
            sector_name = cls._first_text(raw, ("sector_name", "industry_name", "blockname", "block_name", "name", "板块名称", "行业名称"))
            if not sector_name:
                continue
            sector_code = cls._first_text(raw, ("sector_code", "industry_code", "blockcode", "block_code", "板块代码", "行业代码")) or f"tdx:{level}:{sector_name}"
            raw["tdx_block_file"] = block_file
            rows.append(
                {
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "sector_type": f"tdx_industry_l{level}",
                    "source": "tdx",
                    "raw_json": raw,
                }
            )
        return rows

    @classmethod
    def _block_row_contains_code(cls, raw: dict[str, Any], code: str) -> bool:
        normalized = cls.normalize_code(code)
        for key in ("code", "stock_code", "symbol", "证券代码", "股票代码"):
            value = raw.get(key)
            if cls.normalize_code(value) == normalized:
                return True
        for key in ("codes", "code_list", "stocks", "symbols", "证券列表", "股票列表"):
            value = raw.get(key)
            if isinstance(value, (list, tuple, set)) and normalized in {cls.normalize_code(item) for item in value}:
                return True
            if isinstance(value, str):
                parts = value.replace(",", " ").replace(";", " ").replace("|", " ").split()
                if normalized in {cls.normalize_code(item) for item in parts}:
                    return True
        return False

    @classmethod
    def _infer_industry_level(cls, raw: dict[str, Any], block_file: str) -> int:
        for key in ("sector_type", "industry_type", "block_type", "blocktype", "type", "category", "分类", "类别"):
            text = cls._clean_text(raw.get(key))
            lowered = text.lower()
            if "tdx_industry_l3" in lowered or "三级" in text or "细分" in text or lowered in {"l3", "3"}:
                return 3
            if "tdx_industry_l2" in lowered or "二级" in text or lowered in {"l2", "2"}:
                return 2
            if "tdx_industry_l1" in lowered or "一级" in text or lowered in {"l1", "1"}:
                return 1
        for key in ("level", "industry_level", "sector_level", "层级", "级别"):
            text = cls._clean_text(raw.get(key)).lower()
            if text in {"3", "l3"}:
                return 3
            if text in {"2", "l2"}:
                return 2
            if text in {"1", "l1"}:
                return 1
        block_name = cls._first_text(raw, ("blockname", "block_name", "sector_name", "industry_name", "板块名称", "行业名称"))
        block_kind = cls._first_text(raw, ("block_type", "blocktype", "type", "category", "分类", "类别"))
        if any(token in f"{block_name}{block_kind}" for token in ("三级行业", "二级行业", "一级行业", "细分行业")):
            return 3 if "三级" in f"{block_name}{block_kind}" or "细分" in f"{block_name}{block_kind}" else 2 if "二级" in f"{block_name}{block_kind}" else 1
        return 0

    @staticmethod
    def _payload_to_frame(payload: Any) -> pd.DataFrame:
        if payload is None:
            return pd.DataFrame()
        if isinstance(payload, pd.DataFrame):
            return payload.copy()
        if isinstance(payload, pd.Series):
            return pd.DataFrame([payload.to_dict()])
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, tuple):
            return pd.DataFrame(list(payload))
        if isinstance(payload, dict):
            return pd.DataFrame([payload])
        return pd.DataFrame()

    @staticmethod
    def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = MootdxProvider._clean_text(raw.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except TypeError:
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "nat"} else text

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
