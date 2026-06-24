from __future__ import annotations

import sys

from scripts import init_market_data as init


def test_build_steps_defaults_are_safe_and_manual() -> None:
    args = init.parse_args(["--dry-run", "--daily-limit", "2", "--sector-strength-limit-sectors", "1"])

    steps = init.build_steps(args)

    assert [step.name for step in steps] == ["stock", "daily", "sector-membership", "sector-strength"]
    membership = next(step for step in steps if step.name == "sector-membership")
    assert membership.command[:3] == [sys.executable, "scripts/sync_stock_sector_membership.py", "--sources"]
    assert "kaipanla_history" in membership.command
    assert "cninfo_industry" not in membership.command


def test_build_steps_accepts_comma_separated_include_and_dates() -> None:
    args = init.parse_args(
        [
            "--include",
            "stock,daily",
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-20",
            "--stock-limit",
            "3",
            "--daily-limit",
            "2",
            "--offset",
            "5",
            "--skip-if-exists",
        ]
    )

    steps = init.build_steps(args)

    assert [step.name for step in steps] == ["stock", "daily"]
    assert steps[0].command == [sys.executable, "scripts/sync_stock_list.py", "--limit", "3"]
    assert steps[1].command == [
        sys.executable,
        "scripts/sync_history_kline.py",
        "--from-db-stock-list",
        "--start-date",
        "20260601",
        "--adjust",
        "hfq",
        "--end-date",
        "20260620",
        "--limit",
        "2",
        "--offset",
        "5",
        "--skip-if-exists",
    ]


def test_build_steps_sector_strength_can_use_explicit_sector_codes() -> None:
    args = init.parse_args(
        [
            "--include",
            "sector-strength",
            "--start-date",
            "20260612",
            "--end-date",
            "20260618",
            "--sector-codes",
            "801660:通信",
            "801001:芯片",
            "--sector-strength-lookback",
            "5",
            "--throttle",
            "0",
        ]
    )

    steps = init.build_steps(args)

    assert len(steps) == 1
    command = steps[0].command
    assert steps[0].name == "sector-strength"
    assert "scripts/sync_kaipanla_sector_strength_history.py" in command
    assert command[command.index("--start-date") + 1] == "2026-06-12"
    assert command[command.index("--end-date") + 1] == "2026-06-18"
    assert command[command.index("--sector-codes") + 1 : command.index("--sector-codes") + 3] == ["801660:通信", "801001:芯片"]


def test_build_steps_rejects_unknown_include() -> None:
    args = init.parse_args(["--include", "stock,cron"])

    try:
        init.build_steps(args)
    except ValueError as exc:
        assert "cron" in str(exc)
    else:
        raise AssertionError("expected ValueError")
