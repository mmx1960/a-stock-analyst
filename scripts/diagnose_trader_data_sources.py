from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "ashare.duckdb"


@dataclass(frozen=True)
class CoverageCheck:
    name: str
    sql: str
    role: str
    d_shen_need: str
    jie_ge_need: str
    min_code_count: int | None = None
    min_sector_count: int | None = None
    min_day_count: int | None = None


CHECKS = [
    CoverageCheck(
        name="stock_basic",
        sql="select count(*) as row_count, count(distinct code) as code_count from stock_basic",
        role="全市场股票池",
        d_shen_need="基础股票池，够用即可",
        jie_ge_need="区分核心/杂毛的底层 universe",
        min_code_count=5000,
    ),
    CoverageCheck(
        name="daily_hfq",
        sql="select count(*) as row_count, count(distinct code) as code_count, min(trade_date) as min_dt, max(trade_date) as max_dt from daily_kline where adjust='hfq'",
        role="日线/趋势/评估窗口",
        d_shen_need="大周期、5/20/60/120/250 均线、趋势上下文",
        jie_ge_need="低吸后的持有/破位/反抽评估",
        min_code_count=5000,
    ),
    CoverageCheck(
        name="minute_30",
        sql="select count(*) as row_count, count(distinct code) as code_count, min(trade_dt) as min_dt, max(trade_dt) as max_dt from minute_kline where period='30'",
        role="30分钟买点结构",
        d_shen_need="看大周期做小周期、回踩多空/MA13/均线粘合",
        jie_ge_need="强分歧后的分时/小周期反抽触发",
        min_code_count=5000,
    ),
    CoverageCheck(
        name="realtime_quote_snapshot",
        sql="select count(*) as row_count, count(distinct code) as code_count, min(updated_at) as min_dt, max(updated_at) as max_dt from realtime_quote_snapshot",
        role="实时快照/流动性/当前价格",
        d_shen_need="避免追高，判断当前位置和成交额",
        jie_ge_need="资金态度、当前强弱、是否还有乐趣",
        min_code_count=5000,
    ),
    CoverageCheck(
        name="stock_sector_membership",
        sql="select count(*) as row_count, count(distinct code) as code_count, count(distinct sector_name) as sector_count from stock_sector_membership where is_current=true",
        role="股票-板块/概念归属",
        d_shen_need="板块资金一致性的必要匹配表",
        jie_ge_need="判断板块龙头/跟风狗的必要前提",
        min_code_count=5000,
        min_sector_count=300,
    ),
    CoverageCheck(
        name="kaipanla_sector_strength",
        sql="select count(*) as row_count, count(distinct sector_name) as sector_count, count(distinct trade_date) as day_count, min(trade_date) as min_dt, max(trade_date) as max_dt from kaipanla_sector_strength",
        role="开盘啦板块区间强度/尾盘抢筹/资金强度",
        d_shen_need="资金一致性不会假；主线/轮动判断的核心",
        jie_ge_need="板块是否有资金继续接，是否能支持龙头低吸",
        min_sector_count=40,
        min_day_count=300,
    ),
    CoverageCheck(
        name="kaipanla_market_sentiment",
        sql="select count(*) as row_count, count(distinct trade_date) as day_count, min(trade_date) as min_dt, max(trade_date) as max_dt from kaipanla_market_sentiment",
        role="市场情绪/涨跌停/连板分布",
        d_shen_need="大盘与市场环境是否支持出手",
        jie_ge_need="冰点、退潮、强分歧、主升周期判断核心",
        min_day_count=300,
    ),
    CoverageCheck(
        name="kaipanla_limit_up_sectors",
        sql="select count(*) as row_count, count(distinct trade_date) as day_count, count(distinct sector_name) as sector_count, min(trade_date) as min_dt, max(trade_date) as max_dt from kaipanla_limit_up_sectors",
        role="涨停原因板块/题材梯队",
        d_shen_need="题材+消息+龙头三重验证中的题材验证",
        jie_ge_need="板块梯队、低位补涨、跟风和核心识别核心",
        min_day_count=60,
        min_sector_count=100,
    ),
    CoverageCheck(
        name="kaipanla_limit_up_stocks",
        sql="select count(*) as row_count, count(distinct code) as code_count, count(distinct trade_date) as day_count, min(trade_date) as min_dt, max(trade_date) as max_dt from kaipanla_limit_up_stocks",
        role="涨停个股/连板/龙头候选",
        d_shen_need="龙头和低位补涨的交叉验证",
        jie_ge_need="全市场总龙头、板块龙头、活口、跟风狗识别核心",
        min_code_count=500,
        min_day_count=60,
    ),
]


def _scalar(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def _status(row: dict[str, Any], check: CoverageCheck) -> tuple[str, list[str]]:
    problems: list[str] = []
    if check.min_code_count is not None and _scalar(row, "code_count") < check.min_code_count:
        problems.append(f"code_count<{check.min_code_count}")
    if check.min_sector_count is not None and _scalar(row, "sector_count") < check.min_sector_count:
        problems.append(f"sector_count<{check.min_sector_count}")
    if check.min_day_count is not None and _scalar(row, "day_count") < check.min_day_count:
        problems.append(f"day_count<{check.min_day_count}")
    if not problems:
        return "OK", []
    if check.name in {"kaipanla_sector_strength", "kaipanla_limit_up_sectors", "kaipanla_limit_up_stocks", "stock_sector_membership"}:
        return "HIGH_GAP", problems
    return "GAP", problems


def diagnose(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    con = duckdb.connect(str(db_path), read_only=True)
    checks: list[dict[str, Any]] = []
    for check in CHECKS:
        try:
            frame = con.execute(check.sql).df()
            row = frame.iloc[0].to_dict() if not frame.empty else {}
            status, problems = _status(row, check)
            checks.append(
                {
                    "name": check.name,
                    "role": check.role,
                    "status": status,
                    "problems": problems,
                    "metrics": {key: str(value) for key, value in row.items()},
                    "d_shen_need": check.d_shen_need,
                    "jie_ge_need": check.jie_ge_need,
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": check.name,
                    "role": check.role,
                    "status": "ERROR",
                    "problems": [f"{type(exc).__name__}: {exc}"],
                    "metrics": {},
                    "d_shen_need": check.d_shen_need,
                    "jie_ge_need": check.jie_ge_need,
                }
            )
    high_gaps = [item for item in checks if item["status"] == "HIGH_GAP"]
    gaps = [item for item in checks if item["status"] in {"GAP", "ERROR"}]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "summary": {
            "total_checks": len(checks),
            "ok": sum(1 for item in checks if item["status"] == "OK"),
            "gaps": len(gaps),
            "high_gaps": len(high_gaps),
        },
        "checks": checks,
        "d_shen_verdict": _d_shen_verdict(checks),
        "jie_ge_verdict": _jie_ge_verdict(checks),
        "recommended_actions": _recommended_actions(checks),
    }


def _by_name(checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next((item for item in checks if item["name"] == name), {})


def _d_shen_verdict(checks: list[dict[str, Any]]) -> str:
    sector = _by_name(checks, "kaipanla_sector_strength")
    membership = _by_name(checks, "stock_sector_membership")
    if sector.get("status") != "OK":
        return "不好说。K线够了，但板块资金一致性样本不够；现在策略会变成只看形态，D神视角不合格。"
    if membership.get("status") != "OK":
        return "基本可以。板块强度历史够了，D神的资金一致性主线判断能跑；但股票-板块归属还没满覆盖，部分个股会匹配不到核心板块。"
    return "可以。板块资金和股票归属基本能支撑趋势+资金一致性判断。"


def _jie_ge_verdict(checks: list[dict[str, Any]]) -> str:
    limit_stocks = _by_name(checks, "kaipanla_limit_up_stocks")
    limit_sectors = _by_name(checks, "kaipanla_limit_up_sectors")
    market = _by_name(checks, "kaipanla_market_sentiment")
    if limit_stocks.get("status") != "OK" or limit_sectors.get("status") != "OK":
        return "尼玛，涨停梯队历史太少。没有龙头/连板/活口历史，就没法像杰哥那样分清核心和跟风狗。"
    if market.get("status") != "OK":
        return "市场情绪历史不够，冰点/退潮/强分歧判断会失真。"
    return "可以。情绪周期和涨停生态能支撑龙头低吸框架。"


def _recommended_actions(checks: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    names = {item["name"]: item for item in checks}
    if names.get("kaipanla_sector_strength", {}).get("status") != "OK":
        actions.append("扩展 kaipanla_sector_strength 板块池：用 BigAmap 当前热点板块 + D神/杰哥核心题材池跑历史区间强度。")
    if names.get("stock_sector_membership", {}).get("status") != "OK":
        actions.append("继续补 stock_sector_membership：优先 tdx_industry 多级行业，其次 ths_concept/akshare_em_concept 分批。")
    if names.get("kaipanla_limit_up_stocks", {}).get("status") != "OK":
        actions.append("补涨停生态历史：当前开盘啦历史涨停接口只能可靠同步近端/当日，需接 BigAmap/同花顺/本地导入源做历史复盘补充。")
    if names.get("realtime_quote_snapshot", {}).get("status") != "OK":
        actions.append("实时快照缺口多来自远端空返回；先分类 ST/退市/北交/停牌，再决定是否换源补齐。")
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="D神/杰哥视角的数据源覆盖率诊断")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output", default="", help="可选：写 JSON 报告路径")
    args = parser.parse_args()
    result = diagnose(Path(args.db))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
