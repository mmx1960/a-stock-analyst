from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.scheduler import DataJobRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run configured local data sync jobs")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "data_jobs.yaml"))
    parser.add_argument("--job", default="", help="Single job name to run")
    parser.add_argument("--group", default="", help="Job group name to run")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.job and not args.group:
        raise SystemExit("provide --job or --group")
    runner = DataJobRunner(config_path=args.config)
    if args.job:
        result = runner.run_job(args.job)
    else:
        result = runner.run_group(args.group)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
