from __future__ import annotations

from typing import Optional

import akshare as ak
import pandas as pd

from app.core.providers.base import BaseMarketDataProvider


class AkshareProvider(BaseMarketDataProvider):
    """低频补充 provider。"""

    def get_stock_list(self) -> list[dict]:
        try:
            df = ak.stock_info_a_code_name()
            if df is None or df.empty:
                return []
            return [
                {
                    "code": str(row.get("code", "")).strip(),
                    "name": str(row.get("name", "")).strip(),
                    "source": "akshare",
                }
                for _, row in df.iterrows()
            ]
        except Exception:
            return []

    def get_realtime_quote(self, code: str) -> Optional[dict]:
        return None

    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        try:
            return ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        except Exception:
            return pd.DataFrame()

    def get_minute_bars(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        return pd.DataFrame()
