from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.services.market_data_service import MarketDataService


class DataJobRunner:
    """Run configured local data sync jobs."""

    def __init__(self, *, config_path: str | Path, service: MarketDataService | Any | None = None):
        self.config_path = Path(config_path)
        self.service = service or MarketDataService()
        self.config = self._load_config(self.config_path)

    def run_job(self, job_name: str) -> dict[str, Any]:
        jobs = self.config.get("jobs", {})
        if job_name not in jobs:
            raise KeyError(f"unknown data job: {job_name}")
        job = jobs[job_name] or {}
        if not job.get("enabled", True):
            return {"job_name": job_name, "status": "skipped", "rows_written": 0}
        job_type = str(job.get("type") or "").strip()
        params = dict(job.get("params") or {})
        result = self._dispatch(job_type, params)
        result.setdefault("status", "success")
        result.setdefault("rows_written", 0)
        result["job_name"] = job_name
        result["type"] = job_type
        return result

    def run_group(self, group_name: str) -> list[dict[str, Any]]:
        groups = self.config.get("groups", {})
        if group_name not in groups:
            raise KeyError(f"unknown data job group: {group_name}")
        results: list[dict[str, Any]] = []
        for job_name in groups[group_name] or []:
            result = self.run_job(str(job_name))
            if result.get("status") != "skipped":
                results.append(result)
        return results

    def _dispatch(self, job_type: str, params: dict[str, Any]) -> dict[str, Any]:
        if job_type == "stock_list":
            return self.service.sync_stock_list(**params)
        if job_type == "kline":
            return self.service.sync_kline(**params)
        if job_type == "realtime_quote":
            return self.service.sync_realtime_quotes(**params)
        if job_type == "sector_strength":
            return self.service.sync_sector_strength(**params)
        if job_type == "sector_membership":
            return self.service.sync_sector_membership(**params)
        raise ValueError(f"unsupported data job type: {job_type}")

    @classmethod
    def _load_config(cls, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(text)
            return loaded or {}
        except Exception:
            return cls._parse_simple_yaml(text)

    @staticmethod
    def _parse_simple_yaml(text: str) -> dict[str, Any]:
        """Small YAML subset parser for local job files when PyYAML is unavailable."""

        result: dict[str, Any] = {}
        current_section: str | None = None
        current_job: str | None = None
        in_params = False
        current_group: str | None = None

        for raw_line in text.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            line = raw_line.strip()
            if indent == 0 and line.endswith(":"):
                current_section = line[:-1]
                result[current_section] = {} if current_section != "groups" else {}
                current_job = None
                current_group = None
                in_params = False
                continue
            if current_section == "groups":
                if indent == 2 and line.endswith(":"):
                    current_group = line[:-1]
                    result["groups"][current_group] = []
                    continue
                if indent >= 4 and line.startswith("- ") and current_group:
                    result["groups"][current_group].append(line[2:].strip())
                    continue
            if current_section == "jobs":
                if indent == 2 and line.endswith(":"):
                    current_job = line[:-1]
                    result["jobs"][current_job] = {}
                    in_params = False
                    continue
                if not current_job or ":" not in line:
                    continue
                key, value = [part.strip() for part in line.split(":", 1)]
                if indent == 4 and key == "params":
                    result["jobs"][current_job]["params"] = {}
                    in_params = True
                    continue
                if indent >= 6 and in_params:
                    result["jobs"][current_job].setdefault("params", {})[key] = _coerce_scalar(value)
                    continue
                if indent == 4:
                    result["jobs"][current_job][key] = _coerce_scalar(value)
                    in_params = False
        return result


def _coerce_scalar(value: str) -> Any:
    if value == "":
        return {}
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip('"').strip("'")
