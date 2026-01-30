"""
WorldQuant Brain API 客户端
处理认证、请求和会话管理
"""

import json
import time
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Region(Enum):
    """支持的交易区域"""
    USA = "USA"
    CHN = "CHN"  # 中国市场
    EUR = "EUR"
    JPN = "JPN"
    TWN = "TWN"
    KOR = "KOR"
    GBR = "GBR"
    DEU = "DEU"


class Unviverse(Enum):
    """股票池 Universe"""
    TOP3000 = "TOP3000"
    TOP2000 = "TOP2000"
    TOP1000 = "TOP1000"
    TOP500 = "TOP500"
    TOP200 = "TOP200"
    TOP100 = "TOP100"


class Delay(Enum):
    """延迟设置"""
    DELAY_0 = 0  # 实时
    DELAY_1 = 1  # T+1


@dataclass
class AlphaConfig:
    """Alpha 配置参数"""
    expression: str
    region: Region = Region.USA
    universe: Unviverse = Unviverse.TOP3000
    delay: Delay = Delay.DELAY_1
    decay: int = 0
    neutralization: str = "SUBINDUSTRY"  # MARKET, INDUSTRY, SUBINDUSTRY, SECTOR, NONE
    truncation: float = 0.08
    pasteurization: str = "ON"  # ON, OFF
    unit_neutral: bool = False
    visualization: bool = False


@dataclass
class SimulateResult:
    """模拟结果"""
    alpha_id: str
    status: str
    sharpe: float
    fitness: float
    turnover: float
    returns: float
    drawdown: float
    margin: float
    is_submittable: bool
    error_message: Optional[str] = None


class WorldQuantBrainClient:
    """WorldQuant Brain API 客户端"""

    BASE_URL = "https://api.worldquantbrain.com"

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.auth_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0

    def authenticate(self) -> bool:
        """
        用户认证并获取 JWT token

        Returns:
            bool: 认证是否成功
        """
        auth_url = f"{self.BASE_URL}/auth/users/authenticate"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        payload = {
            "username": self.username,
            "password": self.password
        }

        try:
            response = self.session.post(
                auth_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()
            self.auth_token = data.get("token")
            self.refresh_token = data.get("refreshToken")
            # Token 通常有效期为 24 小时
            self.token_expiry = time.time() + 82800  # 23 小时后刷新

            logger.info(f"认证成功: {self.username}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"认证失败: {e}")
            return False

    def _ensure_authenticated(self):
        """确保已认证，如果 token 即将过期则刷新"""
        if not self.auth_token or time.time() >= self.token_expiry:
            if not self.authenticate():
                raise Exception("无法完成认证")

    def _get_headers(self) -> Dict[str, str]:
        """获取带认证的请求头"""
        self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def simulate_alpha(self, config: AlphaConfig) -> SimulateResult:
        """
        模拟单个 Alpha

        Args:
            config: Alpha 配置

        Returns:
            SimulateResult: 模拟结果
        """
        url = f"{self.BASE_URL}/alphas"

        payload = {
            "expression": config.expression,
            "region": config.region.value,
            "universe": config.universe.value,
            "delay": config.delay.value,
            "decay": config.decay,
            "neutralization": config.neutralization,
            "truncation": config.truncation,
            "pasteurization": config.pasteurization,
            "unitNeutral": config.unit_neutral,
            "visualization": config.visualization
        }

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._get_headers(),
                timeout=60
            )

            if response.status_code == 201:
                data = response.json()
                alpha_id = data.get("id")
                logger.info(f"Alpha 创建成功，ID: {alpha_id}")

                # 等待模拟完成并获取结果
                return self._wait_for_simulation(alpha_id)
            else:
                error_msg = response.json().get("message", "未知错误")
                logger.error(f"模拟失败: {error_msg}")
                return SimulateResult(
                    alpha_id="",
                    status="FAILED",
                    sharpe=0,
                    fitness=0,
                    turnover=0,
                    returns=0,
                    drawdown=0,
                    margin=0,
                    is_submittable=False,
                    error_message=error_msg
                )

        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常: {e}")
            return SimulateResult(
                alpha_id="",
                status="ERROR",
                sharpe=0,
                fitness=0,
                turnover=0,
                returns=0,
                drawdown=0,
                margin=0,
                is_submittable=False,
                error_message=str(e)
            )

    def _wait_for_simulation(self, alpha_id: str, max_wait: int = 300) -> SimulateResult:
        """
        等待模拟完成

        Args:
            alpha_id: Alpha ID
            max_wait: 最大等待时间（秒）

        Returns:
            SimulateResult: 模拟结果
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}"
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "")

                    if status in ["COMPLETE", "PASS", "FAIL"]:
                        metrics = data.get("metrics", {})
                        return SimulateResult(
                            alpha_id=alpha_id,
                            status=status,
                            sharpe=metrics.get("sharpe", 0),
                            fitness=metrics.get("fitness", 0),
                            turnover=metrics.get("turnover", 0),
                            returns=metrics.get("returns", 0),
                            drawdown=metrics.get("drawdown", 0),
                            margin=metrics.get("margin", 0),
                            is_submittable=data.get("is.submittable", False)
                        )

                    logger.info(f"Alpha {alpha_id} 模拟中... 状态: {status}")

                time.sleep(5)

            except Exception as e:
                logger.warning(f"检查模拟状态出错: {e}")
                time.sleep(5)

        return SimulateResult(
            alpha_id=alpha_id,
            status="TIMEOUT",
            sharpe=0,
            fitness=0,
            turnover=0,
            returns=0,
            drawdown=0,
            margin=0,
            is_submittable=False,
            error_message="模拟超时"
        )

    def submit_alpha(self, alpha_id: str) -> bool:
        """
        提交 Alpha

        Args:
            alpha_id: Alpha ID

        Returns:
            bool: 是否提交成功
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}/submit"

        try:
            response = self.session.post(
                url,
                headers=self._get_headers(),
                timeout=30
            )

            if response.status_code in [200, 201]:
                logger.info(f"Alpha {alpha_id} 提交成功")
                return True
            else:
                error_msg = response.json().get("message", "未知错误")
                logger.error(f"提交失败: {error_msg}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"提交请求异常: {e}")
            return False

    def get_submittable_alphas(self) -> List[Dict[str, Any]]:
        """
        获取可提交的 Alpha 列表

        Returns:
            List[Dict]: 可提交的 Alpha 列表
        """
        url = f"{self.BASE_URL}/alphas"
        params = {
            "status": "COMPLETE",
            "is.submittable": "true",
            "limit": 100
        }

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self._get_headers(),
                timeout=30
            )

            if response.status_code == 200:
                return response.json().get("alphas", [])
            return []

        except requests.exceptions.RequestException as e:
            logger.error(f"获取可提交 Alpha 列表失败: {e}")
            return []

    def check_alpha_correlation(self, alpha_id: str) -> Dict[str, Any]:
        """
        检查 Alpha 与其他已提交 Alpha 的相关性

        Args:
            alpha_id: Alpha ID

        Returns:
            Dict: 相关性分析结果
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}/correlations"

        try:
            response = self.session.get(
                url,
                headers=self._get_headers(),
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            return {}

        except requests.exceptions.RequestException as e:
            logger.error(f"检查相关性失败: {e}")
            return {}

    def get_data_fields(self, dataset: str = None) -> List[Dict[str, Any]]:
        """
        获取可用的数据字段

        Args:
            dataset: 数据集名称，可选

        Returns:
            List[Dict]: 数据字段列表
        """
        url = f"{self.BASE_URL}/data-fields"
        params = {"dataset.id": dataset} if dataset else {}

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self._get_headers(),
                timeout=30
            )

            if response.status_code == 200:
                return response.json().get("fields", [])
            return []

        except requests.exceptions.RequestException as e:
            logger.error(f"获取数据字段失败: {e}")
            return []
