"""
WorldQuant Brain 自动化 Alpha 提交系统

支持类型：
- Power Pool Alphas
- ATOMs
- Regular Alphas
- SuperAlphas
"""

__version__ = "0.1.0"
__author__ = "Consultant"

from .client import WorldQuantBrainClient
from .alpha_generator import AlphaGenerator
from .alpha_submitter import AlphaSubmitter
from .strategy import StrategySpec

__all__ = ["WorldQuantBrainClient", "AlphaGenerator", "AlphaSubmitter", "StrategySpec"]
