from __future__ import annotations

from app.core.scheduler.data_job_runner import DataJobRunner


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def sync_stock_list(self, **kwargs):
        self.calls.append(("stock_list", kwargs))
        return {"rows_written": 1}

    def sync_kline(self, **kwargs):
        self.calls.append(("kline", kwargs))
        return {"rows_written": 2}

    def sync_sector_membership(self, **kwargs):
        self.calls.append(("sector_membership", kwargs))
        return {"rows_written": 4}


def test_data_job_runner_runs_single_job_from_config(tmp_path) -> None:
    config = tmp_path / "jobs.yaml"
    config.write_text(
        """
jobs:
  stock_basic_daily:
    type: stock_list
    enabled: true
    params:
      limit: 1
  daily_kline_incremental:
    type: kline
    enabled: false
    params:
      period: d
""".strip(),
        encoding="utf-8",
    )
    service = FakeService()

    runner = DataJobRunner(config_path=config, service=service)
    result = runner.run_job("stock_basic_daily")

    assert result["status"] == "success"
    assert service.calls == [("stock_list", {"limit": 1})]


def test_data_job_runner_runs_enabled_group(tmp_path) -> None:
    config = tmp_path / "jobs.yaml"
    config.write_text(
        """
groups:
  intraday:
    - stock_basic_daily
    - daily_kline_incremental
jobs:
  stock_basic_daily:
    type: stock_list
    enabled: true
  daily_kline_incremental:
    type: kline
    enabled: false
""".strip(),
        encoding="utf-8",
    )
    service = FakeService()

    runner = DataJobRunner(config_path=config, service=service)
    results = runner.run_group("intraday")

    assert [item["job_name"] for item in results] == ["stock_basic_daily"]
    assert service.calls == [("stock_list", {})]


def test_data_job_runner_dispatches_sector_membership_job(tmp_path) -> None:
    config = tmp_path / "jobs.yaml"
    config.write_text(
        """
jobs:
  tdx_sector_membership:
    type: sector_membership
    enabled: true
    params:
      source: tdx
      limit: 1
""".strip(),
        encoding="utf-8",
    )
    service = FakeService()

    runner = DataJobRunner(config_path=config, service=service)
    result = runner.run_job("tdx_sector_membership")

    assert result["status"] == "success"
    assert service.calls == [("sector_membership", {"source": "tdx", "limit": 1})]
