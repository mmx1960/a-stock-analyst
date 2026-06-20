"""
运行 v3.1 回测：优先尝试实时全市场列表；失败时自动回退到本地股票池。
"""
from pathlib import Path

from backtest.strategies.strategy_v3_1_backtest import backtest_random_100_v3_1

FALLBACK_STOCK_FILE = Path('backtest/stocks/fallback_stocks_v3_1.json')

if __name__ == "__main__":
    backtest_random_100_v3_1(fallback_paths=[FALLBACK_STOCK_FILE])
