from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class BigAmapProvider:
    """BigAmap / 开盘啦公开数据接口。

    BigAmap 前端使用 `https://bigamap.cn/api/v1/public/...`，其中部分数据来自开盘啦概念/涨停复盘。
    这里仅接入无需登录的公开端点，作为题材热度、涨停梯队、板块强度的候选数据源。
    """

    BASE_URL = "https://bigamap.cn/api/v1"

    def __init__(self, timeout: int = 20, min_interval: float = 0.5):
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request_at = 0.0
        self._cookie_jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))
        self._authenticated = False

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.time()

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.BASE_URL}{path}{query}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://bigamap.cn/",
            },
        )
        with self._opener.open(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": "https://bigamap.cn/",
            },
            method="POST",
        )
        with self._opener.open(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _extract_svg_captcha_code(image_data_url: str) -> str:
        raw = urllib.parse.unquote(image_data_url or "")
        svg = raw.split(",", 1)[1] if "," in raw else raw
        texts = re.findall(r"<text[^>]*>(.*?)</text>", svg, flags=re.S)
        return re.sub(r"\s+", "", texts[-1]) if texts else ""

    def login(self, email: str, password: str) -> dict[str, Any]:
        """登录 BigAmap，凭据由调用方提供；不要在项目文件中硬编码。"""
        last_error: Exception | None = None
        for _ in range(3):
            captcha = self._request_json("/web/auth/captcha")
            captcha_code = self._extract_svg_captcha_code(captcha.get("image_data_url", ""))
            payload = {
                "email": email,
                "password": password,
                "captcha_id": captcha.get("captcha_id"),
                "captcha_code": captcha_code,
                "purpose": "login",
            }
            try:
                result = self._post_json("/web/auth/login", payload)
                self._authenticated = bool(result.get("user"))
                return result
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code != 401:
                    raise
        if last_error:
            raise last_error
        raise RuntimeError("BigAmap login failed")

    def login_from_env(self) -> bool:
        email = os.getenv("BIGAMAP_EMAIL")
        password = os.getenv("BIGAMAP_PASSWORD")
        if not email or not password or self._authenticated:
            return self._authenticated
        self.login(email, password)
        return self._authenticated

    def get_limit_up_review(self) -> dict[str, Any]:
        """涨停复盘：涨停、跌停、炸板、昨日涨停、强势股。"""
        return self._request_json("/public/map/limit-up-review")

    def get_stock_abnormal(self) -> dict[str, Any]:
        """异动 / 严重异动监控。"""
        return self._request_json("/public/map/stock-abnormal")

    def get_board_treemap(self) -> dict[str, Any]:
        """行业/板块树图，含分层板块与成分股涨跌。"""
        return self._request_json("/public/map/boards/treemap")

    def get_board_rankings(self) -> dict[str, Any]:
        """板块轮动排行。游客可见最近若干交易日；登录后可见完整 15 日。"""
        self.login_from_env()
        return self._request_json("/public/map/boards/maximized-rankings")

    def get_board_constituent_rankings(self, requests: list[dict[str, str]]) -> dict[str, Any]:
        """按交易日 + 板块 code 获取板块内股票排名。"""
        return self._post_json("/public/map/boards/constituent-rankings", {"requests": requests})

    def get_repeated_top_hot_boards(
        self,
        payload: dict[str, Any] | None = None,
        *,
        lookback_days: int = 10,
        top_n: int = 10,
        min_appearances: int = 3,
        include_rolling_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        """统计近 N 个可见交易日里进入前 top_n 次数达到阈值的板块。

        BigAmap 游客态通常只暴露 `daily_rankings` 最近 3 天明细；若不足 lookback_days，
        可用 `rolling_rankings.top7` 作为扩大股票池的降级补充，并在 `source` 字段标记。
        """
        payload = payload or self.get_board_rankings()
        daily_rankings = payload.get("daily_rankings", []) or []
        visible_days = daily_rankings[:lookback_days]
        appearances: Counter[str] = Counter()
        strengths: defaultdict[str, float] = defaultdict(float)
        board_names: dict[str, str] = {}
        trade_dates: defaultdict[str, list[str]] = defaultdict(list)

        for day in visible_days:
            trade_date = day.get("trade_date")
            for board in (day.get("boards") or [])[:top_n]:
                code = str(board.get("board_code") or "")
                if not code:
                    continue
                appearances[code] += 1
                strengths[code] += float(board.get("strength") or 0)
                board_names[code] = str(board.get("board_name") or code)
                if trade_date:
                    trade_dates[code].append(str(trade_date))

        result: dict[str, dict[str, Any]] = {}
        for code, count in appearances.items():
            if count >= min_appearances:
                result[code] = {
                    "board_code": code,
                    "board_name": board_names.get(code, code),
                    "top_n_appearances": count,
                    "visible_days": len(visible_days),
                    "requested_lookback_days": lookback_days,
                    "total_strength": strengths[code],
                    "trade_dates": trade_dates[code],
                    "source": "daily_rankings",
                    "data_scope": payload.get("access_scope", "unknown"),
                }

        if include_rolling_fallback and len(visible_days) < lookback_days:
            rolling = payload.get("rolling_rankings", {}) or {}
            for board in (rolling.get("top7") or [])[:30]:
                code = str(board.get("board_code") or "")
                if not code or code in result:
                    continue
                result[code] = {
                    "board_code": code,
                    "board_name": str(board.get("board_name") or code),
                    "top_n_appearances": None,
                    "visible_days": len(visible_days),
                    "requested_lookback_days": lookback_days,
                    "total_strength": float(board.get("total_score") or 0),
                    "trade_dates": [],
                    "source": "rolling_top7_fallback_guest_limited",
                    "data_scope": payload.get("access_scope", "unknown"),
                }

        boards = list(result.values())
        boards.sort(key=lambda x: (x.get("top_n_appearances") or 0, x.get("total_strength") or 0), reverse=True)
        return boards

    def get_repeated_hot_board_stock_map(
        self,
        *,
        lookback_days: int = 10,
        top_n: int = 10,
        min_appearances: int = 3,
        max_boards: int = 20,
        max_stocks_per_board: int = 80,
    ) -> dict[str, dict[str, Any]]:
        """把反复进入热点前列的板块扩展为股票池。"""
        rankings = self.get_board_rankings()
        boards = self.get_repeated_top_hot_boards(
            rankings,
            lookback_days=lookback_days,
            top_n=top_n,
            min_appearances=min_appearances,
        )[:max_boards]
        latest_trade_date = rankings.get("latest_trade_date")
        requests = [
            {"trade_date": latest_trade_date, "board_code": board["board_code"]}
            for board in boards
            if latest_trade_date and board.get("board_code")
        ]
        stock_map: dict[str, dict[str, Any]] = {}
        if not requests:
            return stock_map
        constituents = self.get_board_constituent_rankings(requests)
        board_meta = {board["board_code"]: board for board in boards}
        for item in constituents.get("items", []) or []:
            board_code = str(item.get("board_code") or "")
            meta = board_meta.get(board_code, {})
            for stock in (item.get("items") or [])[:max_stocks_per_board]:
                code = str(stock.get("stock_code") or "")
                name = str(stock.get("stock_name") or "")
                if not code or "ST" in name or "退" in name:
                    continue
                candidate = {
                    "theme": item.get("board_name") or meta.get("board_name") or board_code,
                    "board_code": board_code,
                    "board_source": meta.get("source"),
                    "board_top_n_appearances": meta.get("top_n_appearances"),
                    "board_visible_days": meta.get("visible_days"),
                    "board_requested_lookback_days": meta.get("requested_lookback_days"),
                    "board_total_strength": meta.get("total_strength"),
                    "stock_name": name,
                    "stock_rank_in_board": stock.get("rank"),
                    "stock_board_change_percent": stock.get("change_percent"),
                    "stock_limit_up_days_text": stock.get("continuous_limit_up_count"),
                    "theme_heat_score": min(100.0, float(meta.get("total_strength") or 0) / 1000.0),
                }
                current = stock_map.get(code)
                if current is None or (candidate.get("theme_heat_score") or 0) > (current.get("theme_heat_score") or 0):
                    stock_map[code] = candidate
        return stock_map

    def search_kaipanla_concepts(self, query: str) -> dict[str, Any]:
        """开盘啦概念搜索，用于题材词扩展。"""
        return self._request_json("/public/stocks/screener/filters/kaipanla-concepts", {"q": query})

    def extract_limit_up_theme_stats(self, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """从涨停复盘中聚合行业/申万一级强度。"""
        payload = payload or self.get_limit_up_review()
        items = payload.get("limit_up", {}).get("items", []) or []
        stats: dict[str, dict[str, Any]] = {}
        for item in items:
            theme = item.get("sw_level1_name") or item.get("industry") or "UNKNOWN"
            entry = stats.setdefault(
                theme,
                {
                    "theme": theme,
                    "limit_up_count": 0,
                    "one_word_count": 0,
                    "total_sealed_amount": 0.0,
                    "max_limit_up_days": 0,
                    "stocks": [],
                },
            )
            entry["limit_up_count"] += 1
            entry["total_sealed_amount"] += float(item.get("sealed_amount") or 0)
            entry["max_limit_up_days"] = max(entry["max_limit_up_days"], int(item.get("limit_up_days") or 0))
            if item.get("first_limit_up_time") == "09:25" and item.get("break_board_count") in (None, 0):
                entry["one_word_count"] += 1
            entry["stocks"].append(
                {
                    "code": item.get("stock_code"),
                    "name": item.get("stock_name"),
                    "industry": item.get("industry"),
                    "limit_up_days": item.get("limit_up_days"),
                    "first_limit_up_time": item.get("first_limit_up_time"),
                    "sealed_amount": item.get("sealed_amount"),
                    "break_board_count": item.get("break_board_count"),
                }
            )
        result = list(stats.values())
        result.sort(
            key=lambda x: (
                x["limit_up_count"],
                x["max_limit_up_days"],
                x["one_word_count"],
                x["total_sealed_amount"],
            ),
            reverse=True,
        )
        return result


bigamap_provider = BigAmapProvider()
