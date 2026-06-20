"""
基本面分析模块
PE/PB/ROE/盈利能力/成长性 评估
"""
from typing import Optional


class FundamentalAnalyzer:
    """基本面分析器"""

    def analyze(self, code: str, quote: dict, fundamental: dict) -> Optional[dict]:
        """
        综合基本面分析
        输入：股票代码、实时行情、财务数据
        输出：基本面评分 + 详细分析
        """
        if not fundamental:
            return None

        result = {
            "code": code,
            "industry": fundamental.get("industry", "未知"),
            "region": fundamental.get("region", "未知"),
            "list_date": fundamental.get("list_date", "未知"),
            "valuation": self._evaluate_valuation(quote),
            "profitability": self._evaluate_profitability(fundamental),
            "growth": self._evaluate_growth(fundamental),
        }

        result["score"] = self._calc_score(result)
        return result

    def _evaluate_valuation(self, quote: dict) -> dict:
        """估值分析"""
        pe = quote.get("pe", 0)
        pb = quote.get("pb", 0)

        # PE 评级
        if 0 < pe <= 15: pe_rating = "低估"
        elif pe <= 30: pe_rating = "合理"
        elif pe <= 60: pe_rating = "偏高"
        else: pe_rating = "高估"

        # PB 评级
        if 0 < pb <= 2: pb_rating = "低估"
        elif pb <= 5: pb_rating = "合理"
        elif pb <= 10: pb_rating = "偏高"
        else: pb_rating = "高估"

        return {
            "pe": pe,
            "pb": pb,
            "pe_rating": pe_rating,
            "pb_rating": pb_rating,
            "market_cap": quote.get("market_cap", 0),
        }

    def _evaluate_profitability(self, fundamental: dict) -> dict:
        """盈利能力分析"""
        roe = fundamental.get("roe", 0)
        gross_margin = fundamental.get("gross_margin", 0)
        net_margin = fundamental.get("net_margin", 0)

        if roe >= 20: roe_rating = "优秀"
        elif roe >= 15: roe_rating = "良好"
        elif roe >= 8: roe_rating = "一般"
        else: roe_rating = "较差"

        return {
            "roe": roe,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "revenue": fundamental.get("revenue", 0),
            "net_profit": fundamental.get("net_profit", 0),
            "roe_rating": roe_rating,
        }

    def _evaluate_growth(self, fundamental: dict) -> dict:
        """成长性分析（基于可用数据简化）"""
        revenue = fundamental.get("revenue", 0)
        net_profit = fundamental.get("net_profit", 0)

        # 简化：有正向营收和利润就算健康成长
        if revenue > 0 and net_profit > 0:
            growth_rating = "正向增长"
        elif revenue > 0:
            growth_rating = "亏损中"
        else:
            growth_rating = "数据不足"

        return {"growth_rating": growth_rating, "data_available": revenue > 0}

    def _calc_score(self, result: dict) -> dict:
        """
        基本面 100 分制评分
        - 估值 40 分
        - 盈利能力 40 分
        - 成长性 20 分
        """
        # 估值（40 分）- PE/PB 越低越好（价值投资视角）
        pe = result["valuation"]["pe"]
        pb = result["valuation"]["pb"]

        if 5 <= pe <= 20: val_score = 35
        elif 0 < pe <= 30: val_score = 25
        elif pe > 30: val_score = 10
        else: val_score = 5  # 负 PE

        if pb <= 3: val_score += 5
        elif pb <= 5: val_score += 3
        else: val_score += 0
        val_score = min(40, val_score)

        # 盈利能力（40 分）
        roe = result["profitability"]["roe"]
        if roe >= 20: profit_score = 38
        elif roe >= 15: profit_score = 30
        elif roe >= 10: profit_score = 22
        elif roe >= 5: profit_score = 12
        else: profit_score = 5

        # 有正向利润加分
        if result["profitability"]["net_profit"] > 0: profit_score += 2
        profit_score = min(40, profit_score)

        # 成长性（20 分）
        if result["growth"]["growth_rating"] == "正向增长":
            growth_score = 18
        elif result["growth"]["data_available"]:
            growth_score = 10
        else:
            growth_score = 5

        total = round(val_score + profit_score + growth_score)

        return {
            "total": total,
            "breakdown": {
                "valuation": val_score,
                "profitability": profit_score,
                "growth": growth_score,
            },
            "max": 100,
        }
