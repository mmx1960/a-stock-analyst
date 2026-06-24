# SQLite Data Source Architecture

This project now has a SQLite-first local data layer for market data.

## Layers

- `app/core/sources/`: adapters for external websites and APIs.
- `app/core/storage/sqlite_store.py`: local SQLite tables grouped by data type.
- `app/core/services/market_data_service.py`: unified cache-first API for app, scripts, backtests, and Web.
- `app/core/scheduler/data_job_runner.py`: configurable sync job runner.

## Default Database

Set `SQLITE_PATH` to override the default local file:

```bash
SQLITE_PATH=data/ashare.sqlite3
```

The legacy DuckDB store remains available for compatibility and migration.

## Data Types

- `stock_basic`: A-share universe and names.
- `kline_bars`: daily, weekly, monthly, and minute bars in one table via `period`.
- `realtime_quote_snapshot`: latest quote snapshot and supplemental valuation fields.
- `sector_strength`: board/sector strength and capital metrics.
- `stock_sector_membership`: stock-to-sector relations.
- `hotspot_news`: news and event records.
- `limit_up_events`: limit-up stocks, reasons, and themes.
- `sync_runs` and `data_freshness`: operational metadata.

## Jobs

Jobs are configured in `config/data_jobs.yaml`.

Run one job:

```bash
python scripts/run_data_job.py --job stock_basic_daily
```

Run a group:

```bash
python scripts/run_data_job.py --group bootstrap
```

Inspect freshness:

```bash
python scripts/diagnose_data_freshness.py
```

Migrate legacy DuckDB data:

```bash
python scripts/migrate_duckdb_to_sqlite.py --duckdb-path data/ashare.duckdb --sqlite-path data/ashare.sqlite3
```
