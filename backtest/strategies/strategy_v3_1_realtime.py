"""
缠论 v3.1 主实时选股策略
单一真源：实时选股、回测、Web 接口后续应复用这里的核心判定函数。
"""
import json
import logging
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.data_provider import data_provider

warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TOP_N_ACTIVE = 500
LOOKBACK_DAYS = 365
MIN_DAILY_BARS = 150
MIN_PULLBACK_PCT = 0.03
SIGNAL_WINDOW_DAYS = 7
THROTTLE_SECONDS = 0.3
MACD_AREA_RATIO = 0.8
MAX_WEEKLY_PULLBACK_BARS = 6
WEEKLY_LOOKBACK_MULTIPLIER = 3
MIN_ENTRY_BODY_REBOUND_PCT = 0.01
MIN_ENTRY_CLOSE_FROM_LOW_PCT = 0.015
MIN_ENTRY_CLOSE_ABOVE_PREV_CLOSE_PCT = 0.0
MIN_ENTRY_VOLUME_RATIO = 0.9
MIN_ENTRY_SCORE = 3
MIN_CURRENT_TO_HISTORICAL_HIGH_RATIO = 1 / 3
MIN_CURRENT_TO_MA250_RATIO = 1.0
VOLUME_BULLISH_LOOKBACK_BARS = 50
VOLUME_SPIKE_BASELINE_BARS = 10
MIN_BULLISH_VOLUME_SPIKE_RATIO = 2.5
MAX_BEARISH_VOLUME_RETRACE_RATIO = 1.0
WEEKLY_SLOW_VOLUME_MIN_RED_BARS = 3
WEEKLY_SLOW_VOLUME_BASELINE_BARS = 8
ENTRY_SCORE_THRESHOLD_BY_SIGNAL_TYPE = {
    'zero_axis_pullback': 2,
    'higher_low': 2,
    'macd_area_divergence': 3,
}
ENTRY_SCORE_WEIGHTS = {
    'body_rebound': 1,
    'close_from_low': 1,
    'prev_close': 1,
    'volume_ratio': 1,
}


def calc_macd(df: pd.DataFrame) -> pd.DataFrame:
    close = df['close']
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = 2 * (dif - dea)

    result = df.copy()
    result['dif'] = dif
    result['dea'] = dea
    result['macd'] = macd
    return result


def find_fenxing(df: pd.DataFrame) -> list[tuple[int, str, float, float]]:
    highs, lows = df['high'].values, df['low'].values
    n = len(df)
    fenxing = []
    for i in range(2, n - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 2]:
            fenxing.append((i, 'D', highs[i], lows[i]))
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 2]:
            fenxing.append((i, 'B', highs[i], lows[i]))
    return fenxing


def form_bis(fenxing: list[tuple[int, str, float, float]]) -> list[tuple[int, int, str, str]]:
    if not fenxing:
        return []

    bis = []
    start = None
    for i, fx in enumerate(fenxing):
        if fx[1] == 'B':
            start = i
            break
    if start is None:
        return []

    current = fenxing[start]
    for i in range(start + 1, len(fenxing)):
        nxt = fenxing[i]
        if (current[1] == 'B' and nxt[1] == 'D') or (current[1] == 'D' and nxt[1] == 'B'):
            if nxt[0] - current[0] >= 3:
                bis.append((current[0], nxt[0], current[1], nxt[1]))
                current = nxt
    return bis


def calc_green_area(df: pd.DataFrame, start: int, end: int) -> float:
    sub = df.iloc[start:end + 1]
    return abs(sub[sub['macd'] < 0]['macd'].sum())


def normalize_history_dataframe(df: pd.DataFrame, *, min_bars: int = MIN_DAILY_BARS) -> Optional[pd.DataFrame]:
    if df is None or len(df) < min_bars:
        return None

    result = df.rename(columns={
        '日期': 'date', 'trade_date': 'date', 'trade_dt': 'date', '时间': 'date',
        '开盘': 'open',
        '收盘': 'close', '最高': 'high', '最低': 'low',
        '成交量': 'volume'
    })

    required_cols = ['date', 'open', 'close', 'high', 'low']
    if not all(col in result.columns for col in required_cols):
        return None
    if 'volume' not in result.columns:
        result['volume'] = 0

    result = result[['date', 'open', 'close', 'high', 'low', 'volume']].copy()
    result['date'] = pd.to_datetime(result['date'])
    result = result.sort_values('date').reset_index(drop=True)
    for c in ['open', 'close', 'high', 'low', 'volume']:
        result[c] = pd.to_numeric(result[c], errors='coerce')
    result = result.dropna(subset=['date', 'open', 'close', 'high', 'low'])

    if len(result) < min_bars:
        return None
    return result


def resample_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    return (
        df_daily
        .set_index('date')
        .resample('W-FRI')
        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
        .dropna(subset=['close'])
        .reset_index()
    )


def _count_weekly_pullback_bars(weekly: pd.DataFrame) -> int:
    if weekly is None or len(weekly) < 2:
        return 0

    closes = weekly['close'].tolist()
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            count += 1
        else:
            break
    return count


def check_weekly_uptrend_context(weekly: pd.DataFrame) -> tuple[bool, dict]:
    """周线必须处于上涨走势中的约束：
    1. 最近一段回调周K不超过 6 根；
    2. 再往前 3 倍回调长度区间内，至少一半 high 低于当前价；
    3. 回调前的高点必须是 3 倍窗口里的最高点。
    """
    if weekly is None or len(weekly) < 11:
        return False, {'reason': 'weekly_data_insufficient'}

    pullback_bars = _count_weekly_pullback_bars(weekly)
    if pullback_bars == 0:
        return True, {
            'weekly_pullback_bars': 0,
            'weekly_context_window': 0,
            'weekly_context_high': None,
            'weekly_context_half_below_ratio': None,
            'weekly_pre_pullback_high': None,
            'weekly_current_close': round(float(weekly.iloc[-1]['close']), 2),
            'weekly_uptrend_context_ok': True,
            'weekly_uptrend_reason': 'no_pullback',
        }

    if pullback_bars > MAX_WEEKLY_PULLBACK_BARS:
        return False, {
            'weekly_pullback_bars': pullback_bars,
            'weekly_context_window': pullback_bars * WEEKLY_LOOKBACK_MULTIPLIER,
            'weekly_context_high': None,
            'weekly_context_half_below_ratio': None,
            'weekly_pre_pullback_high': None,
            'weekly_current_close': round(float(weekly.iloc[-1]['close']), 2),
            'weekly_uptrend_context_ok': False,
            'weekly_uptrend_reason': 'pullback_too_long',
        }

    lookback = pullback_bars * WEEKLY_LOOKBACK_MULTIPLIER
    start_idx = max(0, len(weekly) - pullback_bars - lookback)
    end_idx = len(weekly) - pullback_bars
    context = weekly.iloc[start_idx:end_idx]
    if context.empty:
        return False, {
            'weekly_pullback_bars': pullback_bars,
            'weekly_context_window': lookback,
            'weekly_context_high': None,
            'weekly_context_half_below_ratio': None,
            'weekly_pre_pullback_high': None,
            'weekly_current_close': round(float(weekly.iloc[-1]['close']), 2),
            'weekly_uptrend_context_ok': False,
            'weekly_uptrend_reason': 'weekly_context_missing',
        }

    current_close = float(weekly.iloc[-1]['close'])
    context_high = float(context['high'].max())
    pre_pullback_high = float(weekly.iloc[len(weekly) - pullback_bars - 1]['high']) if len(weekly) - pullback_bars - 1 >= 0 else None
    below_ratio = float((context['high'] < current_close).mean()) if len(context) > 0 else 0.0
    ratio_ok = below_ratio >= 0.5
    high_anchor_ok = pre_pullback_high is not None and pre_pullback_high >= context_high
    price_ok = ratio_ok and high_anchor_ok

    if not ratio_ok:
        reason = 'prior_3x_window_less_than_half_below_current'
    elif not high_anchor_ok:
        reason = 'pre_pullback_high_not_context_highest'
    else:
        reason = 'ok'

    return price_ok, {
        'weekly_pullback_bars': pullback_bars,
        'weekly_context_window': lookback,
        'weekly_context_high': round(context_high, 2),
        'weekly_context_half_below_ratio': round(below_ratio, 4),
        'weekly_pre_pullback_high': round(pre_pullback_high, 2) if pre_pullback_high is not None else None,
        'weekly_current_close': round(current_close, 2),
        'weekly_uptrend_context_ok': bool(price_ok),
        'weekly_uptrend_reason': reason,
    }


def _entry_failure_reasons(meta: dict) -> list[str]:
    failed = meta.get('entry_failed_checks')
    if failed:
        return list(failed)

    reason = meta.get('entry_reason')
    if reason and reason not in {'ok', 'score_pass_with_soft_failures', 'baseline_no_entry_confirmation'}:
        return [reason]
    return []


def check_current_price_vs_historical_high(df_daily: pd.DataFrame, buy_idx: int) -> tuple[bool, dict]:
    if df_daily is None or buy_idx < 0 or buy_idx >= len(df_daily):
        return False, {
            'historical_high_ok': False,
            'historical_high_reason': 'invalid_buy_index',
            'historical_high_price': None,
            'buy_close_price': None,
            'current_to_historical_high_ratio': None,
            'min_current_to_historical_high_ratio': round(float(MIN_CURRENT_TO_HISTORICAL_HIGH_RATIO), 4),
        }

    historical_high = float(df_daily.iloc[:buy_idx + 1]['high'].max())
    buy_close = float(df_daily.iloc[buy_idx]['close'])
    ratio = (buy_close / historical_high) if historical_high else 0.0
    ok = ratio >= MIN_CURRENT_TO_HISTORICAL_HIGH_RATIO
    return ok, {
        'historical_high_ok': bool(ok),
        'historical_high_reason': 'ok' if ok else 'current_price_below_historical_high_ratio',
        'historical_high_price': round(historical_high, 2),
        'buy_close_price': round(buy_close, 2),
        'current_to_historical_high_ratio': round(ratio, 4),
        'min_current_to_historical_high_ratio': round(float(MIN_CURRENT_TO_HISTORICAL_HIGH_RATIO), 4),
    }


def check_current_price_vs_ma250(df_daily: pd.DataFrame, buy_idx: int) -> tuple[bool, dict]:
    if df_daily is None or buy_idx < 0 or buy_idx >= len(df_daily):
        return False, {
            'ma250_ok': False,
            'ma250_reason': 'invalid_buy_index',
            'buy_close_price': None,
            'ma250_value': None,
            'current_to_ma250_ratio': None,
            'min_current_to_ma250_ratio': round(float(MIN_CURRENT_TO_MA250_RATIO), 4),
        }

    ma250_series = df_daily['close'].rolling(250, min_periods=250).mean()
    ma250_value = ma250_series.iloc[buy_idx]
    buy_close = float(df_daily.iloc[buy_idx]['close'])

    if pd.isna(ma250_value) or ma250_value <= 0:
        return True, {
            'ma250_ok': True,
            'ma250_reason': 'ma250_unavailable_bypass',
            'buy_close_price': round(buy_close, 2),
            'ma250_value': None,
            'current_to_ma250_ratio': None,
            'min_current_to_ma250_ratio': round(float(MIN_CURRENT_TO_MA250_RATIO), 4),
        }

    ma250_value = float(ma250_value)
    ratio = (buy_close / ma250_value) if ma250_value else 0.0
    ok = ratio >= MIN_CURRENT_TO_MA250_RATIO
    return ok, {
        'ma250_ok': bool(ok),
        'ma250_reason': 'ok' if ok else 'current_price_below_ma250',
        'buy_close_price': round(buy_close, 2),
        'ma250_value': round(ma250_value, 2),
        'current_to_ma250_ratio': round(ratio, 4),
        'min_current_to_ma250_ratio': round(float(MIN_CURRENT_TO_MA250_RATIO), 4),
    }


def check_bullish_volume_retrace_pattern(df_daily: pd.DataFrame, buy_idx: int) -> tuple[bool, dict]:
    if df_daily is None or buy_idx < 0 or buy_idx >= len(df_daily):
        return False, {
            'volume_pattern_ok': False,
            'volume_pattern_reason': 'invalid_buy_index',
            'volume_pattern_anchor_date': None,
            'volume_pattern_anchor_volume': None,
            'volume_pattern_anchor_idx': None,
            'volume_pattern_lookback_bars': VOLUME_BULLISH_LOOKBACK_BARS,
            'volume_pattern_baseline_bars': VOLUME_SPIKE_BASELINE_BARS,
            'volume_pattern_min_spike_ratio': MIN_BULLISH_VOLUME_SPIKE_RATIO,
            'volume_pattern_max_bearish_retrace_ratio': MAX_BEARISH_VOLUME_RETRACE_RATIO,
            'volume_pattern_bearish_violation_date': None,
            'volume_pattern_bearish_violation_volume': None,
            'volume_pattern_bearish_violation_ratio': None,
        }

    if 'volume' not in df_daily.columns:
        return False, {
            'volume_pattern_ok': False,
            'volume_pattern_reason': 'volume_column_missing',
            'volume_pattern_anchor_date': None,
            'volume_pattern_anchor_volume': None,
            'volume_pattern_anchor_idx': None,
            'volume_pattern_lookback_bars': VOLUME_BULLISH_LOOKBACK_BARS,
            'volume_pattern_baseline_bars': VOLUME_SPIKE_BASELINE_BARS,
            'volume_pattern_min_spike_ratio': MIN_BULLISH_VOLUME_SPIKE_RATIO,
            'volume_pattern_max_bearish_retrace_ratio': MAX_BEARISH_VOLUME_RETRACE_RATIO,
            'volume_pattern_bearish_violation_date': None,
            'volume_pattern_bearish_violation_volume': None,
            'volume_pattern_bearish_violation_ratio': None,
        }

    lookback_start = max(0, buy_idx - VOLUME_BULLISH_LOOKBACK_BARS + 1)
    anchor_idx = None
    anchor_volume = None
    anchor_ratio = None

    for idx in range(buy_idx, lookback_start - 1, -1):
        if idx < VOLUME_SPIKE_BASELINE_BARS:
            continue
        row = df_daily.iloc[idx]
        open_price = float(row['open'])
        close_price = float(row['close'])
        volume = float(row.get('volume', 0) or 0)
        if close_price <= open_price or volume <= 0:
            continue

        baseline_window = df_daily.iloc[idx - VOLUME_SPIKE_BASELINE_BARS:idx]
        baseline_volume = float(baseline_window['volume'].mean()) if len(baseline_window) == VOLUME_SPIKE_BASELINE_BARS else 0.0
        if baseline_volume <= 0:
            continue

        ratio = volume / baseline_volume
        if ratio >= MIN_BULLISH_VOLUME_SPIKE_RATIO:
            anchor_idx = idx
            anchor_volume = volume
            anchor_ratio = ratio
            break

    if anchor_idx is None:
        return False, {
            'volume_pattern_ok': False,
            'volume_pattern_reason': 'no_bullish_volume_spike_in_lookback',
            'volume_pattern_anchor_date': None,
            'volume_pattern_anchor_volume': None,
            'volume_pattern_anchor_idx': None,
            'volume_pattern_lookback_bars': VOLUME_BULLISH_LOOKBACK_BARS,
            'volume_pattern_baseline_bars': VOLUME_SPIKE_BASELINE_BARS,
            'volume_pattern_min_spike_ratio': MIN_BULLISH_VOLUME_SPIKE_RATIO,
            'volume_pattern_max_bearish_retrace_ratio': MAX_BEARISH_VOLUME_RETRACE_RATIO,
            'volume_pattern_bearish_violation_date': None,
            'volume_pattern_bearish_violation_volume': None,
            'volume_pattern_bearish_violation_ratio': None,
        }

    for idx in range(anchor_idx + 1, buy_idx + 1):
        row = df_daily.iloc[idx]
        open_price = float(row['open'])
        close_price = float(row['close'])
        volume = float(row.get('volume', 0) or 0)
        if close_price >= open_price:
            continue
        ratio = (volume / anchor_volume) if anchor_volume else None
        if ratio is not None and ratio >= MAX_BEARISH_VOLUME_RETRACE_RATIO:
            return False, {
                'volume_pattern_ok': False,
                'volume_pattern_reason': 'bearish_volume_not_contracted_after_anchor',
                'volume_pattern_anchor_date': str(df_daily.iloc[anchor_idx]['date'])[:10],
                'volume_pattern_anchor_volume': round(anchor_volume, 2),
                'volume_pattern_anchor_idx': int(anchor_idx),
                'volume_pattern_anchor_spike_ratio': round(anchor_ratio, 4) if anchor_ratio is not None else None,
                'volume_pattern_lookback_bars': VOLUME_BULLISH_LOOKBACK_BARS,
                'volume_pattern_baseline_bars': VOLUME_SPIKE_BASELINE_BARS,
                'volume_pattern_min_spike_ratio': MIN_BULLISH_VOLUME_SPIKE_RATIO,
                'volume_pattern_max_bearish_retrace_ratio': MAX_BEARISH_VOLUME_RETRACE_RATIO,
                'volume_pattern_bearish_violation_date': str(row['date'])[:10],
                'volume_pattern_bearish_violation_volume': round(volume, 2),
                'volume_pattern_bearish_violation_ratio': round(ratio, 4),
            }

    return True, {
        'volume_pattern_ok': True,
        'volume_pattern_reason': 'ok',
        'volume_pattern_anchor_date': str(df_daily.iloc[anchor_idx]['date'])[:10],
        'volume_pattern_anchor_volume': round(anchor_volume, 2),
        'volume_pattern_anchor_idx': int(anchor_idx),
        'volume_pattern_anchor_spike_ratio': round(anchor_ratio, 4) if anchor_ratio is not None else None,
        'volume_pattern_lookback_bars': VOLUME_BULLISH_LOOKBACK_BARS,
        'volume_pattern_baseline_bars': VOLUME_SPIKE_BASELINE_BARS,
        'volume_pattern_min_spike_ratio': MIN_BULLISH_VOLUME_SPIKE_RATIO,
        'volume_pattern_max_bearish_retrace_ratio': MAX_BEARISH_VOLUME_RETRACE_RATIO,
        'volume_pattern_bearish_violation_date': None,
        'volume_pattern_bearish_violation_volume': None,
        'volume_pattern_bearish_violation_ratio': None,
    }


def check_weekly_slow_volume_build(weekly: pd.DataFrame) -> tuple[bool, dict]:
    min_bars_required = max(WEEKLY_SLOW_VOLUME_MIN_RED_BARS, WEEKLY_SLOW_VOLUME_BASELINE_BARS + 1)
    if weekly is None or len(weekly) < min_bars_required:
        return False, {
            'weekly_slow_volume_ok': False,
            'weekly_slow_volume_reason': 'weekly_data_insufficient',
            'weekly_slow_volume_consecutive_red_bars': 0,
            'weekly_slow_volume_max_ratio': None,
            'weekly_slow_volume_last_ratio': None,
            'weekly_slow_volume_start_date': None,
            'weekly_slow_volume_end_date': None,
            'weekly_slow_volume_baseline_bars': WEEKLY_SLOW_VOLUME_BASELINE_BARS,
        }

    consecutive = []
    for idx in range(len(weekly) - 1, -1, -1):
        row = weekly.iloc[idx]
        if float(row['close']) > float(row['open']):
            consecutive.append(idx)
        else:
            break

    consecutive_count = len(consecutive)
    if consecutive_count < WEEKLY_SLOW_VOLUME_MIN_RED_BARS:
        return False, {
            'weekly_slow_volume_ok': False,
            'weekly_slow_volume_reason': 'not_enough_consecutive_red_bars',
            'weekly_slow_volume_consecutive_red_bars': consecutive_count,
            'weekly_slow_volume_max_ratio': None,
            'weekly_slow_volume_last_ratio': None,
            'weekly_slow_volume_start_date': None,
            'weekly_slow_volume_end_date': None,
            'weekly_slow_volume_baseline_bars': WEEKLY_SLOW_VOLUME_BASELINE_BARS,
        }

    ordered = list(reversed(consecutive))
    start_date = str(weekly.iloc[ordered[0]]['date'])[:10]
    end_date = str(weekly.iloc[ordered[-1]]['date'])[:10]
    ratios = []
    for idx in ordered:
        if idx < WEEKLY_SLOW_VOLUME_BASELINE_BARS:
            return False, {
                'weekly_slow_volume_ok': False,
                'weekly_slow_volume_reason': 'baseline_history_insufficient',
                'weekly_slow_volume_consecutive_red_bars': consecutive_count,
                'weekly_slow_volume_max_ratio': None,
                'weekly_slow_volume_last_ratio': None,
                'weekly_slow_volume_start_date': start_date,
                'weekly_slow_volume_end_date': end_date,
                'weekly_slow_volume_baseline_bars': WEEKLY_SLOW_VOLUME_BASELINE_BARS,
            }

        baseline_window = weekly.iloc[idx - WEEKLY_SLOW_VOLUME_BASELINE_BARS:idx]
        baseline_volume = float(baseline_window['volume'].mean()) if len(baseline_window) == WEEKLY_SLOW_VOLUME_BASELINE_BARS else 0.0
        volume = float(weekly.iloc[idx].get('volume', 0) or 0)
        if baseline_volume <= 0 or volume <= 0:
            return False, {
                'weekly_slow_volume_ok': False,
                'weekly_slow_volume_reason': 'baseline_volume_invalid',
                'weekly_slow_volume_consecutive_red_bars': consecutive_count,
                'weekly_slow_volume_max_ratio': None,
                'weekly_slow_volume_last_ratio': None,
                'weekly_slow_volume_start_date': start_date,
                'weekly_slow_volume_end_date': end_date,
                'weekly_slow_volume_baseline_bars': WEEKLY_SLOW_VOLUME_BASELINE_BARS,
            }
        ratios.append(volume / baseline_volume)

    max_ratio = max(ratios) if ratios else None
    last_ratio = ratios[-1] if ratios else None
    return True, {
        'weekly_slow_volume_ok': True,
        'weekly_slow_volume_reason': 'ok',
        'weekly_slow_volume_consecutive_red_bars': consecutive_count,
        'weekly_slow_volume_max_ratio': round(max_ratio, 4) if max_ratio is not None else None,
        'weekly_slow_volume_last_ratio': round(last_ratio, 4) if last_ratio is not None else None,
        'weekly_slow_volume_start_date': start_date,
        'weekly_slow_volume_end_date': end_date,
        'weekly_slow_volume_baseline_bars': WEEKLY_SLOW_VOLUME_BASELINE_BARS,
    }


def _is_second_buy_signal(freq_df: pd.DataFrame, freq_label: str) -> tuple[bool, dict]:
    if freq_df is None or len(freq_df) < 20:
        return False, {'czsc_second_buy_ok': False, 'czsc_second_buy_reason': 'data_insufficient', 'czsc_second_buy_freq': freq_label}

    enriched = calc_macd(freq_df)
    fx = find_fenxing(enriched)
    bis = form_bis(fx)
    down_bis = [b for b in bis if b[2] == 'D' and b[3] == 'B']
    up_bis = [b for b in bis if b[2] == 'B' and b[3] == 'D']
    if len(down_bis) < 2 or len(up_bis) < 1:
        return False, {'czsc_second_buy_ok': False, 'czsc_second_buy_reason': 'bi_not_enough', 'czsc_second_buy_freq': freq_label}

    first_down = down_bis[-2]
    second_down = down_bis[-1]
    bridge_up = None
    for up_bi in reversed(up_bis):
        if first_down[1] < up_bi[0] and up_bi[1] < second_down[0]:
            bridge_up = up_bi
            break
    if bridge_up is None:
        return False, {'czsc_second_buy_ok': False, 'czsc_second_buy_reason': 'missing_bridge_up_bi', 'czsc_second_buy_freq': freq_label}

    first_low = float(enriched.iloc[first_down[1]]['low'])
    second_low = float(enriched.iloc[second_down[1]]['low'])
    first_area = calc_green_area(enriched, first_down[0], first_down[1])
    second_area = calc_green_area(enriched, second_down[0], second_down[1])
    low_ok = second_low > first_low
    area_ok = (second_area < first_area * MACD_AREA_RATIO) if second_area > 0 else True
    ok = low_ok and area_ok
    reason = 'ok' if ok else ('second_low_not_higher' if not low_ok else 'second_down_momentum_not_weaker')
    return ok, {
        'czsc_second_buy_ok': bool(ok),
        'czsc_second_buy_reason': reason,
        'czsc_second_buy_freq': freq_label,
        'czsc_second_buy_first_low': round(first_low, 2),
        'czsc_second_buy_second_low': round(second_low, 2),
        'czsc_second_buy_first_area': round(float(first_area), 4),
        'czsc_second_buy_second_area': round(float(second_area), 4),
    }


def check_intraday_second_buy(code: str, buy_date: pd.Timestamp) -> tuple[bool, dict]:
    buy_ts = pd.to_datetime(buy_date)
    candidate_details = []
    reasons = []
    for period, label in [('60', '60分钟'), ('30', '30分钟')]:
        try:
            raw = data_provider.get_kline_minute(code=code, period=period)
        except Exception:
            raw = None
        minute_df = normalize_history_dataframe(raw, min_bars=50) if raw is not None else None
        if minute_df is None:
            reasons.append(f'{label}_data_unavailable')
            continue
        minute_df = minute_df[minute_df['date'] <= buy_ts + timedelta(days=1)].reset_index(drop=True)
        if len(minute_df) < 50:
            reasons.append(f'{label}_data_before_buy_date_insufficient')
            continue
        ok, meta = _is_second_buy_signal(minute_df, label)
        candidate_details.append(meta)
        if ok:
            result = {
                'intraday_second_buy_ok': True,
                'intraday_second_buy_reason': f'{label}_second_buy',
                'intraday_second_buy_freq': label,
                'intraday_second_buy_date': str(buy_ts)[:10],
            }
            result.update(meta)
            return True, result
        reasons.append(f"{label}_{meta.get('czsc_second_buy_reason', 'no_match')}")

    return False, {
        'intraday_second_buy_ok': False,
        'intraday_second_buy_reason': '|'.join(reasons) if reasons else 'no_intraday_match',
        'intraday_second_buy_freq': None,
        'intraday_second_buy_date': str(buy_ts)[:10],
        'intraday_second_buy_candidates': candidate_details,
    }


def check_daily_entry_confirmation(df_daily: pd.DataFrame, buy_idx: int) -> tuple[bool, dict]:
    if df_daily is None or buy_idx < 0 or buy_idx >= len(df_daily):
        return False, {'entry_ok': False, 'entry_reason': 'invalid_buy_index', 'entry_score': 0, 'entry_score_max': 4}

    row = df_daily.iloc[buy_idx]
    prev_close = float(df_daily.iloc[buy_idx - 1]['close']) if buy_idx > 0 else float(row['open'])
    open_price = float(row['open'])
    close_price = float(row['close'])
    low_price = float(row['low'])
    volume = float(row.get('volume', 0) or 0)

    body_rebound_pct = ((close_price - open_price) / open_price) if open_price else 0.0
    close_from_low_pct = ((close_price - low_price) / low_price) if low_price else 0.0
    close_above_prev_close_pct = ((close_price - prev_close) / prev_close) if prev_close else 0.0

    volume_ma5 = float(df_daily.iloc[max(0, buy_idx - 4):buy_idx + 1]['volume'].mean()) if 'volume' in df_daily.columns else 0.0
    entry_volume_ratio = (volume / volume_ma5) if volume_ma5 else 0.0

    body_ok = body_rebound_pct >= MIN_ENTRY_BODY_REBOUND_PCT
    close_from_low_ok = close_from_low_pct >= MIN_ENTRY_CLOSE_FROM_LOW_PCT
    prev_close_ok = close_above_prev_close_pct >= MIN_ENTRY_CLOSE_ABOVE_PREV_CLOSE_PCT
    volume_ok = entry_volume_ratio >= MIN_ENTRY_VOLUME_RATIO

    signal_type = row.get('signal_type') if 'signal_type' in df_daily.columns else None
    min_entry_score = ENTRY_SCORE_THRESHOLD_BY_SIGNAL_TYPE.get(signal_type, MIN_ENTRY_SCORE)

    score_breakdown = {
        'body_rebound': ENTRY_SCORE_WEIGHTS['body_rebound'] if body_ok else 0,
        'close_from_low': ENTRY_SCORE_WEIGHTS['close_from_low'] if close_from_low_ok else 0,
        'prev_close': ENTRY_SCORE_WEIGHTS['prev_close'] if prev_close_ok else 0,
        'volume_ratio': ENTRY_SCORE_WEIGHTS['volume_ratio'] if volume_ok else 0,
    }
    entry_score = int(sum(score_breakdown.values()))
    entry_score_max = int(sum(ENTRY_SCORE_WEIGHTS.values()))
    entry_ok = entry_score >= min_entry_score

    failed_checks = []
    if not body_ok:
        failed_checks.append('entry_body_too_weak')
    if not close_from_low_ok:
        failed_checks.append('entry_close_too_close_to_low')
    if not prev_close_ok:
        failed_checks.append('entry_close_below_prev_close')
    if not volume_ok:
        failed_checks.append('entry_volume_too_low')

    if entry_ok:
        reason = 'ok' if not failed_checks else 'score_pass_with_soft_failures'
    else:
        reason = failed_checks[0] if failed_checks else 'entry_score_below_threshold'

    return entry_ok, {
        'entry_ok': entry_ok,
        'entry_reason': reason,
        'entry_signal_type': signal_type,
        'entry_score': entry_score,
        'entry_min_score_required': int(min_entry_score),
        'entry_score_max': entry_score_max,
        'entry_score_breakdown': score_breakdown,
        'entry_failed_checks': failed_checks,
        'entry_body_ok': bool(body_ok),
        'entry_close_from_low_ok': bool(close_from_low_ok),
        'entry_prev_close_ok': bool(prev_close_ok),
        'entry_volume_ok': bool(volume_ok),
        'entry_body_rebound_pct': round(body_rebound_pct, 4),
        'entry_close_from_low_pct': round(close_from_low_pct, 4),
        'entry_close_above_prev_close_pct': round(close_above_prev_close_pct, 4),
        'entry_volume_ratio': round(entry_volume_ratio, 4),
        'entry_volume_ma5': round(volume_ma5, 2),
    }


def analyze_v3_1_signal(
    df_daily: pd.DataFrame,
    now: Optional[datetime] = None,
    *,
    enforce_weekly_context: bool = False,
    enforce_historical_high_filter: bool = True,
    enforce_ma250_filter: bool = True,
    enforce_volume_pattern: bool = True,
    enforce_entry_confirmation: bool = True,
) -> Optional[dict]:
    now = now or datetime.now()
    if df_daily is None or len(df_daily) < MIN_DAILY_BARS:
        return None

    df_weekly = resample_weekly(df_daily)
    if len(df_weekly) < 20:
        return None

    try:
        daily = calc_macd(df_daily)
        weekly = calc_macd(df_weekly)

        fx_w = find_fenxing(weekly)
        bis_w = form_bis(fx_w)
        fx_d = find_fenxing(daily)
        bis_d = form_bis(fx_d)
        if len(bis_d) < 2:
            return None

        weekly_context_ok, weekly_context_meta = check_weekly_uptrend_context(weekly)
        weekly_gate_reason = 'weekly_context_ok' if weekly_context_ok else 'weekly_context_failed'
        if enforce_weekly_context and not weekly_context_ok:
            return None

        down_bis = [b for b in bis_d if b[2] == 'D' and b[3] == 'B']
        if len(down_bis) < 2:
            return None

        last_bi = down_bis[-1]
        prev_bi = down_bis[-2]

        price_last = float(daily.iloc[last_bi[1]]['low'])
        price_prev = float(daily.iloc[prev_bi[1]]['low'])
        high_at_bi_start = float(daily.iloc[last_bi[0]]['high'])
        pullback_pct = (high_at_bi_start - price_last) / high_at_bi_start
        if pullback_pct < MIN_PULLBACK_PCT:
            return None

        area_last = calc_green_area(daily, last_bi[0], last_bi[1])
        area_prev = calc_green_area(daily, prev_bi[0], prev_bi[1])

        macd_area_divergence = (area_last < area_prev * MACD_AREA_RATIO) if area_last > 0 else True
        higher_low = price_last > price_prev
        zero_axis_pullback = area_last == 0

        if zero_axis_pullback:
            signal_type = 'zero_axis_pullback'
            support_reason = 'MACD 绿柱面积为 0，属于零轴上方强势回踩'
        elif macd_area_divergence:
            signal_type = 'macd_area_divergence'
            support_reason = '最近向下笔 MACD 绿柱面积较前一笔明显缩小'
        elif higher_low:
            signal_type = 'higher_low'
            support_reason = '最近向下笔低点高于前一笔，属于强支撑回调'
        else:
            return None

        buy_date = daily.iloc[last_bi[1]]['date']
        buy_idx = int(last_bi[1])
        historical_high_ok, historical_high_meta = check_current_price_vs_historical_high(daily, buy_idx)
        if enforce_historical_high_filter and not historical_high_ok:
            return None

        ma250_ok, ma250_meta = check_current_price_vs_ma250(daily, buy_idx)
        if enforce_ma250_filter and not ma250_ok:
            return None

        volume_pattern_ok, volume_pattern_meta = check_bullish_volume_retrace_pattern(daily, buy_idx)
        weekly_slow_volume_ok, weekly_slow_volume_meta = check_weekly_slow_volume_build(weekly)
        volume_gate_ok = volume_pattern_ok or weekly_slow_volume_ok
        if enforce_volume_pattern and not volume_gate_ok:
            return None

        daily_for_entry = daily.copy()
        daily_for_entry['signal_type'] = signal_type
        entry_ok, entry_meta = check_daily_entry_confirmation(daily_for_entry, buy_idx)
        if enforce_entry_confirmation and not entry_ok:
            return None

        code = str(df_daily.attrs.get('code', '')).strip()
        if not code:
            return None

        intraday_second_buy_ok, intraday_second_buy_meta = check_intraday_second_buy(code, buy_date)
        intraday_second_buy_meta = {
            **intraday_second_buy_meta,
            'intraday_second_buy_enforced': False,
        }

        days_ago = (now - buy_date).days
        if not (0 <= days_ago <= SIGNAL_WINDOW_DAYS):
            return None

        return {
            'buy_date': str(buy_date)[:10],
            'price': float(daily.iloc[last_bi[1]]['close']),
            'days_ago': int(days_ago),
            'signal_type': signal_type,
            'weekly_trend': 'weekly_gate_relaxed',
            'weekly_context_bypassed': bool(not weekly_context_ok),
            'weekly_gate_reason': weekly_gate_reason,
            'pullback_pct': round(pullback_pct * 100, 2),
            'support_reason': support_reason,
            'macd_area_divergence': bool(macd_area_divergence),
            'higher_low': bool(higher_low),
            'zero_axis_pullback': bool(zero_axis_pullback),
            'last_bi_low': round(price_last, 2),
            'prev_bi_low': round(price_prev, 2),
            'area_last': round(float(area_last), 4),
            'area_prev': round(float(area_prev), 4),
            **entry_meta,
            **historical_high_meta,
            **ma250_meta,
            **volume_pattern_meta,
            'volume_gate_ok': bool(volume_gate_ok),
            'volume_gate_reason': 'daily_volume_spike_retrace' if volume_pattern_ok else 'weekly_slow_volume_build',
            **weekly_slow_volume_meta,
            **intraday_second_buy_meta,
            **weekly_context_meta,
        }
    except Exception:
        return None


def get_history(code: str, lookback_days: int = LOOKBACK_DAYS) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    try:
        start = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')
        end = datetime.now().strftime('%Y%m%d')
        raw = data_provider.get_kline_daily(code=code, start_date=start, end_date=end)
        df_daily = normalize_history_dataframe(raw)
        if df_daily is None:
            return None, None
        df_daily.attrs['code'] = code
        return df_daily, resample_weekly(df_daily)
    except Exception:
        return None, None


def check_signal(
    df_daily: pd.DataFrame,
    df_weekly: Optional[pd.DataFrame] = None,
    now: Optional[datetime] = None,
    **kwargs,
) -> Optional[dict]:
    _ = df_weekly
    return analyze_v3_1_signal(df_daily=df_daily, now=now, **kwargs)


def get_active_stocks(top_n: int = TOP_N_ACTIVE) -> list[tuple[str, str, float, float]]:
    stocks = data_provider.get_stock_list() or []
    enriched = []
    for item in stocks[: max(top_n * 2, top_n)]:
        code = str(item.get('code', '')).strip()
        name = str(item.get('name', code)).strip() or code
        quote = data_provider.get_realtime_quote(code)
        if not quote:
            continue
        turnover = float(quote.get('turnover', 0) or 0)
        enriched.append((code, name, float(quote.get('price', 0) or 0), float(quote.get('change_pct', 0) or 0), turnover))
    enriched.sort(key=lambda x: x[4], reverse=True)
    return [(code, name, price, chg) for code, name, price, chg, _ in enriched[:top_n]]


def scan_active_stocks(top_n: int = TOP_N_ACTIVE, throttle_seconds: float = THROTTLE_SECONDS) -> list[dict]:
    logger.info(f'=== 缠论 v3.1 实时选股 (Top {top_n}) ===')
    stocks = get_active_stocks(top_n=top_n)
    logger.info(f'扫描 {len(stocks)} 只活跃股...')

    results = []
    start_time = time.time()
    for i, (code, name, price, chg) in enumerate(stocks):
        df_daily, _df_weekly = get_history(code)
        if df_daily is None:
            continue

        res = analyze_v3_1_signal(df_daily)
        if res:
            res.update({
                'code': code,
                'name': name,
                'current_price': float(price),
                'change_pct': float(chg),
            })
            results.append(res)
            logger.info(f"🎯 {code} {name} 现价:{price} 涨跌幅:{chg}% 买点:{res['buy_date']} 类型:{res['signal_type']}")

        if (i + 1) % 50 == 0:
            logger.info(f'进度: {i + 1}/{len(stocks)} 耗时:{time.time() - start_time:.0f}s 命中:{len(results)}')
        time.sleep(throttle_seconds)

    results.sort(key=lambda x: x['days_ago'])
    logger.info(f'\n完成! 耗时 {time.time() - start_time:.0f}s, 共 {len(results)} 只')
    return results


def save_results(results: list[dict], output_path: str = 'current_v3_selections.json') -> None:
    with open(output_path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def main() -> None:
    results = scan_active_stocks()
    save_results(results)
    for r in results:
        logger.info(
            f"  {r['code']} {r['name']} 现价:{r['current_price']} 买点:{r['buy_date']} "
            f"(距今{r['days_ago']}天) 类型:{r['signal_type']}"
        )


if __name__ == '__main__':
    main()
