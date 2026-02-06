"""
Alpha 生成器

基于 WorldQuant「101 Formulaic Alphas」论文与市场规律设计：
- 持仓周期约 0.6–6.4 天、低两两相关性（约 15.9%）、收益与波动相关
- 使用 rank/ts_* 组合、adv 归一化、防除零，提高稳健性

生成类型：
- Regular Alphas: 常规 Alpha（动量/反转/成交量/波动率/均值回归）
- Power Pool Alphas: 高潜力组合
- ATOMs: 原子 Alpha（简单有效）
- SuperAlphas: 多因子组合
"""

import random
from typing import List, Dict, Tuple
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
        "fundamental": ["market_cap", "pe", "pb", "roe", "debt_to_equity"],
    }

    # 技术操作符
    TS_OPERATORS = [
        "ts_rank",
        "ts_delta",
        "ts_corr",
        "ts_covariance",
        "ts_sum",
        "ts_mean",
        "ts_std",
        "ts_max",
        "ts_min",
        "ts_zscore",
        "ts_skewness",
        "ts_kurtosis",
    ]

    CROSS_SECTIONAL_OPERATORS = ["rank", "zscore", "normalize"]

    ARITHMETIC_OPERATORS = ["+", "-", "*", "/", "^", "abs", "sign", "log", "sqrt"]

    def __init__(self):
        self.regular_templates = self._init_regular_templates()
        self.power_pool_templates = self._init_power_pool_templates()
        self.atom_templates = self._init_atom_templates()
        self.superalpha_templates = self._init_superalpha_templates()

    def _init_regular_templates(self) -> List[AlphaTemplate]:
        """初始化 Regular Alpha 模板（结合 101 Alphas 与市场规律）"""
        return [
            # 动量/反转：短期反转、动量持续性（研报：0.6-6.4 天持仓）
            AlphaTemplate(
                name="momentum_reversal",
                expression="-ts_corr(close, ts_delay(close, {delay}), {window})",
                category="momentum",
                description="价格动量反转",
                params={"delay": [3, 5, 10], "window": [10, 20, 30]},
            ),
            AlphaTemplate(
                name="volume_price",
                expression="-ts_corr(volume, close, {window})",
                category="volume",
                description="成交量与价格相关性（101 风格）",
                params={"window": [10, 20]},
            ),
            AlphaTemplate(
                name="mean_reversion",
                expression="rank((close - ts_mean(close, {window})) / (ts_std(close, {window}) + 0.001))",
                category="mean_reversion",
                description="截面均值回归（rank 增强稳健性）",
                params={"window": [10, 20, 60]},
            ),
            AlphaTemplate(
                name="bollinger_reversion",
                expression="-rank((close - ts_mean(close, {window})) / (ts_std(close, {window}) + 0.001))",
                category="mean_reversion",
                description="布林带式均值回归（负向偏离）",
                params={"window": [10, 20, 60]},
            ),
            AlphaTemplate(
                name="volatility_low",
                expression="-rank(ts_std(ts_returns(close, 1), {window}))",
                category="volatility",
                description="低波动因子（波动率与收益正相关）",
                params={"window": [20, 60]},
            ),
            AlphaTemplate(
                name="price_range",
                expression="rank((high - low) / (close + 0.001))",
                category="volatility",
                description="日内波动率截面",
                params={},
            ),
            AlphaTemplate(
                name="rsi_like",
                expression="rank(ts_sum(max(close - ts_delay(close, 1), 0), {window}) / (ts_sum(abs(close - ts_delay(close, 1)), {window}) + 0.001))",
                category="momentum",
                description="类 RSI 截面",
                params={"window": [14, 20]},
            ),
            AlphaTemplate(
                name="vwap_deviation",
                expression="rank((close - vwap) / (close + 0.001))",
                category="mean_reversion",
                description="VWAP 偏离度截面",
                params={},
            ),
            AlphaTemplate(
                name="vwap_reversion",
                expression="rank((vwap - close) / (ts_std(close, {window}) + 0.001))",
                category="mean_reversion",
                description="VWAP 反转强度（标准化）",
                params={"window": [10, 20]},
            ),
            AlphaTemplate(
                name="volume_momentum",
                expression="rank(volume / (adv20 + 0.001)) * rank((close - open) / (close + 0.001))",
                category="volume",
                description="相对成交量 × 日内收益（adv 归一化）",
                params={},
            ),
            # 101 风格：open-volume, rank(close-open)/rank(high-low)
            AlphaTemplate(
                name="open_volume_corr",
                expression="-ts_corr(open, volume, {window})",
                category="volume",
                description="开盘价与成交量相关性",
                params={"window": [10, 20]},
            ),
            AlphaTemplate(
                name="intraday_strength",
                expression="rank(close - open) / (rank(high - low) + 0.001)",
                category="momentum",
                description="日内强度（101 #7 风格）",
                params={},
            ),
            AlphaTemplate(
                name="volume_spike_reversal",
                expression="-rank(ts_delta(close, {delay})) * rank(volume / (adv20 + 0.001))",
                category="reversal",
                description="放量反转（价格变化 × 相对成交量）",
                params={"delay": [1, 3, 5]},
            ),
            AlphaTemplate(
                name="low_vol_reversion",
                expression="-rank(ts_returns(close, {delay})) * (1 - rank(ts_std(ts_returns(close, 1), {window})))",
                category="reversal",
                description="低波权重反转",
                params={"delay": [1, 3], "window": [20, 60]},
            ),
            # 距离高低点（反转/动量）
            AlphaTemplate(
                name="dist_from_high",
                expression="-rank(ts_max(close, {window}) - close)",
                category="reversal",
                description="距近期高点的距离",
                params={"window": [5, 10, 20]},
            ),
            AlphaTemplate(
                name="dist_from_low",
                expression="rank(close - ts_min(close, {window}))",
                category="momentum",
                description="距近期低点的距离",
                params={"window": [5, 10, 20]},
            ),
            AlphaTemplate(
                name="value_quality",
                expression="rank(roe) * rank(-pb)",
                category="fundamental",
                description="质量 × 价值（高 ROE & 低 PB）",
                params={},
            ),
        ]

    def _init_power_pool_templates(self) -> List[AlphaTemplate]:
        """初始化 Power Pool Alpha 模板（高潜力，研报风格组合）"""
        return [
            AlphaTemplate(
                name="advanced_momentum",
                expression="-ts_rank(ts_corr(rank(close), rank(volume), {window1}), {window2})",
                category="momentum",
                description="价格与成交量排名相关性（101 风格）",
                params={"window1": [10, 20], "window2": [5, 10]},
            ),
            AlphaTemplate(
                name="dual_ma_reversion",
                expression="rank(ts_mean(close, {window1}) - ts_mean(close, {window2})) * rank(volume / (adv20 + 0.001))",
                category="mean_reversion",
                description="双均线偏离 × 相对成交量",
                params={"window1": [5, 10], "window2": [20, 60]},
            ),
            AlphaTemplate(
                name="volatility_skew",
                expression="rank(ts_skewness(ts_returns(close, 1), {window}))",
                category="volatility",
                description="收益偏度截面（研报：收益与波动相关）",
                params={"window": [20, 60]},
            ),
            AlphaTemplate(
                name="correlation_decay",
                expression="-ts_corr(rank(ts_delta(close, {delay1})), rank(volume), {window})",
                category="volume",
                description="价格变化与成交量相关性",
                params={"delay1": [1, 5], "window": [10, 20]},
            ),
            AlphaTemplate(
                name="adaptive_momentum",
                expression="rank(ts_delta(close, {delay}) / (ts_std(close, {std_window}) + 0.001)) * rank(volume)",
                category="momentum",
                description="标准化动量 × 成交量排名",
                params={"delay": [5, 10], "std_window": [20, 60]},
            ),
            AlphaTemplate(
                name="momentum_continuation",
                expression="rank(ts_corr(ts_returns(close, {delay1}), ts_returns(close, {delay2}), {window}))",
                category="momentum",
                description="动量持续性截面",
                params={"delay1": [1, 5], "delay2": [10, 20], "window": [20]},
            ),
            AlphaTemplate(
                name="volume_breakout",
                expression="rank(volume / (ts_mean(volume, {window}) + 0.001)) * rank((close - ts_delay(close, {delay})) / (ts_delay(close, {delay}) + 0.001))",
                category="volume",
                description="相对成交量 × 收益率（防除零）",
                params={"window": [20, 60], "delay": [1, 5]},
            ),
            # 101 风格组合：vwap 区间、高低相关
            AlphaTemplate(
                name="vwap_range",
                expression="rank(ts_max(vwap - close, {window})) / (rank(ts_min(vwap - close, {window}) + 0.001) + 0.001)",
                category="mean_reversion",
                description="VWAP 偏离区间（101 #8 风格）",
                params={"window": [3, 5, 10]},
            ),
            AlphaTemplate(
                name="high_vol_corr_delta",
                expression="-rank(ts_delta(ts_corr(high, volume, {window1}), {delay})) * rank(ts_corr(rank(close), rank(volume), {window2}))",
                category="combined",
                description="高低量相关变化 × 价量相关（101 #6 风格）",
                params={"window1": [5, 10], "delay": [5], "window2": [60]},
            ),
        ]

    def _init_atom_templates(self) -> List[AlphaTemplate]:
        """初始化 ATOM 模板（简单、基础，101 与市场规律）"""
        return [
            # 短期反转（研报：0.6-6.4 天持仓）
            AlphaTemplate(
                name="simple_reversal",
                expression="-ts_returns(close, {delay})",
                category="reversal",
                description="简单短期反转",
                params={"delay": [1, 3, 5, 10]},
            ),
            AlphaTemplate(
                name="volume_rank",
                expression="rank(volume / (adv20 + 0.001))",
                category="volume",
                description="相对成交量排名（adv 归一化）",
                params={},
            ),
            AlphaTemplate(
                name="price_position",
                expression="rank((close - ts_min(low, {window})) / (ts_max(high, {window}) - ts_min(low, {window}) + 0.001))",
                category="momentum",
                description="价格在区间中的位置（防除零）",
                params={"window": [10, 20]},
            ),
            AlphaTemplate(
                name="simple_momentum",
                expression="rank((close - ts_delay(close, {delay})) / (ts_delay(close, {delay}) + 0.001))",
                category="momentum",
                description="简单动量截面",
                params={"delay": [5, 10, 20]},
            ),
            AlphaTemplate(
                name="vwap_ratio",
                expression="rank(close / (vwap + 0.001))",
                category="mean_reversion",
                description="价格与 VWAP 比率截面",
                params={},
            ),
            AlphaTemplate(
                name="range_normalized",
                expression="-rank((high - low) / (ts_mean(close, {window}) + 0.001))",
                category="volatility",
                description="标准化价格区间（低波因子）",
                params={"window": [20, 60]},
            ),
            # 101 风格原子
            AlphaTemplate(
                name="abs_delta_rank",
                expression="-ts_rank(abs(ts_delta(close, {delay})), {window})",
                category="volatility",
                description="价格变化绝对值时间排名（101 #3 风格）",
                params={"delay": [1, 7], "window": [60]},
            ),
            AlphaTemplate(
                name="low_vol_corr",
                expression="-rank(ts_corr(rank(low), rank(volume), {window}))",
                category="volume",
                description="低价与成交量相关（101 #4 风格）",
                params={"window": [10, 20]},
            ),
        ]

    def _init_superalpha_templates(self) -> List[AlphaTemplate]:
        """初始化 SuperAlpha 模板（多因子组合，研报风格）"""
        return [
            AlphaTemplate(
                name="momentum_volume_combo",
                expression="0.5 * rank(-ts_returns(close, {delay1})) + 0.5 * rank(volume / (ts_mean(volume, {window}) + 0.001))",
                category="combined",
                description="短期反转 + 相对成交量（防除零）",
                params={"delay1": [5, 10], "window": [20, 60]},
            ),
            AlphaTemplate(
                name="triple_screen",
                expression="(rank(ts_delta(close, {delay1})) + rank(ts_delta(volume, {delay2})) + rank(ts_std(close, {window}))) / 3",
                category="combined",
                description="价格变化 + 成交量变化 + 波动率",
                params={"delay1": [5, 10], "delay2": [1, 5], "window": [20, 60]},
            ),
            AlphaTemplate(
                name="mean_reversion_vol",
                expression="rank((close - ts_mean(close, {window1})) / (ts_std(close, {window1}) + 0.001)) * (1 - rank(ts_std(close, {window2})))",
                category="combined",
                description="均值回归 × 低波权重（研报：收益与波动相关）",
                params={"window1": [20, 60], "window2": [20, 60]},
            ),
            AlphaTemplate(
                name="momentum_quality",
                expression="rank(ts_corr(rank(ts_returns(close, {delay})), rank(volume), {window})) * rank(-abs(ts_returns(close, {delay})))",
                category="combined",
                description="动量与成交量相关 × 动量幅度",
                params={"delay": [5, 10], "window": [20, 60]},
            ),
            AlphaTemplate(
                name="reversal_vol_combo",
                expression="rank(-ts_returns(close, {delay})) * (1 - rank(ts_std(ts_returns(close, 1), {window})))",
                category="combined",
                description="短期反转 × 低波（分散化）",
                params={"delay": [3, 5], "window": [20]},
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

    def _sample_with_diversity(
        self, items: List[Dict], count: int, category_key: str = "category"
    ) -> List[Dict]:
        """按类别分层采样，提高 Alpha 多样性（研报：低相关性 15.9%）"""
        if count >= len(items):
            return items
        by_cat: Dict[str, List[Dict]] = {}
        for x in items:
            c = x.get(category_key, "other")
            by_cat.setdefault(c, []).append(x)
        n_cat = len(by_cat)
        per_cat = max(1, count // n_cat)
        selected = []
        for lst in by_cat.values():
            selected.extend(random.sample(lst, min(per_cat, len(lst))))
        if len(selected) < count:
            pool = [x for x in items if x not in selected]
            selected.extend(random.sample(pool, min(count - len(selected), len(pool))))
        random.shuffle(selected)
        return selected[:count]

    def generate_regular_alphas(
        self, count: int = 10, diversify: bool = True
    ) -> List[Dict]:
        """
        生成 Regular Alphas

        Args:
            count: 要生成的数量
            diversify: 是否按类别分层采样以降低相关性

        Returns:
            List[Dict]: Alpha 配置列表
        """
        all_expressions = []
        for template in self.regular_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append(
                    {
                        "expression": expr,
                        "name": template.name,
                        "category": template.category,
                        "type": "regular",
                    }
                )
        n = min(count, len(all_expressions))
        if diversify and n < len(all_expressions):
            selected = self._sample_with_diversity(all_expressions, n)
        else:
            selected = random.sample(all_expressions, n)
        return selected

    def generate_power_pool_alphas(
        self, count: int = 10, diversify: bool = True
    ) -> List[Dict]:
        """
        生成 Power Pool Alphas（高潜力 Alpha）

        Args:
            count: 要生成的数量
            diversify: 是否按类别分层采样以降低相关性

        Returns:
            List[Dict]: Alpha 配置列表
        """
        all_expressions = []
        for template in self.power_pool_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append(
                    {
                        "expression": expr,
                        "name": template.name,
                        "category": template.category,
                        "type": "power_pool",
                    }
                )
        n = min(count, len(all_expressions))
        if diversify and n < len(all_expressions):
            selected = self._sample_with_diversity(all_expressions, n)
        else:
            selected = random.sample(all_expressions, n)
        return selected

    def generate_atoms(self, count: int = 10, diversify: bool = True) -> List[Dict]:
        """
        生成 ATOMs（简单、基础的 Alpha）

        Args:
            count: 要生成的数量
            diversify: 是否按类别分层采样以降低相关性

        Returns:
            List[Dict]: Alpha 配置列表
        """
        all_expressions = []
        for template in self.atom_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append(
                    {
                        "expression": expr,
                        "name": template.name,
                        "category": template.category,
                        "type": "atom",
                    }
                )
        n = min(count, len(all_expressions))
        if diversify and n < len(all_expressions):
            selected = self._sample_with_diversity(all_expressions, n)
        else:
            selected = random.sample(all_expressions, n)
        return selected

    def generate_superalphas(
        self, count: int = 5, diversify: bool = True
    ) -> List[Dict]:
        """
        生成 SuperAlphas（组合 Alpha）

        Args:
            count: 要生成的数量
            diversify: 是否按类别分层采样以降低相关性

        Returns:
            List[Dict]: Alpha 配置列表
        """
        all_expressions = []
        for template in self.superalpha_templates:
            exprs = self._fill_template(template)
            for expr in exprs:
                all_expressions.append(
                    {
                        "expression": expr,
                        "name": template.name,
                        "category": template.category,
                        "type": "superalpha",
                    }
                )
        n = min(count, len(all_expressions))
        if diversify and n < len(all_expressions):
            selected = self._sample_with_diversity(all_expressions, n)
        else:
            selected = random.sample(all_expressions, n)
        return selected

    def generate_all_types(
        self,
        regular_count: int = 5,
        power_pool_count: int = 5,
        atom_count: int = 5,
        superalpha_count: int = 3,
    ) -> Dict[str, List[Dict]]:
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
            "superalphas": self.generate_superalphas(superalpha_count),
        }

    def generate_custom_alpha(
        self, base_expression: str, variations: List[Tuple[str, List]]
    ) -> List[str]:
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
                "params": {"window1": [8, 16], "window2": [8, 16], "window3": [6, 12]},
            },
            {
                "expression": "-ts_corr(open, volume, {window})",
                "name": "alpha_101_var_2",
                "category": "volume",
                "params": {"window": [10, 20, 60]},
            },
            {
                "expression": "-ts_rank(abs(ts_delta(close, {delay})), {window})",
                "name": "alpha_101_var_3",
                "category": "volatility",
                "params": {"delay": [7], "window": [60, 120]},
            },
            {
                "expression": "-rank(ts_corr(rank(low), rank(volume), {window}))",
                "name": "alpha_101_var_4",
                "category": "volume",
                "params": {"window": [10, 20]},
            },
            {
                "expression": "rank((open - ts_mean(vwap, {window1}))) * -abs(rank((close - vwap)))",
                "name": "alpha_101_var_5",
                "category": "mean_reversion",
                "params": {"window1": [10, 20]},
            },
            {
                "expression": "-rank(ts_delta(ts_corr(high, volume, {window1}), {delay}) * rank(ts_corr(rank(close), rank(volume), {window2})))",
                "name": "alpha_101_var_6",
                "category": "combined",
                "params": {"window1": [5, 10], "delay": [5], "window2": [60, 120]},
            },
            {
                "expression": "rank(close - open) / (rank(high - low) + 0.001)",
                "name": "alpha_101_var_7",
                "category": "momentum",
                "params": {},
            },
            {
                "expression": "rank(ts_max((vwap - close), {window})) / (rank(ts_min((vwap - close), {window})) + 0.001)",
                "name": "alpha_101_var_8",
                "category": "mean_reversion",
                "params": {"window": [3, 5, 10]},
            },
            # 更多 101 风格变体（研报：低相关性、持仓 0.6-6.4 天）
            {
                "expression": "rank(ts_sum(ts_returns(close, 1), {window}))",
                "name": "alpha_101_var_9",
                "category": "momentum",
                "params": {"window": [5, 10, 20]},
            },
            {
                "expression": "-rank(ts_corr(high, volume, {window}))",
                "name": "alpha_101_var_10",
                "category": "volume",
                "params": {"window": [5, 10, 20]},
            },
            {
                "expression": "rank(ts_delta(volume, {delay})) * rank((close - open) / (high - low + 0.001))",
                "name": "alpha_101_var_11",
                "category": "combined",
                "params": {"delay": [1, 5]},
            },
            {
                "expression": "rank(ts_mean(close, {w1}) / (ts_mean(close, {w2}) + 0.001))",
                "name": "alpha_101_var_12",
                "category": "momentum",
                "params": {"w1": [5, 10], "w2": [20, 60]},
            },
        ]

        results = []
        for alpha in alpha_101_variations:
            template = AlphaTemplate(
                name=alpha["name"],
                expression=alpha["expression"],
                category=alpha["category"],
                description="101 Alpha 变体",
                params=alpha.get("params", {}),
            )
            exprs = self._fill_template(template)
            for expr in exprs:
                results.append(
                    {
                        "expression": expr,
                        "name": alpha["name"],
                        "category": alpha["category"],
                        "type": "regular",
                    }
                )

        return results
