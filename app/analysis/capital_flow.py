"""
资金流向分析模块
主力资金、北向资金、散户资金追踪
"""
from typing import Optional


class CapitalFlowAnalyzer:
    """资金流向分析器"""

    def analyze(self, code: str, capital_flow: Optional[dict], northbound: Optional[dict] = None) -> dict:
        """
        资金面综合分析
        输入：股票代码、资金流向数据、（可选）北向资金
        输出：资金面评分 + 流向分析
        """
        result = {"code": code}

        if capital_flow:
            result["main_flow"] = self._analyze_main_flow(capital_flow)
            result["retail_flow"] = self._analyze_retail_flow(capital_flow)
            result["flow_signal"] = self._determine_signal(capital_flow)
            result["score"] = self._calc_score(capital_flow, northbound)
        else:
            result["main_flow"] = None
            result["flow_signal"] = "数据不足"
            result["score"] = {"total": 50, "message": "资金流向数据暂不可用"}

        if northbound:
            result["northbound"] = {
                "net_flow": northbound.get("net_flow", 0),
                "signal": "净流入" if northbound.get("net_flow", 0) > 0 else "净流出",
            }

        return result

    def _analyze_main_flow(self, data: dict) -> dict:
        """主力资金流向分析"""
        main_net = data.get("main_net_inflow", 0)
        super_large = data.get("super_large_net", 0)
        large = data.get("large_net", 0)

        return {
            "main_net_inflow": main_net,
            "super_large_net": super_large,
            "large_net": large,
            "main_signal": "主力净流入" if main_net > 0 else "主力净流出",
        }

    def _analyze_retail_flow(self, data: dict) -> dict:
        """散户资金流向"""
        medium = data.get("medium_net", 0)
        small = data.get("small_net", 0)
        retail = medium + small

        return {
            "medium_net": medium,
            "small_net": small,
            "retail_net": retail,
            "retail_signal": "散户净流入" if retail > 0 else "散户净流出",
        }

    def _determine_signal(self, data: dict) -> str:
        """
        综合资金信号
        主力流入 + 散户流出 = 最积极信号（主力吸筹）
        主力流出 + 散户流入 = 最消极信号（主力出货）
        """
        main = data.get("main_net_inflow", 0)
        retail = data.get("medium_net", 0) + data.get("small_net", 0)

        if main > 0 and retail < 0:
            return "strong_buy"  # 主力吸筹
        elif main > 0 and retail > 0:
            return "buy"  # 全线买入
        elif main < 0 and retail > 0:
            return "strong_sell"  # 主力出货
        elif main < 0 and retail < 0:
            return "sell"  # 全线卖出
        else:
            return "neutral"

    def _calc_score(self, capital_flow: dict, northbound: Optional[dict]) -> dict:
        """
        资金面 100 分制评分
        - 主力流向 60 分
        - 散户行为 20 分
        - 北向资金 20 分
        """
        main = capital_flow.get("main_net_inflow", 0)
        retail = capital_flow.get("medium_net", 0) + capital_flow.get("small_net", 0)

        # 主力流向（60 分）
        if main > 1e8:  # > 1 亿
            main_score = 60
        elif main > 5e7:  # > 5000 万
            main_score = 50
        elif main > 0:
            main_score = 35
        elif main > -5e7:
            main_score = 20
        else:
            main_score = 5

        # 散户行为（20 分）- 散户流出反而好（筹码集中）
        if retail < 0:
            retail_score = 20  # 散户流出，筹码集中
        elif retail < 5e7:
            retail_score = 12
        else:
            retail_score = 5  # 散户大举流入，注意风险

        # 北向资金（20 分）
        nb_score = 10  # 默认
        if northbound:
            nb_flow = northbound.get("net_flow", 0)
            if nb_flow > 5e8:  # > 5 亿
                nb_score = 20
            elif nb_flow > 0:
                nb_score = 15
            elif nb_flow > -5e8:
                nb_score = 5
            else:
                nb_score = 0

        total = main_score + retail_score + nb_score

        return {
            "total": total,
            "breakdown": {
                "main_flow": main_score,
                "retail_behavior": retail_score,
                "northbound": nb_score,
            },
            "max": 100,
        }


def format_money(amount: float) -> str:
    """格式化金额"""
    if abs(amount) >= 1e8:
        return f"{amount/1e8:.2f}亿"
    elif abs(amount) >= 1e4:
        return f"{amount/1e4:.2f}万"
    else:
        return f"{amount:.2f}"
