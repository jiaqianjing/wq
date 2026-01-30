"""
Alpha 生成器

生成不同类型的 Alpha 表达式：
- Regular Alphas: 常规 Alpha
- Power Pool Alphas: 高潜力 Alpha
- ATOMs: 原子 Alpha (简单但有效)
- SuperAlphas: 组合 Alpha
"""

import random
from typing import List, Dict, Callable, Tuple
from dataclasses import dataclass
import itertools
import logging

logger = logging.getLogger(__name__)


@dataclass
class AlphaTemplate:
    """Alpha 模板"""
    name: str
    expression: str
    category: str
    description: str
    params: Dict[str, List] = None

    def __post_init__(self):
        if self.params is None:
            self.params = {}


class AlphaGenerator:
    """Alpha 生成器"""

    # 基础数据字段
    DATA_FIELDS = {
        "price": ["close", "open", "high", "low", "vwap"],
        "volume": ["volume", "adv20", "adv60", "adv120"],
        "returns": ["returns", "ts_returns"],
        "fundamental": ["market_cap", "pe", "pb", "roe", "debt_to_equity"]
    }

    # 技术操作符
    TS_OPERATORS = [
        "ts_rank", "ts_delta", "ts_corr", "ts_covariance",
        "ts_sum", "ts_mean", "ts_std", "ts_max", "ts_min",
        "ts_zscore", "ts_skewness", "ts_kurtosis"
    ]

    CROSS_SECTIONAL_OPERATORS = [
        "rank", "zscore", "normalize"
    ]

    ARITHMETIC_OPERATORS = [
        "+", "-", "*", "/", "^", "abs", "sign", "log", "sqrt"
    ]

    def __init__(self):
        self.regular_templates = self._init_regular_templates()
        self.power_pool_templates = self._init_power_pool_templates()
        self.atom_templates = self._init_atom_templates()
        self.superalpha_templates = self._init_superalpha_templates()

    def _init_regular_templates(self) -> List[AlphaTemplate]:
        """初始化 Regular Alpha 模板"""
        return [
            AlphaTemplate(
                name="momentum",
                expression="-ts_corr(close, ts_delay(close, {delay}), {window})",
                category="momentum",
                description="价格动量反转",
                params={"delay": [5, 10, 20], "window": [10, 20, 60]}
            ),
            AlphaTemplate(
                name="volume_price",
                expression="-ts_corr(volume, close, {window})",
                category="volume",
                description="成交量与价格相关性",
                params={"window": [10, 20, 60]}
            ),
            AlphaTemplate(
                name="mean_reversion",
                expression="(close - ts_mean(close, {window})) / ts_std(close, {window})",
                category="mean_reversion",
                description="均值回归",
                params={"window": [10, 20, 60, 120]}
            ),
            AlphaTemplate(
                name="volatility",
                expression="-ts_std(ts_returns(close, 1), {window})",
                category="volatility",
                description="波动率",
                params={"window": [10, 20, 60]}
            ),
            AlphaTemplate(
                name="price_range",
                expression="(high - low) / close",
                category="volatility",
                description="日内波动率",
                params={}
            ),
            AlphaTemplate(
                name="rsi_like",
                expression="ts_sum(max(close - ts_delay(close, 1), 0), {window}) / ts_sum(abs(close - ts_delay(close, 1)), {window})",
                category="momentum",
                description="类 RSI 指标",
                params={"window": [14, 20, 30]}
            ),
            AlphaTemplate(
                name="vwap_deviation",
                expression="(close - vwap) / close",
                category="mean_reversion",
                description="VWAP 偏离度",
                params={}
            ),
            AlphaTemplate(
                name="volume_momentum",
                expression="rank(volume) * (close - open) / close",
                category="volume",
                description="成交量动量",
                params={}
            ),
        ]

    def _init_power_pool_templates(self) -> List[AlphaTemplate]:
        """初始化 Power Pool Alpha 模板（高潜力）"""
        return [
            AlphaTemplate(
                name="advanced_momentum",
                expression="-ts_rank(ts_corr(rank(close), rank(volume), {window1}), {window2})",
                category="momentum",
                description="高级动量 - 价格和成交量排名相关性",
                params={"window1": [10, 20], "window2": [5, 10]}
            ),
            AlphaTemplate(
                name="complex_mean_reversion",
                expression="rank(ts_mean(close, {window1}) - ts_mean(close, {window2})) * rank(volume)",
                category="mean_reversion",
                description="复杂均值回归 - 双均线",
                params={"window1": [5, 10], "window2": [20, 60]}
            ),
            AlphaTemplate(
                name="volatility_skew",
                expression="ts_skewness(ts_returns(close, 1), {window})",
                category="volatility",
                description="收益偏度",
                params={"window": [20, 60, 120]}
            ),
            AlphaTemplate(
                name="correlation_decay",
                expression="-ts_corr(rank(ts_delta(close, {delay1})), rank(volume), {window})",
                category="volume",
                description="价格变化与成交量相关性",
                params={"delay1": [1, 5], "window": [10, 20, 60]}
            ),
            AlphaTemplate(
                name="adaptive_momentum",
                expression="ts_delta(close, {delay}) / ts_std(close, {std_window}) * rank(volume)",
                category="momentum",
                description="自适应动量",
                params={"delay": [5, 10], "std_window": [20, 60]}
            ),
            AlphaTemplate(
                name="momentum_continuation",
                expression="ts_corr(ts_returns(close, {delay1}), ts_returns(close, {delay2}), {window})",
                category="momentum",
                description="动量持续性",
                params={"delay1": [1, 5], "delay2": [10, 20], "window": [20, 60]}
            ),
            AlphaTemplate(
                name="volume_breakout",
                expression="rank(volume / ts_mean(volume, {window})) * (close - ts_delay(close, {delay})) / ts_delay(close, {delay})",
                category="volume",
                description="成交量突破",
                params={"window": [20, 60], "delay": [1, 5]}
            ),
        ]

    def _init_atom_templates(self) -> List[AlphaTemplate]:
        """初始化 ATOM 模板（简单、基础但有效的 Alpha）"""
        return [
            AlphaTemplate(
                name="simple_reversal",
                expression="-ts_returns(close, {delay})",
                category="reversal",
                description="简单反转",
                params={"delay": [1, 5, 10]}
            ),
            AlphaTemplate(
                name="volume_rank",
                expression="rank(volume)",
                category="volume",
                description="成交量排名",
                params={}
            ),
            AlphaTemplate(
                name="price_position",
                expression="(close - ts_min(low, {window})) / (ts_max(high, {window}) - ts_min(low, {window}))",
                category="momentum",
                description="价格在区间中的位置",
                params={"window": [10, 20, 60]}
            ),
            AlphaTemplate(
                name="simple_momentum",
                expression="(close - ts_delay(close, {delay})) / ts_delay(close, {delay})",
                category="momentum",
                description="简单动量",
                params={"delay": [5, 10, 20]}
            ),
            AlphaTemplate(
                name="vwap_ratio",
                expression="close / vwap",
                category="mean_reversion",
                description="价格与 VWAP 比率",
                params={}
            ),
            AlphaTemplate(
                name="range_normalized",
                expression="(high - low) / ts_mean(close, {window})",
                category="volatility",
                description="标准化价格区间",
                params={"window": [20, 60]}
            ),
        ]

    def _init_superalpha_templates(self) -> List[AlphaTemplate]:
        """初始化 SuperAlpha 模板（组合多个简单 Alpha）"""
        return [
            AlphaTemplate(
                name="momentum_volume_combo",
                expression="0.5 * rank(-ts_returns(close, {delay1})) + 0.5 * rank(volume / ts_mean(volume, {window}))",
                category="combined",
                description="动量成交量组合",
                params={"delay1": [5, 10], "window": [20, 60]}
            ),
            AlphaTemplate(
                name="triple_screen",
                expression="(rank(ts_delta(close, {delay1})) + rank(ts_delta(volume, {delay2})) + rank(ts_std(close, {window}))) / 3",
                category="combined",
                description="三重筛选",
                params={"delay1": [5, 10], "delay2": [1, 5], "window": [20, 60]}
            ),
            AlphaTemplate(
                name="mean_reversion_vol",
                expression="rank((close - ts_mean(close, {window1})) / ts_std(close, {window1})) * (1 - rank(ts_std(close, {window2})))",
                category="combined",
                description="均值回归波动率调整",
                params={"window1": [20, 60], "window2": [20, 60]}
            ),
            AlphaTemplate(
                name="momentum_quality",
                expression="ts_corr(rank(ts_returns(close, {delay})), rank(volume), {window}) * rank(-abs(ts_returns(close, {delay})))",
                category="combined",
                description="动量质量",
                params={"delay": [5, 10], "window": [20, 60]}
            ),
        ]

    def _fill_template(self, template: AlphaTemplate) -> List[str]:
        """填充模板参数，生成具体的 Alpha 表达式"""
        if not template.params:
            return [template.expression]

        # 生成所有参数组合
        param_names = list(template.params.keys())
        param_values = [template.params[p] for p in param_names]

        expressions = []
        for combo in itertools.product(*param_values):
            expr = template.expression
            for name, value in zip(param_names, combo):
                expr = expr.replace(f"{{{name}}}", str(value))
            expressions.append(expr)

        return expressions

    def generate_regular_alphas(self, count: int = 10) -> List[Dict]:
        """
        生成 Regular Alphas

        Args:
            count: 要生成的数量

        Returns:
            List[Dict]: Alpha 配置列表
        """
        alphas = []
        all_expressions = []

        for template in self.regular_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append({
                    "expression": expr,
                    "name": template.name,
                    "category": template.category,
                    "type": "regular"
                })

        # 随机选择指定数量
        selected = random.sample(all_expressions, min(count, len(all_expressions)))
        return selected

    def generate_power_pool_alphas(self, count: int = 10) -> List[Dict]:
        """
        生成 Power Pool Alphas（高潜力 Alpha）

        Args:
            count: 要生成的数量

        Returns:
            List[Dict]: Alpha 配置列表
        """
        alphas = []
        all_expressions = []

        for template in self.power_pool_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append({
                    "expression": expr,
                    "name": template.name,
                    "category": template.category,
                    "type": "power_pool"
                })

        selected = random.sample(all_expressions, min(count, len(all_expressions)))
        return selected

    def generate_atoms(self, count: int = 10) -> List[Dict]:
        """
        生成 ATOMs（简单、基础的 Alpha）

        Args:
            count: 要生成的数量

        Returns:
            List[Dict]: Alpha 配置列表
        """
        alphas = []
        all_expressions = []

        for template in self.atom_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append({
                    "expression": expr,
                    "name": template.name,
                    "category": template.category,
                    "type": "atom"
                })

        selected = random.sample(all_expressions, min(count, len(all_expressions)))
        return selected

    def generate_superalphas(self, count: int = 5) -> List[Dict]:
        """
        生成 SuperAlphas（组合 Alpha）

        Args:
            count: 要生成的数量

        Returns:
            List[Dict]: Alpha 配置列表
        """
        alphas = []
        all_expressions = []

        for template in self.superalpha_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append({
                    "expression": expr,
                    "name": template.name,
                    "category": template.category,
                    "type": "superalpha"
                })

        selected = random.sample(all_expressions, min(count, len(all_expressions)))
        return selected

    def generate_all_types(self, regular_count: int = 5, power_pool_count: int = 5,
                          atom_count: int = 5, superalpha_count: int = 3) -> Dict[str, List[Dict]]:
        """
        生成所有类型的 Alpha

        Args:
            regular_count: Regular Alpha 数量
            power_pool_count: Power Pool Alpha 数量
            atom_count: ATOM 数量
            superalpha_count: SuperAlpha 数量

        Returns:
            Dict: 按类型分类的 Alpha 列表
        """
        return {
            "regular": self.generate_regular_alphas(regular_count),
            "power_pool": self.generate_power_pool_alphas(power_pool_count),
            "atoms": self.generate_atoms(atom_count),
            "superalphas": self.generate_superalphas(superalpha_count)
        }

    def generate_custom_alpha(self, base_expression: str,
                             variations: List[Tuple[str, List]]) -> List[str]:
        """
        基于基础表达式生成变体

        Args:
            base_expression: 基础表达式，使用 {param} 占位符
            variations: 参数变化列表 [(param_name, [values])]

        Returns:
            List[str]: 生成的表达式列表
        """
        param_names = [v[0] for v in variations]
        param_values = [v[1] for v in variations]

        expressions = []
        for combo in itertools.product(*param_values):
            expr = base_expression
            for name, value in zip(param_names, combo):
                expr = expr.replace(f"{{{name}}}", str(value))
            expressions.append(expr)

        return expressions

    def generate_101_alphas_variations(self) -> List[Dict]:
        """
        生成 WorldQuant 101 Alphas 的变体
        基于经典论文 "101 Formulaic Alphas"
        """
        alpha_101_variations = [
            {
                "expression": "rank(ts_corr(ts_sum(((high * 0.508285) + (low * (1 - 0.508285))), {window1}), ts_sum(close, {window2}), {window3}))",
                "name": "alpha_101_var_1",
                "category": "volume",
                "params": {"window1": [8, 16], "window2": [8, 16], "window3": [6, 12]}
            },
            {
                "expression": "-ts_corr(open, volume, {window})",
                "name": "alpha_101_var_2",
                "category": "volume",
                "params": {"window": [10, 20, 60]}
            },
            {
                "expression": "-ts_rank(abs(ts_delta(close, {delay})), {window})",
                "name": "alpha_101_var_3",
                "category": "volatility",
                "params": {"delay": [7], "window": [60, 120]}
            },
            {
                "expression": "-rank(ts_corr(rank(low), rank(volume), {window}))",
                "name": "alpha_101_var_4",
                "category": "volume",
                "params": {"window": [10, 20]}
            },
            {
                "expression": "rank((open - ts_sum(vwap, {window1}) / {window1})) * -abs(rank((close - vwap)))",
                "name": "alpha_101_var_5",
                "category": "mean_reversion",
                "params": {"window1": [10, 20]}
            },
            {
                "expression": "-rank(ts_delta(ts_corr(high, volume, {window1}), {delay}) * rank(ts_corr(rank(close), rank(volume), {window2})))",
                "name": "alpha_101_var_6",
                "category": "combined",
                "params": {"window1": [5, 10], "delay": [5], "window2": [60, 120]}
            },
            {
                "expression": "rank(close - open) / rank(high - low)",
                "name": "alpha_101_var_7",
                "category": "momentum",
                "params": {}
            },
            {
                "expression": "rank(ts_max((vwap - close), {window})) / rank(ts_min((vwap - close), {window}) + 0.001)",
                "name": "alpha_101_var_8",
                "category": "mean_reversion",
                "params": {"window": [3, 5, 10]}
            },
        ]

        results = []
        for alpha in alpha_101_variations:
            template = AlphaTemplate(
                name=alpha["name"],
                expression=alpha["expression"],
                category=alpha["category"],
                description="101 Alpha 变体",
                params=alpha.get("params", {})
            )
            exprs = self._fill_template(template)
            for expr in exprs:
                results.append({
                    "expression": expr,
                    "name": alpha["name"],
                    "category": alpha["category"],
                    "type": "regular"
                })

        return results
