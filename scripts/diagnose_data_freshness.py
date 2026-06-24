from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.storage.sqlite_store import SQLiteStore


def main() -> None:
    store = SQLiteStore()
    freshness = store.get_data_freshness()
    runs = store.get_sync_runs(limit=10)
    payload = {
        "data_freshness": freshness.to_dict("records"),
        "recent_sync_runs": runs.to_dict("records"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
