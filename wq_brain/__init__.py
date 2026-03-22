"""
WorldQuant multi-agent runtime core package.
"""

__version__ = "0.1.0"
__author__ = "Consultant"

from .client import WorldQuantBrainClient
from .alpha_generator import AlphaGenerator
from .alpha_submitter import AlphaSubmitter

__all__ = ["WorldQuantBrainClient", "AlphaGenerator", "AlphaSubmitter"]
