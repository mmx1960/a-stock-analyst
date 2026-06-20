"""
缠论 v3.2 主实时选股策略
在 v3.1 单一真源基础上增加信号分级与排序评分。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import pandas as pd
import numpy as np

from backtest.strategies.strategy_v3_1_realtime import (
    THROTTLE_SECONDS,
    analyze_v3_1_signal,
    calc_macd,
    get_active_stocks,
    get_history,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SIGNAL_BASE_SCORES = {
    'higher_low': 100.0,
    'zero_axis_pullback': 95.0,
    'macd_area_divergence': 80.0,
}

SIGNAL_PRIORITY_LABELS = {
    'higher_low': 'P1',
    'zero_axis_pullback': 'P2',
    'macd_area_divergence': 'P3',
}


def calc_obv(df: pd.DataFrame) -> pd.Series:
    close = df['close'].astype(float).values
    vol = df['volume'].fillna(0).astype(float).values
    obv = [0.0]
    for i in range(1, len(df)):
        if close[i] > close[i - 1]:
            obv.append(obv[-1] + vol[i])
        elif close[i] < close[i - 1]:
            obv.append(obv[-1] - vol[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)


def _slope_ratio(series: pd.Series) -> float | None:
    arr = pd.Series(series).astype(float).dropna().values
    if len(arr) < 2:
        return None
    start = arr[0]
    end = arr[-1]
    base = abs(start) if abs(start) > 1e-9 else max(pd.Series(arr).abs().mean(), 1.0)
    return float((end - start) / base)


def _linear_slope(series: pd.Series) -> float | None:
    arr = pd.Series(series).astype(float).dropna().values
    if len(arr) < 2:
        return None
    x = list(range(len(arr)))
    slope, _ = np.polyfit(x, arr, 1)
    return float(slope)


def enrich_signal_filters(df_daily: pd.DataFrame, signal: dict) -> dict:
    buy_date = pd.to_datetime(signal['buy_date'])
    matched = df_daily.index[df_daily['date'] == buy_date].tolist()
    if not matched:
        enriched = dict(signal)
        enriched['filter_metrics'] = {}
        return enriched

    idx = matched[0]
    window = df_daily.iloc[:idx + 1].copy()
    if window.empty:
        enriched = dict(signal)
        enriched['filter_metrics'] = {}
        return enriched

    if not all(col in window.columns for col in ('dif', 'dea', 'macd')):
        window = calc_macd(window)

    pre = window.iloc[max(0, len(window) - 21):].copy()
    if pre.empty:
        enriched = dict(signal)
        enriched['filter_metrics'] = {}
        return enriched

    if 'obv' not in pre.columns:
        pre['obv'] = calc_obv(pre)

    vol_ma20 = float(pre['volume'].tail(20).mean()) if len(pre) else 0.0
    volume_ma5_ma20_ratio = float(pre['volume'].tail(5).mean() / vol_ma20) if vol_ma20 else 0.0
    buy_day_volume_vs_ma20 = float(pre.iloc[-1]['volume'] / vol_ma20) if vol_ma20 else 0.0
    obv_last5_trend = _slope_ratio(pre['obv'].tail(5)) or 0.0
    dif_trend_20d = _linear_slope(pre['dif']) or 0.0
    dea_trend_20d = _linear_slope(pre['dea']) or 0.0
    macd_negative_days_last10 = int((pre['macd'].tail(10) < 0).sum())
    price_below_dea_on_buy = bool(pre.iloc[-1]['dif'] < pre.iloc[-1]['dea'])

    enriched = dict(signal)
    enriched['filter_metrics'] = {
        'volume_ma5_ma20_ratio': round(volume_ma5_ma20_ratio, 4),
        'buy_day_volume_vs_ma20': round(buy_day_volume_vs_ma20, 4),
        'obv_last5_trend': round(obv_last5_trend, 4),
        'dif_trend_20d': round(dif_trend_20d, 6),
        'dea_trend_20d': round(dea_trend_20d, 6),
        'macd_negative_days_last10': macd_negative_days_last10,
        'price_below_dea_on_buy': price_below_dea_on_buy,
    }
    return enriched


def passes_v3_2_1_filters(signal: dict) -> bool:
    metrics = signal.get('filter_metrics', {})
    signal_type = signal['signal_type']

    buy_day_volume_vs_ma20 = float(metrics.get('buy_day_volume_vs_ma20', signal.get('buy_day_volume_vs_ma20', 0.0)) or 0.0)
    obv_last5_trend = float(metrics.get('obv_last5_trend', signal.get('obv_last5_trend', 0.0)) or 0.0)
    dif_trend_20d = float(metrics.get('dif_trend_20d', signal.get('dif_trend_20d', 0.0)) or 0.0)
    dea_trend_20d = float(metrics.get('dea_trend_20d', signal.get('dea_trend_20d', 0.0)) or 0.0)
    macd_negative_days_last10 = int(metrics.get('macd_negative_days_last10', signal.get('macd_negative_days_last10', 99)) or 99)
    price_below_dea_on_buy = bool(metrics.get('price_below_dea_on_buy', signal.get('price_below_dea_on_buy', False)))

    if signal_type == 'zero_axis_pullback':
        return buy_day_volume_vs_ma20 >= 0.85

    if buy_day_volume_vs_ma20 < 0.9:
        return False

    if signal_type == 'higher_low':
        return obv_last5_trend > 0 and macd_negative_days_last10 <= 3 and not price_below_dea_on_buy

    if signal_type == 'macd_area_divergence':
        return dif_trend_20d > 0 and dea_trend_20d > 0 and obv_last5_trend >= 0 and macd_negative_days_last10 <= 5

    return True


def score_v3_2_signal(signal: dict) -> dict:
    signal_type = signal['signal_type']
    base_score = SIGNAL_BASE_SCORES.get(signal_type, 60.0)

    pullback_pct = float(signal.get('pullback_pct', 0.0) or 0.0)
    if pullback_pct >= 12.0:
        pullback_bonus = 10.0
    elif pullback_pct >= 8.0:
        pullback_bonus = 14.0
    elif pullback_pct >= 5.0:
        pullback_bonus = 8.0
    elif pullback_pct >= 3.0:
        pullback_bonus = 4.0
    else:
        pullback_bonus = 0.0

    days_ago = float(signal.get('days_ago', 7) or 7)
    freshness_bonus = max(0.0, 6.0 - min(days_ago, 6.0))

    red_bar_count = int(signal.get('entry_recent_5bars_red_bar_count', 0) or 0)
    if red_bar_count >= 4:
        stopfall_bonus = 6.0
    elif red_bar_count == 3:
        stopfall_bonus = 4.0
    elif red_bar_count == 2:
        stopfall_bonus = 2.0
    else:
        stopfall_bonus = 0.0

    volume_gate_reason = str(signal.get('volume_gate_reason', '') or '')
    if volume_gate_reason == 'daily_volume_spike_retrace':
        volume_bonus = 6.0
    elif volume_gate_reason == 'weekly_slow_volume_build':
        volume_bonus = 3.0
    else:
        volume_bonus = 0.0

    weekly_context_bonus = 4.0 if (not bool(signal.get('weekly_context_bypassed', False)) and signal.get('weekly_gate_reason') == 'weekly_context_ok') else 0.0
    intraday_bonus = 2.0 if bool(signal.get('intraday_second_buy_ok', False)) else 0.0

    score = round(base_score + pullback_bonus + freshness_bonus + stopfall_bonus + volume_bonus + weekly_context_bonus + intraday_bonus, 2)

    scored = dict(signal)
    scored.update({
        'strategy_version': 'v3.2',
        'signal_base_score': round(base_score, 2),
        'signal_priority': SIGNAL_PRIORITY_LABELS.get(signal_type, 'P9'),
        'score_breakdown': {
            'base_score': round(base_score, 2),
            'pullback_bonus': round(pullback_bonus, 2),
            'freshness_bonus': round(freshness_bonus, 2),
            'stopfall_bonus': round(stopfall_bonus, 2),
            'volume_bonus': round(volume_bonus, 2),
            'weekly_context_bonus': round(weekly_context_bonus, 2),
            'intraday_bonus': round(intraday_bonus, 2),
        },
        'signal_score': score,
    })
    return scored



def analyze_v3_2_signal(
    df_daily,
    now: datetime | None = None,
    precomputed_signal: dict | None = None,
) -> dict | None:
    signal = precomputed_signal or analyze_v3_1_signal(df_daily=df_daily, now=now)
    if not signal:
        return None
    enriched_signal = dict(precomputed_signal) if precomputed_signal and precomputed_signal.get('filter_metrics') else enrich_signal_filters(df_daily=df_daily, signal=signal)
    scored = score_v3_2_signal(enriched_signal)
    scored['strategy_version'] = 'v3.2.2'
    scored['ranking_filter_pass'] = passes_v3_2_1_filters(enriched_signal)
    return scored



def scan_active_stocks_v3_2(top_n: int = 500, throttle_seconds: float = THROTTLE_SECONDS) -> list[dict]:
    logger.info(f'=== 缠论 v3.2 实时选股 (Top {top_n}) ===')
    stocks = get_active_stocks(top_n=top_n)
    logger.info(f'扫描 {len(stocks)} 只活跃股...')

    results: list[dict] = []
    start_time = time.time()
    for i, (code, name, price, chg) in enumerate(stocks):
        df_daily, _ = get_history(code)
        if df_daily is None:
            continue

        res = analyze_v3_2_signal(df_daily)
        if res:
            res.update({
                'code': code,
                'name': name,
                'current_price': float(price),
                'change_pct': float(chg),
            })
            results.append(res)
            logger.info(
                f"🎯 {code} {name} 分数:{res['signal_score']} 优先级:{res['signal_priority']} "
                f"买点:{res['buy_date']} 类型:{res['signal_type']}"
            )

        if (i + 1) % 50 == 0:
            logger.info(f'进度: {i + 1}/{len(stocks)} 耗时:{time.time() - start_time:.0f}s 命中:{len(results)}')
        time.sleep(throttle_seconds)

    results.sort(key=lambda x: (-x['signal_score'], x['days_ago'], -x['pullback_pct']))
    logger.info(f'\n完成! 耗时 {time.time() - start_time:.0f}s, 共 {len(results)} 只')
    return results



def save_results(results: list[dict], output_path: str = 'current_v3_2_selections.json') -> None:
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)



def main() -> None:
    results = scan_active_stocks_v3_2()
    save_results(results)
    for r in results:
        logger.info(
            f"  {r['code']} {r['name']} 分数:{r['signal_score']} 优先级:{r['signal_priority']} "
            f"买点:{r['buy_date']} (距今{r['days_ago']}天) 类型:{r['signal_type']}"
        )


if __name__ == '__main__':
    main()
