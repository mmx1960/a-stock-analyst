"""
缠论 v3.1 回测：复用 `strategy_v3_1_realtime.py` 中的核心判定函数。
目标：验证实时主策略在历史样本上的信号质量，而不是维护另一套独立逻辑。
"""
import json
import logging
import random
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data_provider import data_provider
from backtest.strategies.strategy_v3_1_realtime import (
    LOOKBACK_DAYS,
    MIN_CURRENT_TO_MA250_RATIO,
    MIN_DAILY_BARS,
    SIGNAL_WINDOW_DAYS,
    analyze_v3_1_signal,
    normalize_history_dataframe,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

CACHE_DIR = Path('backtest/cache/history_v3_1')
EXCLUDED_CODE_PREFIXES = ('92',)
EXCLUDED_EXACT_CODES = set()


def _cache_file_for_history(code: str, start_date: str, end_date: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = f'{code}_{start_date}_{end_date}'
    digest = hashlib.md5(key.encode('utf-8')).hexdigest()[:12]
    return CACHE_DIR / f'{code}_{start_date}_{end_date}_{digest}.csv'


def _is_supported_stock_code(code: str) -> bool:
    if not code or not code.isdigit():
        return False
    if code in EXCLUDED_EXACT_CODES:
        return False
    if code.startswith(EXCLUDED_CODE_PREFIXES):
        return False
    return code.startswith(('000', '001', '002', '003', '300', '301', '600', '601', '603', '605', '688', '689', '830', '831', '832', '833', '835', '836', '837', '838', '839', '870', '871', '872', '873', '874', '875', '876', '877', '878', '879'))


def _trim_history_range(df: pd.DataFrame, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    frame['date'] = pd.to_datetime(frame['date'])
    frame = frame[frame['date'] >= pd.to_datetime(start_date)]
    if end_date:
        frame = frame[frame['date'] <= pd.to_datetime(end_date)]
    return frame.reset_index(drop=True)


def get_stock_history(code: str, start_date: str = '20190101', end_date: str | None = None) -> pd.DataFrame:
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')

    cache_file = _cache_file_for_history(code, start_date, end_date)
    if cache_file.exists():
        try:
            cached_df = pd.read_csv(cache_file)
            cached_df = normalize_history_dataframe(cached_df)
            if cached_df is not None:
                cached_df.attrs['code'] = code
            cached_df = _trim_history_range(cached_df, start_date, end_date)
            if cached_df is not None and not cached_df.empty:
                logger.info(f'{code} 历史K线命中本地缓存: {cache_file.name}')
                return cached_df
        except Exception as exc:
            logger.warning(f'{code} 历史K线缓存读取失败，改走数据门面: {exc}')

    raw = data_provider.get_kline_daily(code=code, start_date=start_date, end_date=end_date)
    df = normalize_history_dataframe(raw)
    if df is not None:
        df.attrs['code'] = code
    df = _trim_history_range(df, start_date, end_date)
    if df is not None and len(df) >= MIN_DAILY_BARS:
        df.to_csv(cache_file, index=False)
        return df

    logger.warning(f'{code} 历史K线从统一数据门面获取失败或数据不足')
    return pd.DataFrame()


def calculate_future_return(df: pd.DataFrame, buy_date: pd.Timestamp, buy_price: float, weeks: int = 10) -> dict:
    future = df[df['date'] > buy_date].copy()
    if len(future) < 5:
        return {'max_return': 0, 'max_date': None, 'weeks_to_max': 0, 'status': '数据不足'}

    end_date = buy_date + timedelta(days=weeks * 7)
    future = future[future['date'] <= end_date]
    if future.empty:
        return {'max_return': 0, 'max_date': None, 'weeks_to_max': 0, 'status': '无后续数据'}

    max_price = float(future['close'].max())
    max_return = (max_price - buy_price) / buy_price * 100
    max_idx = future['close'].idxmax()
    max_date = future.loc[max_idx, 'date']
    days = (max_date - buy_date).days
    return {
        'max_return': round(max_return, 2),
        'max_price': round(max_price, 2),
        'max_date': str(max_date)[:10],
        'days': int(days),
        'weeks_to_max': round(days / 7, 1),
        'status': '已实现',
    }


def backtest_stock_v3_1(
    code: str,
    name: str,
    start_year: int = 2020,
    hold_weeks: int = 10,
    *,
    enforce_weekly_context: bool = False,
    enforce_historical_high_filter: bool = True,
    enforce_ma250_filter: bool = True,
    enforce_volume_pattern: bool = True,
    enforce_entry_confirmation: bool = True,
) -> dict:
    try:
        df_daily = get_stock_history(code, start_date=f'{start_year}0101')
        if len(df_daily) < 220:
            return {'code': code, 'name': name, 'error': '数据不足', 'buy_points': []}

        buy_points = []

        start_idx = max(LOOKBACK_DAYS, 180)
        for i in range(start_idx, len(df_daily)):
            window_df = df_daily.iloc[:i + 1].copy()
            window_df.attrs['backtest_end_idx'] = i
            signal = analyze_v3_1_signal(
                window_df,
                now=window_df.iloc[-1]['date'],
                enforce_weekly_context=enforce_weekly_context,
                enforce_historical_high_filter=enforce_historical_high_filter,
                enforce_ma250_filter=enforce_ma250_filter,
                enforce_volume_pattern=enforce_volume_pattern,
                enforce_entry_confirmation=enforce_entry_confirmation,
            )
            if not signal:
                continue

            buy_date = pd.to_datetime(signal['buy_date'])
            buy_price = float(signal['price'])

            detection_date = pd.to_datetime(window_df.iloc[-1]['date'])

            future = calculate_future_return(df_daily, buy_date, buy_price, weeks=hold_weeks)
            buy_points.append({
                'buy_date': str(buy_date)[:10],
                'detection_date': str(detection_date)[:10],
                'detection_idx': int(i),
                'buy_price': round(buy_price, 2),
                'signal_type': signal['signal_type'],
                'days_ago_at_detection': signal['days_ago'],
                'pullback_pct': signal['pullback_pct'],
                'support_reason': signal['support_reason'],
                'entry_ok': signal.get('entry_ok'),
                'entry_reason': signal.get('entry_reason'),
                'entry_signal_type': signal.get('entry_signal_type'),
                'entry_score': signal.get('entry_score'),
                'entry_min_score_required': signal.get('entry_min_score_required'),
                'entry_score_max': signal.get('entry_score_max'),
                'entry_failed_checks': signal.get('entry_failed_checks', []),
                'entry_body_rebound_pct': signal.get('entry_body_rebound_pct'),
                'entry_close_from_low_pct': signal.get('entry_close_from_low_pct'),
                'entry_close_above_prev_close_pct': signal.get('entry_close_above_prev_close_pct'),
                'entry_volume_ratio': signal.get('entry_volume_ratio'),
                'historical_high_ok': signal.get('historical_high_ok'),
                'historical_high_reason': signal.get('historical_high_reason'),
                'historical_high_price': signal.get('historical_high_price'),
                'buy_close_price': signal.get('buy_close_price'),
                'current_to_historical_high_ratio': signal.get('current_to_historical_high_ratio'),
                'min_current_to_historical_high_ratio': signal.get('min_current_to_historical_high_ratio'),
                'ma250_ok': signal.get('ma250_ok'),
                'ma250_reason': signal.get('ma250_reason'),
                'ma250_value': signal.get('ma250_value'),
                'current_to_ma250_ratio': signal.get('current_to_ma250_ratio'),
                'min_current_to_ma250_ratio': signal.get('min_current_to_ma250_ratio'),
                'volume_pattern_ok': signal.get('volume_pattern_ok'),
                'volume_pattern_reason': signal.get('volume_pattern_reason'),
                'volume_pattern_anchor_date': signal.get('volume_pattern_anchor_date'),
                'volume_pattern_anchor_volume': signal.get('volume_pattern_anchor_volume'),
                'volume_pattern_anchor_idx': signal.get('volume_pattern_anchor_idx'),
                'volume_pattern_anchor_spike_ratio': signal.get('volume_pattern_anchor_spike_ratio'),
                'volume_pattern_bearish_violation_date': signal.get('volume_pattern_bearish_violation_date'),
                'volume_pattern_bearish_violation_ratio': signal.get('volume_pattern_bearish_violation_ratio'),
                'weekly_uptrend_context_ok': signal.get('weekly_uptrend_context_ok'),
                'weekly_uptrend_reason': signal.get('weekly_uptrend_reason'),
                'weekly_pullback_bars': signal.get('weekly_pullback_bars'),
                'weekly_context_window': signal.get('weekly_context_window'),
                'weekly_context_half_below_ratio': signal.get('weekly_context_half_below_ratio'),
                'weekly_pre_pullback_high': signal.get('weekly_pre_pullback_high'),
                'weekly_current_close': signal.get('weekly_current_close'),
                'weekly_slow_volume_ok': signal.get('weekly_slow_volume_ok'),
                'weekly_slow_volume_reason': signal.get('weekly_slow_volume_reason'),
                'volume_gate_ok': signal.get('volume_gate_ok'),
                'volume_gate_reason': signal.get('volume_gate_reason'),
                'intraday_second_buy_ok': signal.get('intraday_second_buy_ok'),
                'intraday_second_buy_reason': signal.get('intraday_second_buy_reason'),
                **future,
            })

        realized_returns = [bp['max_return'] for bp in buy_points if bp.get('status') == '已实现']
        signal_types = {}
        for bp in buy_points:
            st = bp.get('signal_type', 'unknown')
            signal_types[st] = signal_types.get(st, 0) + 1

        if buy_points:
            return {
                'code': code,
                'name': name,
                'total_buy_points': len(buy_points),
                'avg_return': round(np.mean(realized_returns), 2) if realized_returns else 0,
                'max_return': round(max(realized_returns), 2) if realized_returns else 0,
                'win_rate': round(sum(1 for r in realized_returns if r > 10) / len(realized_returns) * 100, 1) if realized_returns else 0,
                'signal_type_breakdown': signal_types,
                'buy_points': buy_points,
            }

        return {'code': code, 'name': name, 'total_buy_points': 0, 'buy_points': []}
    except Exception as e:
        return {'code': code, 'name': name, 'error': str(e), 'buy_points': []}


def load_stock_list_from_file(path: str | Path) -> list[tuple[str, str]]:
    stock_path = Path(path)
    data = json.loads(stock_path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError('股票池文件必须是数组')

    stocks: list[tuple[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            code = str(item.get('code', '')).strip()
            name = str(item.get('name', code)).strip() or code
        elif isinstance(item, (list, tuple)) and len(item) >= 1:
            code = str(item[0]).strip()
            name = str(item[1]).strip() if len(item) > 1 and item[1] else code
        else:
            continue

        if code:
            stocks.append((code, name))

    if not stocks:
        raise ValueError(f'股票池文件为空: {stock_path}')
    filtered = [(code, name) for code, name in stocks if _is_supported_stock_code(code)]
    if not filtered:
        raise ValueError(f'股票池文件过滤后为空: {stock_path}')
    return filtered


def fetch_stock_list_with_fallback(fallback_paths: Iterable[str | Path] | None = None) -> list[tuple[str, str]]:
    stocks_raw = data_provider.get_stock_list() or []
    stocks = []
    for item in stocks_raw:
        code = str(item.get('code', '')).strip()
        name = str(item.get('name', code)).strip() or code
        if code and _is_supported_stock_code(code):
            stocks.append((code, name))
    if stocks:
        logger.info(f'股票列表改用统一数据门面: {len(stocks)} 只')
        return stocks

    logger.warning('统一数据门面未拿到股票列表，尝试本地 fallback 文件')
    for path in fallback_paths or []:
        stock_path = Path(path)
        if stock_path.exists():
            stocks = load_stock_list_from_file(stock_path)
            logger.info(f'改用本地股票池: {stock_path} ({len(stocks)} 只)')
            return stocks
    raise RuntimeError('股票列表全部数据源失败')


def backtest_random_100_v3_1(start_year: int = 2020, hold_weeks: int = 10, sample_size: int = 100, seed: int = 42,
                              stock_list: list[tuple[str, str]] | None = None,
                              fallback_paths: Iterable[str | Path] | None = None) -> Path:
    random.seed(seed)
    logger.info('=== 策略 v3.1 回测开始（复用实时主策略核心函数）===')

    if stock_list is None:
        logger.info('获取股票列表...')
        all_stocks = fetch_stock_list_with_fallback(fallback_paths=fallback_paths)
    else:
        all_stocks = list(stock_list)
        logger.info(f'使用外部提供股票列表，共 {len(all_stocks)} 只')

    sample_stocks = random.sample(all_stocks, min(sample_size, len(all_stocks)))
    logger.info(f'随机选中 {len(sample_stocks)} 只股票')

    results = []
    start_time = time.time()

    for i, (code, name) in enumerate(sample_stocks):
        res = backtest_stock_v3_1(code, name, start_year=start_year, hold_weeks=hold_weeks)
        if res.get('total_buy_points', 0) > 0:
            results.append(res)
            logger.info(
                f"✅ {code} {name} | 买点:{res['total_buy_points']} | 平均涨幅:{res.get('avg_return', 0)}% | "
                f"胜率:{res.get('win_rate', 0)}% | 最大:{res.get('max_return', 0)}%"
            )

        if (i + 1) % 20 == 0:
            elapsed = time.time() - start_time
            logger.info(f'进度: {i + 1}/{len(sample_stocks)} 耗时:{elapsed:.0f}s 有效:{len(results)}')

    total_bp = sum(r.get('total_buy_points', 0) for r in results)
    all_returns = []
    signal_type_breakdown = {}
    for r in results:
        for k, v in r.get('signal_type_breakdown', {}).items():
            signal_type_breakdown[k] = signal_type_breakdown.get(k, 0) + v
        for bp in r.get('buy_points', []):
            if bp.get('status') == '已实现':
                all_returns.append(bp['max_return'])

    report = {
        'strategy': 'v3.1',
        'sample_size': len(sample_stocks),
        'valid_stocks': len(results),
        'total_buy_points': total_bp,
        'total_signals': len(all_returns),
        'hold_weeks': hold_weeks,
        'signal_window_days': SIGNAL_WINDOW_DAYS,
        'min_current_to_ma250_ratio': MIN_CURRENT_TO_MA250_RATIO,
        'avg_return': round(np.mean(all_returns), 2) if all_returns else 0,
        'median_return': round(np.median(all_returns), 2) if all_returns else 0,
        'max_return': round(max(all_returns), 2) if all_returns else 0,
        'win_rate': round(sum(1 for r in all_returns if r > 10) / len(all_returns) * 100, 1) if all_returns else 0,
        'signal_type_breakdown': signal_type_breakdown,
        'detailed': results,
    }

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_file = Path('backtest/results_v6') / f'backtest_v3_1_random100_{timestamp}.json'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"\n{'=' * 60}")
    logger.info(f"🎉 v3.1 回测完成! 总耗时: {time.time() - start_time:.0f}s")
    logger.info(f"{'=' * 60}")
    logger.info(f"样本数量: {report['sample_size']}")
    logger.info(f"有效股票: {report['valid_stocks']}")
    logger.info(f"总买点数: {report['total_buy_points']}")
    logger.info(f"总信号数: {report['total_signals']}")
    logger.info(f"平均涨幅: {report['avg_return']}%")
    logger.info(f"中位数涨幅: {report['median_return']}%")
    logger.info(f"最大涨幅: {report['max_return']}%")
    logger.info(f"胜率(>10%): {report['win_rate']}%")
    logger.info(f"信号类型分布: {report['signal_type_breakdown']}")
    logger.info(f"结果已保存: {out_file}")
    return out_file


def main() -> None:
    backtest_random_100_v3_1()


if __name__ == '__main__':
    main()
