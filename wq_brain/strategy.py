"""
策略定义与执行辅助

用于将策略配置与执行逻辑解耦，便于复用与复盘。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

from .alpha_generator import AlphaGenerator
from .alpha_submitter import AlphaSubmitter, AlphaSettings
from .client import Region, Unviverse, Delay


_TYPE_ALIASES = {
    "atom": "atom",
    "atoms": "atom",
    "regular": "regular",
    "power_pool": "power_pool",
    "superalpha": "superalpha",
    "superalphas": "superalpha",
}


@dataclass
class StrategySpec:
    """策略配置"""
    name: str
    date: str
    region: Region
    universe: Unviverse
    delay: Delay
    types: Dict[str, int]
    diversify: bool = True
    seed: Optional[int] = None
    auto_submit: bool = True
    check_correlation: bool = True
    max_correlation: float = 0.7
    decay: int = 0
    neutralization: str = "SUBINDUSTRY"
    truncation: float = 0.08
    pasteurization: str = "ON"
    unit_neutral: bool = False
    visualization: bool = False

    @staticmethod
    def from_dict(data: Dict) -> "StrategySpec":
        types = data.get("types", {})
        if not types:
            raise ValueError("strategy.types 不能为空")

        raw_date = data.get("date", "")
        if hasattr(raw_date, "isoformat"):
            date_str = raw_date.isoformat()
        else:
            date_str = str(raw_date) if raw_date is not None else ""

        region = Region(data.get("region", "USA"))
        universe = Unviverse(data.get("universe", "TOP3000"))
        delay = Delay(data.get("delay", 1))

        return StrategySpec(
            name=data.get("name", "strategy"),
            date=date_str,
            region=region,
            universe=universe,
            delay=delay,
            types=types,
            diversify=bool(data.get("diversify", True)),
            seed=data.get("seed"),
            auto_submit=bool(data.get("auto_submit", True)),
            check_correlation=bool(data.get("check_correlation", True)),
            max_correlation=float(data.get("max_correlation", 0.7)),
            decay=int(data.get("decay", 0)),
            neutralization=data.get("neutralization", "SUBINDUSTRY"),
            truncation=float(data.get("truncation", 0.08)),
            pasteurization=data.get("pasteurization", "ON"),
            unit_neutral=bool(data.get("unit_neutral", False)),
            visualization=bool(data.get("visualization", False)),
        )


def load_strategy(path: str) -> StrategySpec:
    """从 YAML 加载策略配置"""
    if not yaml:
        raise RuntimeError("缺少 pyyaml 依赖，无法读取策略配置")
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return StrategySpec.from_dict(data)


def build_alphas_by_type(
    spec: StrategySpec, generator: AlphaGenerator
) -> Dict[str, List[Dict]]:
    """根据策略配置生成 Alpha 列表"""
    if spec.seed is not None:
        random.seed(spec.seed)

    type_map = {
        "atom": generator.generate_atoms,
        "regular": generator.generate_regular_alphas,
        "power_pool": generator.generate_power_pool_alphas,
        "superalpha": generator.generate_superalphas,
    }

    alphas_by_type: Dict[str, List[Dict]] = {}
    for raw_type, count in spec.types.items():
        alpha_type = _TYPE_ALIASES.get(raw_type)
        if not alpha_type:
            raise ValueError(f"不支持的 Alpha 类型: {raw_type}")
        generator_fn = type_map[alpha_type]
        alphas_by_type[alpha_type] = generator_fn(int(count), diversify=spec.diversify)

    return alphas_by_type


def save_templates(path: str, spec: StrategySpec, alphas_by_type: Dict[str, List[Dict]]):
    """保存生成的 Alpha 模板到 JSON"""
    payload = {
        "name": spec.name,
        "date": spec.date,
        "region": spec.region.value,
        "universe": spec.universe.value,
        "delay": spec.delay.value,
        "types": spec.types,
        "alphas": alphas_by_type,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def run_strategy(
    spec: StrategySpec,
    alphas_by_type: Dict[str, List[Dict]],
    submitter: AlphaSubmitter,
) -> List:
    """执行策略：模拟并按标准自动提交"""
    settings = AlphaSettings(
        delay=spec.delay,
        decay=spec.decay,
        neutralization=spec.neutralization,
        truncation=spec.truncation,
        pasteurization=spec.pasteurization,
        unit_neutral=spec.unit_neutral,
        visualization=spec.visualization,
    )

    all_records = []
    for alpha_type, alphas in alphas_by_type.items():
        submitter.criteria = submitter._get_criteria_for_type(alpha_type)
        records = submitter.simulate_and_submit(
            alphas=alphas,
            region=spec.region,
            universe=spec.universe,
            auto_submit=spec.auto_submit,
            check_correlation=spec.check_correlation,
            max_correlation=spec.max_correlation,
            settings=settings,
        )
        all_records.extend(records)
    return all_records
