"""
A 股分析系统 - Web 服务
Flask API + 简单前端
"""
import os
import threading
import numpy as np
import pandas as pd
from datetime import datetime

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from app.core.config import PORT, DEBUG, BASE_DIR
from app.core.data_provider import data_provider
from app.analysis.composite import CompositeAnalyzer

app = Flask(__name__,
    template_folder=str(BASE_DIR / "app" / "web" / "templates"),
    static_folder=str(BASE_DIR / "app" / "web" / "static"),
)
CORS(app)
analyzer = CompositeAnalyzer()


class NumpyEncoder:
    """处理 numpy 类型的 JSON 序列化"""
    @staticmethod
    def _convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {k: NumpyEncoder._convert(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [NumpyEncoder._convert(i) for i in obj]
        return obj


# ====== 启动时预热 ======

def _warmup_cache():
    """后台预热：预加载全量股票列表（约 60s）"""
    def _warm():
        try:
            print("⏳ 正在预热数据缓存（加载股票列表，约需 1 分钟）...")
            data_provider.get_stock_list()
            print("✅ 数据缓存预热完成！")
        except Exception as e:
            print(f"⚠️ 缓存预热失败: {e}")

    threading.Thread(target=_warm, daemon=True).start()


# ====== 页面路由 ======

@app.route("/")
def index():
    """首页"""
    return render_template("index.html")


@app.route("/stock/<code>")
def stock_detail(code):
    """个股详情页"""
    return render_template("stock_detail.html", code=code)


# ====== API 路由 ======

@app.route("/api/quote/<code>")
def api_quote(code):
    """获取个股实时行情"""
    code = code.split(".")[0]  # 去掉后缀
    quote = data_provider.get_realtime_quote(code)
    if not quote:
        return jsonify({"error": f"未找到股票 {code}", "code": code}), 404
    return jsonify(quote)


@app.route("/api/kline/<code>")
def api_kline(code):
    """获取 K 线数据"""
    code = code.split(".")[0]
    period = request.args.get("period", "daily")
    start = request.args.get("start", "")
    end = request.args.get("end", "")

    try:
        # 分钟 K 线走独立接口
        if period in ("5", "15", "30", "60"):
            df = data_provider.get_kline_minute(code, period=period)
        elif period == "weekly":
            df = data_provider.get_kline_daily(code, period="weekly", start_date=start, end_date=end)
        else:
            df = data_provider.get_kline_daily(code, period="daily", start_date=start, end_date=end)
    except Exception as e:
        print(f"[DEBUG] KLINE ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"K 线数据异常: {e}"}), 500

    if df is None:
        return jsonify({"error": f"无 K 线数据 {code}"}), 404
    if isinstance(df, pd.DataFrame) and df.empty:
        return jsonify({"error": f"K 线数据为空 {code}"}), 404

    records = df.tail(120).copy()
    # 统一日期字段为 date
    if "datetime" in records.columns and "date" not in records.columns:
        records = records.rename(columns={"datetime": "date"})
    if "date" in records.columns:
        records["date"] = records["date"].astype(str)
    return jsonify(records.to_dict("records"))


@app.route("/api/czsc/<code>")
def api_czsc(code):
    """缠论分析（支持级别切换）"""
    code = code.split(".")[0]
    period = request.args.get("period", "daily")

    # 获取对应级别的 K 线
    if period in ("5", "15", "30", "60"):
        df = data_provider.get_kline_minute(code, period=period)
    elif period == "weekly":
        df = data_provider.get_kline_daily(code, period="weekly")
    else:
        df = data_provider.get_kline_daily(code)

    if df is None or df.empty:
        return jsonify({"available": False}), 200

    # 级别映射
    freq_map = {
        "daily": "日线", "weekly": "周线",
        "5": "5分钟", "15": "15分钟", "30": "30分钟", "60": "60分钟",
    }
    freq_label = freq_map.get(period, "日线")

    # 缠论分析
    czsc_result = None
    try:
        from app.analysis.czsc_analyzer import CZSCAnalyzer
        czsc = CZSCAnalyzer()
        czsc_result = czsc.analyze(df, freq=freq_label)
    except Exception as e:
        print(f"[CZSC ERROR] {e}")
        import traceback
        traceback.print_exc()

    if czsc_result:
        return jsonify(czsc_result)
    return jsonify({"available": False}), 200


@app.route("/api/analysis/<code>")
def api_analysis(code):
    """全维度分析"""
    code = code.split(".")[0]

    # 获取所有数据
    quote = data_provider.get_realtime_quote(code)
    if not quote:
        return jsonify({"error": f"未找到股票 {code}"}), 404

    kline_df = data_provider.get_kline_daily(code)
    fundamental = data_provider.get_fundamental(code)
    capital_flow = data_provider.get_capital_flow(code)
    northbound = data_provider.get_northbound_flow()

    # 综合分析
    result = analyzer.full_analysis(code, quote, kline_df, fundamental, capital_flow, northbound)

    # 缠论分析
    czsc_result = None
    try:
        from app.analysis.czsc_analyzer import CZSCAnalyzer
        czsc = CZSCAnalyzer()
        czsc_result = czsc.analyze(kline_df)
    except Exception:
        pass

    # 转换 numpy 类型为原生 Python 类型
    result = NumpyEncoder._convert(result)
    if czsc_result:
        result["czsc"] = czsc_result
    return jsonify(result)


@app.route("/api/market")
def api_market():
    """市场概览"""
    overview = data_provider.get_market_overview()
    if not overview:
        return jsonify({"error": "市场数据获取失败"}), 500
    return jsonify(overview)


@app.route("/api/sectors")
def api_sectors():
    """板块排行"""
    indicator = request.args.get("indicator", "今日涨跌排名")
    sectors = data_provider.get_sector_rank(indicator)
    if sectors is None:
        return jsonify({"error": "板块数据获取失败"}), 500
    return jsonify(sectors[:30])  # 返回前 30


@app.route("/api/portfolio")
def api_portfolio():
    """持仓分析（示例）"""
    # TODO: 从数据库或配置文件加载真实持仓
    portfolio = []
    results = []
    for holding in portfolio:
        code = holding.get("code", "")
        quote = data_provider.get_realtime_quote(code)
        if quote:
            quote["shares"] = holding.get("shares", 0)
            quote["avg_cost"] = holding.get("avg_cost", 0)
            quote["pnl"] = (quote["price"] - quote["avg_cost"]) * quote["shares"]
            results.append(quote)
    return jsonify(results)


@app.route("/api/watchlist")
def api_watchlist():
    """获取缠论回测 Top 10 自选股"""
    import json
    watchlist_path = BASE_DIR / "app" / "data" / "watchlist_top10.json"
    try:
        with open(watchlist_path, 'r') as f:
            watchlist = json.load(f)
        # 补充实时行情
        for item in watchlist:
            quote = data_provider.get_realtime_quote(item['code'])
            if quote:
                item['current_price'] = quote.get('price', 0)
                item['change_pct'] = quote.get('change_pct', 0)
                item['pe'] = quote.get('pe', 0)
                item['pb'] = quote.get('pb', 0)
                item['market_cap'] = quote.get('market_cap', 0)
        return jsonify(watchlist)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/watchlist")
def watchlist_page():
    """自选股页面"""
    return render_template("watchlist.html")


# ====== 启动 ======

if __name__ == "__main__":
    # 后台预热缓存
    _warmup_cache()

    print(f"\n{'='*50}")
    print(f"  A 股分析系统启动")
    print(f"  访问地址: http://localhost:{PORT}")
    print(f"  个股分析: http://localhost:{PORT}/stock/600519")
    print(f"  API 文档: http://localhost:{PORT}/api/market")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
