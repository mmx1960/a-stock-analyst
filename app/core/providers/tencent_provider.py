from __future__ import annotations

from typing import Optional

import requests

from app.core.config import TENCENT_CONFIG


class TencentProvider:
    """腾讯财经补充字段 provider。"""

    def __init__(self):
        self.base_url = TENCENT_CONFIG["base_url"]
        self.timeout = TENCENT_CONFIG["timeout"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        })

    @staticmethod
    def normalize_code(code: str) -> str:
        code = str(code).strip().replace("sh", "").replace("sz", "").replace("bj", "")
        prefix = "sh" if code.startswith("6") else "sz"
        return f"{prefix}{code}"

    @staticmethod
    def _safe_float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def get_realtime_quote_extra(self, code: str) -> Optional[dict]:
        symbol = self.normalize_code(code)
        url = f"{self.base_url}/q={symbol}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        text = response.text.strip()
        if '="' not in text:
            return None
        body = text.split('="', 1)[1].rsplit('";', 1)[0]
        parts = body.split('~')
        if len(parts) < 58:
            return None
        return {
            "pe": self._safe_float(parts[39]),
            "pb": self._safe_float(parts[46]),
            "market_cap": self._safe_float(parts[44]) * 1e8,
            "circulating_cap": self._safe_float(parts[45]) * 1e8,
            "turnover_rate": self._safe_float(parts[38]),
            "volume_ratio": self._safe_float(parts[49]),
            "amplitude": self._safe_float(parts[43]),
            "source_extra": "tencent",
        }
