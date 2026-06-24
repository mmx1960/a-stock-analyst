from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INCLUDE = ["stock", "daily", "sector-membership", "sector-strength"]
VALID_INCLUDE = {
    "stock",
    "daily",
    "sector-membership",
    "sector-strength",
}


@dataclass(frozen=True)
class InitStep:
    name: str
    command: list[str]


def _split_include(values: Iterable[str]) -> list[str]:
    include: list[str] = []
    for value in values:
        for part in str(value).split(","):
            name = part.strip()
            if name:
                include.append(name)
    return include or list(DEFAULT_INCLUDE)


def _date_compact(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    return text.replace("-", "")


def _date_iso(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _python_cmd(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def build_steps(args: argparse.Namespace) -> list[InitStep]:
    include = _split_include(args.include)
    unknown = sorted(set(include) - VALID_INCLUDE)
    if unknown:
        raise ValueError(f"unknown include item(s): {', '.join(unknown)}")

    steps: list[InitStep] = []

    if "stock" in include:
        command = _python_cmd("scripts/sync_stock_list.py")
        if args.stock_limit > 0:
            command += ["--limit", str(args.stock_limit)]
        steps.append(InitStep("stock", command))

    if "daily" in include:
        command = _python_cmd(
            "scripts/sync_history_kline.py",
            "--from-db-stock-list",
            "--start-date",
            _date_compact(args.start_date),
            "--adjust",
            args.adjust,
        )
        if args.end_date:
            command += ["--end-date", _date_compact(args.end_date)]
        if args.daily_limit > 0:
            command += ["--limit", str(args.daily_limit)]
        if args.offset > 0:
            command += ["--offset", str(args.offset)]
        if args.skip_if_exists:
            command.append("--skip-if-exists")
        steps.append(InitStep("daily", command))

    if "sector-membership" in include:
        command = _python_cmd(
            "scripts/sync_stock_sector_membership.py",
            "--sources",
            *args.sector_membership_sources,
        )
        if args.start_date:
            command += ["--start-date", _date_iso(args.start_date)]
        if args.end_date:
            command += ["--end-date", _date_iso(args.end_date)]
        if args.sector_membership_limit_stocks > 0:
            command += ["--limit-stocks", str(args.sector_membership_limit_stocks)]
        if args.sector_membership_limit_boards > 0:
            command += ["--limit-boards", str(args.sector_membership_limit_boards)]
        if args.sector_membership_limit_pages > 0:
            command += ["--limit-pages", str(args.sector_membership_limit_pages)]
        if args.sector_membership_limit_history_rows > 0:
            command += ["--limit-history-rows", str(args.sector_membership_limit_history_rows)]
        if args.offset > 0:
            command += ["--offset", str(args.offset)]
        command += ["--throttle", str(args.throttle)]
        steps.append(InitStep("sector-membership", command))

    if "sector-strength" in include:
        command = _python_cmd(
            "scripts/sync_kaipanla_sector_strength_history.py",
            "--start-date",
            _date_iso(args.start_date),
            "--end-date",
            _date_iso(args.end_date or datetime.now().strftime("%Y-%m-%d")),
            "--lookback",
            str(args.sector_strength_lookback),
            "--timeout",
            str(args.timeout),
            "--min-interval",
            str(args.throttle),
        )
        if args.sector_codes:
            command += ["--sector-codes", *args.sector_codes]
        if args.use_bigmamap_boards:
            command.append("--use-bigmamap-boards")
        if args.sector_strength_limit_sectors > 0:
            command += ["--limit-sectors", str(args.sector_strength_limit_sectors)]
        steps.append(InitStep("sector-strength", command))

    return steps


def run_steps(steps: list[InitStep], *, dry_run: bool, continue_on_error: bool) -> int:
    if not steps:
        print("no steps selected")
        return 0

    print(f"init_market_data steps={','.join(step.name for step in steps)} dry_run={dry_run}")
    for index, step in enumerate(steps, start=1):
        printable = " ".join(step.command)
        print(f"[{index}/{len(steps)}] {step.name}: {printable}")
        if dry_run:
            continue
        result = subprocess.run(step.command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"step={step.name} failed exit_code={result.returncode}")
            if not continue_on_error:
                return result.returncode
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化 A 股本地市场数据；当前阶段仅手动触发，不创建定时任务")
    parser.add_argument(
        "--include",
        nargs="*",
        default=list(DEFAULT_INCLUDE),
        help="初始化阶段，可逗号分隔：stock,daily,sector-membership,sector-strength",
    )
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--adjust", default="hfq")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--stock-limit", type=int, default=0)
    parser.add_argument("--daily-limit", type=int, default=0)
    parser.add_argument("--skip-if-exists", action="store_true", default=False)
    parser.add_argument("--throttle", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--continue-on-error", action="store_true", default=False)

    parser.add_argument(
        "--sector-membership-sources",
        nargs="*",
        default=["kaipanla_history", "tdx_industry"],
        help="传给 sync_stock_sector_membership.py 的 sources；默认投影本地开盘啦历史并拉取通达信多级行业",
    )
    parser.add_argument("--sector-membership-limit-stocks", type=int, default=0)
    parser.add_argument("--sector-membership-limit-boards", type=int, default=0)
    parser.add_argument("--sector-membership-limit-pages", type=int, default=0)
    parser.add_argument("--sector-membership-limit-history-rows", type=int, default=0)

    parser.add_argument("--sector-codes", nargs="*", default=[])
    parser.add_argument("--sector-strength-lookback", type=int, default=5)
    parser.add_argument("--sector-strength-limit-sectors", type=int, default=0)
    parser.add_argument("--use-bigmamap-boards", action="store_true", default=False)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        steps = build_steps(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run_steps(steps, dry_run=args.dry_run, continue_on_error=args.continue_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
