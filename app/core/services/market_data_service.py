from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

import pandas as pd

from app.core.sources import HotspotSource, MarketSource, SectorSource
from app.core.storage.sqlite_store import SQLiteStore


class MarketDataService:
    """Unified data service backed by SQLite and external source adapters."""

    def __init__(
        self,
        *,
        store: SQLiteStore | None = None,
        market_sources: list[MarketSource] | None = None,
        sector_sources: list[SectorSource] | None = None,
        hotspot_sources: list[HotspotSource] | None = None,
    ):
        self.store = store or SQLiteStore()
        self.market_sources = market_sources if market_sources is not None else [self._default_market_source()]
        self.sector_sources = sector_sources if sector_sources is not None else [self._default_sector_source()]
        self.hotspot_sources = hotspot_sources or []

    @staticmethod
    def _default_market_source() -> MarketSource:
        from app.core.sources.direct_sources import DirectMarketSource

        return DirectMarketSource()

    def _default_sector_source(self) -> SectorSource:
        from app.core.sources.tdx_sector_source import TdxSectorSource

        return TdxSectorSource(store=self.store)

    def get_stock_list(self, *, refresh: bool = False, limit: int | None = None) -> pd.DataFrame:
        local = self.store.get_stock_basic(limit=limit)
        min_rows = 1 if limit else 1000
        if not refresh and local is not None and not local.empty and len(local) >= min_rows:
            return local
        summary = self.sync_stock_list(limit=limit or 0)
        if summary.get("rows_written", 0) > 0:
            return self.store.get_stock_basic(limit=limit)
        return local

    def get_realtime_quote(self, code: str, *, refresh: bool = False) -> dict[str, Any] | None:
        normalized_code = self._normalize_code(code)
        local = self.store.get_realtime_quote_snapshot(normalized_code)
        if not refresh and local is not None and not local.empty:
            return local.iloc[0].to_dict()
        rows = self._fetch_realtime_quotes([normalized_code])
        if rows:
            self.store.upsert_realtime_quote_snapshot(pd.DataFrame(rows))
            latest = self.store.get_realtime_quote_snapshot(normalized_code)
            if latest is not None and not latest.empty:
                return latest.iloc[0].to_dict()
        if local is not None and not local.empty:
            return local.iloc[0].to_dict()
        return {"code": normalized_code, "source": "sqlite_empty_quote"}

    def get_kline(
        self,
        code: str,
        *,
        period: str = "d",
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "hfq",
        refresh_missing: bool = True,
    ) -> pd.DataFrame:
        normalized_code = self._normalize_code(code)
        normalized_period = self._normalize_period(period)
        local = self.store.get_kline_bars(
            normalized_code,
            period=normalized_period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if local is not None and not local.empty:
            return local
        if not refresh_missing:
            return local
        for source in self.market_sources:
            frame = source.fetch_kline(
                normalized_code,
                normalized_period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            normalized = self._normalize_kline_frame(
                frame,
                code=normalized_code,
                period=normalized_period,
                adjust=adjust,
                source=getattr(source, "name", "unknown"),
            )
            if normalized.empty:
                continue
            self.store.upsert_kline_bars(normalized)
            return self.store.get_kline_bars(
                normalized_code,
                period=normalized_period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        return pd.DataFrame()

    def get_sector_strength(self, date: str | None = None, *, top_n: int | None = None) -> pd.DataFrame:
        return self.store.get_sector_strength(start_date=date, end_date=date, top_n=top_n)

    def get_stock_sectors(self, code: str, *, current_only: bool = True) -> pd.DataFrame:
        return self.store.get_stock_sector_membership(code, current_only=current_only)

    def get_hotspot_news(self, *, date: str | None = None, code: str | None = None, sector: str | None = None) -> pd.DataFrame:
        return self.store.get_hotspot_news(date=date, code=code, sector=sector)

    def get_limit_up_events(self, *, date: str | None = None, code: str | None = None) -> pd.DataFrame:
        return self.store.get_limit_up_events(date=date, code=code)

    def sync_stock_list(self, *, limit: int = 0, **_: Any) -> dict[str, Any]:
        run_id = self.store.start_sync_run("stock_basic", data_type="stock_basic", params={"limit": limit})
        try:
            for source in self.market_sources:
                rows = source.fetch_stock_list()
                if limit and limit > 0:
                    rows = rows[:limit]
                if rows:
                    self.store.upsert_stock_basic(pd.DataFrame(rows))
                    self.store.finish_sync_run(run_id, status="success", rows_written=len(rows))
                    self.store.update_data_freshness("stock_basic", "all", latest_time=self._now_date(), row_count=len(rows), source=getattr(source, "name", "unknown"))
                    return {"status": "success", "rows_written": len(rows), "source": getattr(source, "name", "unknown")}
            self.store.finish_sync_run(run_id, status="empty", rows_written=0)
            return {"status": "empty", "rows_written": 0}
        except Exception as exc:
            self.store.finish_sync_run(run_id, status="failed", rows_written=0, error=str(exc))
            raise

    def sync_realtime_quotes(self, *, codes: Iterable[str], **_: Any) -> dict[str, Any]:
        codes_list = [self._normalize_code(code) for code in codes if str(code).strip()]
        run_id = self.store.start_sync_run("realtime_quote", data_type="realtime_quote", params={"codes": codes_list})
        try:
            rows = self._fetch_realtime_quotes(codes_list)
            if rows:
                self.store.upsert_realtime_quote_snapshot(pd.DataFrame(rows))
            self.store.finish_sync_run(run_id, status="success" if rows else "empty", rows_written=len(rows))
            if rows:
                self.store.update_data_freshness("realtime_quote", "snapshot", latest_time=self._now(), row_count=len(rows), source=",".join(sorted({str(row.get("source_main") or row.get("source") or "") for row in rows})))
            return {"status": "success" if rows else "empty", "rows_written": len(rows)}
        except Exception as exc:
            self.store.finish_sync_run(run_id, status="failed", rows_written=0, error=str(exc))
            raise

    def sync_kline(
        self,
        *,
        codes: Iterable[str] | None = None,
        period: str = "d",
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "hfq",
        limit: int = 0,
        incremental_days: int = 0,
        **_: Any,
    ) -> dict[str, Any]:
        normalized_period = self._normalize_period(period)
        if incremental_days > 0 and not start_date:
            start_date = (datetime.now() - timedelta(days=incremental_days)).strftime("%Y-%m-%d")
        if not end_date:
            end_date = self._now_date()
        codes_list = [self._normalize_code(code) for code in (codes or []) if str(code).strip()]
        if not codes_list:
            stock_basic = self.store.get_stock_basic(limit=limit or None)
            if stock_basic is not None and not stock_basic.empty:
                codes_list = stock_basic["code"].astype(str).tolist()
        if limit and limit > 0:
            codes_list = codes_list[:limit]

        params = {"codes": codes_list, "period": normalized_period, "start_date": start_date, "end_date": end_date, "adjust": adjust}
        run_id = self.store.start_sync_run("kline", data_type="kline", params=params)
        rows_written = 0
        try:
            for code in codes_list:
                frame = self.get_kline(
                    code,
                    period=normalized_period,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                    refresh_missing=True,
                )
                rows_written += 0 if frame is None else len(frame)
            self.store.finish_sync_run(run_id, status="success" if rows_written else "empty", rows_written=rows_written)
            self.store.update_data_freshness("kline", normalized_period, latest_time=end_date, row_count=rows_written)
            return {"status": "success" if rows_written else "empty", "rows_written": rows_written}
        except Exception as exc:
            self.store.finish_sync_run(run_id, status="failed", rows_written=rows_written, error=str(exc))
            raise

    def sync_sector_strength(self, *, trade_date: str | None = None, **_: Any) -> dict[str, Any]:
        run_id = self.store.start_sync_run("sector_strength", data_type="sector_strength", params={"trade_date": trade_date})
        rows_written = 0
        try:
            for source in self.sector_sources:
                frame = source.fetch_sector_strength(trade_date)
                if frame is not None and not frame.empty:
                    self.store.upsert_sector_strength(frame)
                    rows_written += len(frame)
            self.store.finish_sync_run(run_id, status="success" if rows_written else "empty", rows_written=rows_written)
            if rows_written:
                self.store.update_data_freshness("sector_strength", "all", latest_time=trade_date or self._now_date(), row_count=rows_written)
            return {"status": "success" if rows_written else "empty", "rows_written": rows_written}
        except Exception as exc:
            self.store.finish_sync_run(run_id, status="failed", rows_written=rows_written, error=str(exc))
            raise

    def sync_sector_membership(
        self,
        *,
        source: str = "tdx",
        trade_date: str | None = None,
        limit: int = 0,
        offset: int = 0,
        **params: Any,
    ) -> dict[str, Any]:
        run_params = {"source": source, "trade_date": trade_date, "limit": limit, "offset": offset, **params}
        run_id = self.store.start_sync_run("sector_membership", data_type="stock_sector_membership", params=run_params)
        rows_written = 0
        try:
            for sector_source in self.sector_sources:
                if source and source != "all" and getattr(sector_source, "name", "") != source:
                    continue
                frame = sector_source.fetch_stock_sector_membership(
                    trade_date,
                    limit=limit,
                    offset=offset,
                    **params,
                )
                if frame is not None and not frame.empty:
                    self.store.upsert_stock_sector_membership(frame)
                    rows_written += len(frame)
            self.store.finish_sync_run(run_id, status="success" if rows_written else "empty", rows_written=rows_written)
            if rows_written:
                self.store.update_data_freshness(
                    "stock_sector_membership",
                    source or "all",
                    latest_time=trade_date or self._now_date(),
                    row_count=rows_written,
                    source=source,
                )
            return {"status": "success" if rows_written else "empty", "rows_written": rows_written}
        except Exception as exc:
            self.store.finish_sync_run(run_id, status="failed", rows_written=rows_written, error=str(exc))
            raise

    def _fetch_realtime_quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        for source in self.market_sources:
            rows = source.fetch_realtime_quotes(codes)
            if rows:
                return rows
        return []

    @classmethod
    def _normalize_kline_frame(
        cls,
        frame: pd.DataFrame | None,
        *,
        code: str,
        period: str,
        adjust: str,
        source: str,
    ) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        result = frame.copy().rename(
            columns={
                "date": "trade_time",
                "trade_date": "trade_time",
                "datetime": "trade_time",
                "trade_dt": "trade_time",
                "vol": "volume",
                "成交量": "volume",
                "成交额": "amount",
                "换手率": "turnover_rate",
                "涨跌幅": "change_pct",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
            }
        )
        if "trade_time" not in result.columns:
            return pd.DataFrame()
        result["code"] = result.get("code", code)
        result["period"] = result.get("period", period)
        result["adjust"] = result.get("adjust", adjust)
        result["source"] = result.get("source", source)
        return result

    @staticmethod
    def _normalize_code(value: Any) -> str:
        text = str(value or "").strip()
        if text.endswith(".0"):
            text = text[:-2]
        text = text.replace("sh", "").replace("sz", "").replace("bj", "")
        return text.zfill(6) if text.isdigit() and len(text) <= 6 else text

    @staticmethod
    def _normalize_period(value: Any) -> str:
        text = str(value or "d").strip().lower()
        return {
            "daily": "d",
            "day": "d",
            "weekly": "w",
            "monthly": "m",
            "1": "1m",
            "5": "5m",
            "15": "15m",
            "30": "30m",
            "60": "60m",
        }.get(text, text)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _now_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")
