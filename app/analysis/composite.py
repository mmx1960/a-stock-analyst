"""
综合评分器
整合技术面、基本面、资金面，输出最终评级
"""
from app.analysis.technical import TechnicalAnalyzer
from app.analysis.fundamental import FundamentalAnalyzer
from app.analysis.capital_flow import CapitalFlowAnalyzer


class CompositeAnalyzer:
    """综合分析器 - 整合所有分析维度"""

    def __init__(self):
        self.technical = TechnicalAnalyzer()
        self.fundamental = FundamentalAnalyzer()
        self.capital_flow = CapitalFlowAnalyzer()

    def full_analysis(self, code: str, quote: dict, kline_df,
                      fundamental: dict = None, capital_flow: dict = None,
                      northbound: dict = None) -> dict:
        """
        全维度分析
        返回：综合报告
        """
        result = {
            "code": code,
            "name": quote.get("name", ""),
            "price": quote.get("price", 0),
            "change_pct": quote.get("change_pct", 0),
            "updated_at": quote.get("updated_at", ""),
        }

        # 技术面
        tech = self.technical.analyze(kline_df)
        result["technical"] = tech

        # 基本面
        fund = self.fundamental.analyze(code, quote, fundamental or {})
        result["fundamental"] = fund

        # 资金面
        flow = self.capital_flow.analyze(code, capital_flow, northbound)
        result["capital_flow"] = flow

        # 综合评分（技术 40% + 基本面 35% + 资金面 25%）
        result["composite"] = self._calc_composite(tech, fund, flow)

        # 操作建议
        result["recommendation"] = self._make_recommendation(result["composite"])

        return result

    def _calc_composite(self, tech: dict, fund: dict, flow: dict) -> dict:
        """综合评分计算"""
        tech_score = tech.get("score", {}).get("total", 50) if tech else 50
        fund_score = fund.get("score", {}).get("total", 50) if fund else 50
        flow_score = flow.get("score", {}).get("total", 50) if flow else 50

        # 加权：技术 40% + 基本面 35% + 资金面 25%
        total = int(round(tech_score * 0.4 + fund_score * 0.35 + flow_score * 0.25))

        if total >= 75:
            grade = "A"
            label = "强势"
        elif total >= 60:
            grade = "B"
            label = "偏强"
        elif total >= 45:
            grade = "C"
            label = "中性"
        elif total >= 30:
            grade = "D"
            label = "偏弱"
        else:
            grade = "E"
            label = "弱势"

        return {
            "score": total,
            "grade": grade,
            "label": label,
            "breakdown": {
                "technical": tech_score,
                "fundamental": fund_score,
                "capital_flow": flow_score,
            },
            "weights": {
                "technical": "40%",
                "fundamental": "35%",
                "capital_flow": "25%",
            },
        }

    def _make_recommendation(self, composite: dict) -> dict:
        """生成操作建议"""
        score = composite["score"]

        if score >= 75:
            action = "BUY"
            confidence = "high"
            reason = "多维度评分优秀，技术面强势，基本面扎实"
        elif score >= 60:
            action = "BUY"
            confidence = "medium"
            reason = "整体偏强，可逢低关注"
        elif score >= 45:
            action = "HOLD"
            confidence = "medium"
            reason = "中性震荡，观望为主"
        elif score >= 30:
            action = "SELL"
            confidence = "medium"
            reason = "偏弱走势，注意风险"
        else:
            action = "SELL"
            confidence = "high"
            reason = "多维度评分较差，建议回避"

        return {
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "disclaimer": "以上为技术分析参考，不构成投资建议。股市有风险，投资需谨慎。",
        }
