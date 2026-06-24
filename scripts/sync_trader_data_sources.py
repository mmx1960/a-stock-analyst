from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SECTOR_POOL = PROJECT_ROOT / "config" / "trader_core_sector_pool.txt"
DEFAULT_DIAG_OUTPUT = PROJECT_ROOT / "data" / "reports" / "trader_data_source_coverage.json"


def _read_sector_pool(path: Path) -> list[str]:
    if not path.exists():
        return []
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        values.append(text)
    return values


def _run(cmd: list[str], *, dry_run: bool = False) -> None:
    printable = " ".join(cmd)
    print(f"$ {printable}")
    if dry_run:
        return
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="D神/杰哥视角数据源补齐编排：板块资金、涨停生态、股票-板块归属")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    parser.add_argument("--diagnose", action="store_true", help="运行覆盖率诊断并写报告")
    parser.add_argument("--sync-membership", action="store_true", help="补股票-板块归属表")
    parser.add_argument("--sync-sector-strength", action="store_true", help="补开盘啦板块强度历史")
    parser.add_argument("--sync-kaipanla-recent", action="store_true", help="补近期市场情绪/涨停生态")
    parser.add_argument("--all", action="store_true", help="执行 diagnose + membership + sector strength + recent kaipanla")
    parser.add_argument("--start-date", default="2026-06-01")
    parser.add_argument("--end-date", default="2026-06-19")
    parser.add_argument("--recent-days", type=int, default=20)
    parser.add_argument("--sector-pool", default=str(DEFAULT_SECTOR_POOL))
    parser.add_argument("--limit-sectors", type=int, default=0)
    parser.add_argument("--limit-boards", type=int, default=0)
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--limit-history-rows", type=int, default=0)
    parser.add_argument("--throttle", type=float, default=0.5)
    parser.add_argument("--diag-output", default=str(DEFAULT_DIAG_OUTPUT))
    args = parser.parse_args()

    do_all = args.all
    if do_all or args.diagnose:
        _run(
            [
                sys.executable,
                "scripts/diagnose_trader_data_sources.py",
                "--output",
                args.diag_output,
            ],
            dry_run=args.dry_run,
        )

    if do_all or args.sync_membership:
        # 杰哥需要“龙头/板块龙头/跟风狗”的归属；D神需要“板块资金一致性”的匹配。
        # 默认先跑相对稳的开盘啦历史投影 + 东财行业/概念；cninfo/ths 可用 limit 分批继续。
        cmd = [
            sys.executable,
            "scripts/sync_stock_sector_membership.py",
            "--sources",
            "kaipanla_history",
            "akshare_em_industry",
            "akshare_em_concept",
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--throttle",
            str(args.throttle),
        ]
        if args.limit_boards:
            cmd += ["--limit-boards", str(args.limit_boards)]
        if args.limit_stocks:
            cmd += ["--limit-stocks", str(args.limit_stocks)]
        if args.limit_history_rows:
            cmd += ["--limit-history-rows", str(args.limit_history_rows)]
        _run(cmd, dry_run=args.dry_run)

    if do_all or args.sync_sector_strength:
        sector_values = _read_sector_pool(Path(args.sector_pool))
        if sector_values:
            cmd = [
                sys.executable,
                "scripts/sync_kaipanla_sector_strength_history.py",
                "--start-date",
                args.start_date,
                "--end-date",
                args.end_date,
                "--sector-codes",
                *sector_values,
                "--min-interval",
                str(args.throttle),
            ]
            if args.limit_sectors:
                cmd += ["--limit-sectors", str(args.limit_sectors)]
            _run(cmd, dry_run=args.dry_run)
        # 再用 BigAmap 当前热点板块做补充，避免只靠手工池。游客态可能只有近几天，脚本会 fallback。
        cmd = [
            sys.executable,
            "scripts/sync_kaipanla_sector_strength_history.py",
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--use-bigmamap-boards",
            "--min-interval",
            str(args.throttle),
        ]
        if args.limit_sectors:
            cmd += ["--limit-sectors", str(args.limit_sectors)]
        _run(cmd, dry_run=args.dry_run)

    if do_all or args.sync_kaipanla_recent:
        # 开盘啦历史涨停原因接口对远日期可能返回当前日期；这里仍保留近端补库入口，脚本会记录 date mismatch。
        _run(
            [
                sys.executable,
                "scripts/sync_kaipanla_data.py",
                "--recent-days",
                str(args.recent_days),
                "--include-sector-strength",
                "--skip-weekends",
                "--min-interval",
                str(args.throttle),
            ],
            dry_run=args.dry_run,
        )

    if do_all or args.diagnose or args.sync_membership or args.sync_sector_strength or args.sync_kaipanla_recent:
        _run(
            [
                sys.executable,
                "scripts/diagnose_trader_data_sources.py",
                "--output",
                args.diag_output,
            ],
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
