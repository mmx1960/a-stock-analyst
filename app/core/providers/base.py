from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd


class BaseMarketDataProvider(ABC):
    """统一市场数据 Provider 抽象接口。"""

    @abstractmethod
    def get_stock_list(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_realtime_quote(self, code: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_daily_bars(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "hfq",
    ) -> Optional[pd.DataFrame]:
        raise NotImplementedError

    @abstractmethod
    def get_minute_bars(
        self,
        code: str,
        period: str = "5",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        raise NotImplementedError
