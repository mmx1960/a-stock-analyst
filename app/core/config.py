"""
A 股分析系统 - 核心配置
"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # app/core/config.py → 项目根目录

# Flask 配置
SECRET_KEY = os.getenv("SECRET_KEY", "a-stock-analyst-dev-key-change-me")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
PORT = int(os.getenv("PORT", "8888"))

# AI 配置（兼容 OpenAI API 格式）
AI_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", ""),
    "base_url": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
    "model": os.getenv("AI_MODEL", "gpt-4"),
    "timeout": 180,
    "max_retries": 2,
}

# 缓存配置（TTL，秒）
CACHE_CONFIG = {
    "realtime_quote": 60,        # 实时行情 1 分钟
    "kline_daily": 300,          # 日线 5 分钟
    "kline_minute": 120,         # 分钟线 2 分钟
    "fundamental": 3600,         # 基本面 1 小时
    "market_overview": 120,      # 市场概览 2 分钟
    "stock_list": 3600,          # 股票列表 1 小时
}

# 请求限流
MIN_REQUEST_INTERVAL = 0.2  # 秒

# 持仓配置
PORTFOLIO = []  # 从 .env 或数据库加载

# 预警阈值
ALERTS = {
    "price_change": 0.05,       # 5%
    "volume_spike": 3.0,        # 3 倍
    "portfolio_drawdown": 0.10, # 10%
}

# 多源数据架构配置
DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", str(BASE_DIR / "data" / "ashare.duckdb")))

DATA_SOURCE_CONFIG = {
    "primary_market_source": os.getenv("PRIMARY_MARKET_SOURCE", "mootdx"),
    "secondary_market_source": os.getenv("SECONDARY_MARKET_SOURCE", "tencent"),
    "low_freq_source": os.getenv("LOW_FREQ_SOURCE", "akshare"),
    "hotspot_source": os.getenv("HOTSPOT_SOURCE", "kaipanla"),
    "historical_storage": os.getenv("HISTORICAL_STORAGE", "duckdb"),
    "duckdb_read_through": os.getenv("DUCKDB_READ_THROUGH", "true").lower() == "true",
}

MOOTDX_CONFIG = {
    "host": os.getenv("TDX_HOST", "127.0.0.1"),
    "port": int(os.getenv("TDX_PORT", "7709")),
    "bestip_timeout": float(os.getenv("TDX_BESTIP_TIMEOUT", "3.0")),
}

TENCENT_CONFIG = {
    "base_url": os.getenv("TENCENT_API", "https://qt.gtimg.cn"),
    "timeout": float(os.getenv("TENCENT_TIMEOUT", "8.0")),
}

AKSHARE_LOW_FREQ_CONFIG = {
    "rate_limit_seconds": float(os.getenv("AKSHARE_RATE_LIMIT", "5")),
}

KAIPANLA_CONFIG = {
    "rate_limit_seconds": float(os.getenv("KAIPANLA_RATE_LIMIT", "0.5")),
}

IWENCAI_CONFIG = {
    "cookie": os.getenv("IWENCAI_COOKIE", ""),
    "rate_limit_seconds": float(os.getenv("IWENCAI_RATE_LIMIT", "30")),
}
