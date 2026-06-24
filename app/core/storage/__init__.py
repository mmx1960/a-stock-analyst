from .sqlite_store import SQLiteStore

try:  # DuckDB remains optional while SQLite is the default local store.
    from .duckdb_store import DuckDBStore
except Exception:  # pragma: no cover
    DuckDBStore = None  # type: ignore

__all__ = ["DuckDBStore", "SQLiteStore"]
