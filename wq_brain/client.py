"""
WorldQuant Brain API 客户端
处理认证、请求和会话管理
"""

import json
import time
import requests
from requests.auth import HTTPBasicAuth
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
    DEFAULT_SIMULATION_MAX_WAIT = 120
    DEFAULT_SIMULATION_RETRY_WAIT = 60

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.auth_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.use_bearer_auth: bool = False
        self.token_expiry: float = 0

    def _has_session_cookie(self) -> bool:
        """检查当前会话是否持有认证 cookie"""
        return len(self.session.cookies) > 0

    def authenticate(self) -> bool:
        """
        用户认证并获取 JWT token

        Returns:
            bool: 认证是否成功
        """
        auth_url = f"{self.BASE_URL}/authentication"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            response = self.session.post(
                auth_url,
                auth=HTTPBasicAuth(self.username, self.password),
                headers=headers,
                timeout=30
            )
            response.raise_for_status()

            data = {}
            try:
                if response.text.strip():
                    data = response.json()
            except ValueError:
                data = {}

            token = None
            refresh_token = None
            if isinstance(data, dict):
                token = (
                    data.get("token")
                    or data.get("accessToken")
                    or data.get("access_token")
                    or data.get("jwt")
                )
                refresh_token = data.get("refreshToken") or data.get("refresh_token")

            if self._has_session_cookie():
                # 优先使用 Session Cookie：兼容性更好，Bearer 仅作为兜底
                self.auth_token = token
                self.refresh_token = refresh_token
                self.use_bearer_auth = False
                # cookie 过期时间不一定可用，使用保守刷新时间
                self.token_expiry = time.time() + 82800
                logger.info(f"认证成功(Session): {self.username}")
                return True

            if token:
                self.auth_token = token
                self.refresh_token = refresh_token
                self.use_bearer_auth = True
                # Token 通常有效期为 24 小时
                self.token_expiry = time.time() + 82800  # 23 小时后刷新
                logger.info(f"认证成功(Bearer): {self.username}")
                return True

            logger.error("认证失败: 响应中未获取到 token，且无会话 cookie")
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"认证失败: {e}")
            return False

    def _ensure_authenticated(self):
        """确保已认证，如果 token 即将过期则刷新"""
        needs_auth = time.time() >= self.token_expiry
        if self.use_bearer_auth:
            needs_auth = needs_auth or not self.auth_token
        else:
            needs_auth = needs_auth or not self._has_session_cookie()

        if needs_auth:
            if not self.authenticate():
                raise Exception("无法完成认证")

    def _get_headers(self) -> Dict[str, str]:
        """获取带认证的请求头"""
        self._ensure_authenticated()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        if self.use_bearer_auth and self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _should_retry_auth(self, response: requests.Response) -> bool:
        if response.status_code in (401, 403):
            return True
        try:
            data = response.json()
        except Exception:
            return False
        detail = str(data.get("detail", "")).lower()
        message = str(data.get("message", "")).lower()
        return "incorrect authentication credentials" in detail or "incorrect authentication credentials" in message

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """统一请求入口，自动处理认证失效重试"""
        headers = kwargs.pop("headers", None)
        for _ in range(2):
            if headers:
                req_headers = headers.copy()
                if "Authorization" not in req_headers:
                    req_headers.update(self._get_headers())
            else:
                req_headers = self._get_headers()

            response = self.session.request(
                method,
                url,
                headers=req_headers,
                **kwargs
            )

            if not self._should_retry_auth(response):
                return response

            logger.warning("认证可能失效，尝试重新认证并重试...")
            self.auth_token = None
            self.refresh_token = None
            self.use_bearer_auth = False
            self.token_expiry = 0
            self.session.cookies.clear()
            if not self.authenticate():
                return response
            # 二次尝试时移除外部传入的旧 Authorization，避免复用失效凭证
            if headers and "Authorization" in headers:
                headers = {k: v for k, v in headers.items() if k != "Authorization"}

        return response

    def simulate_alpha(self, config: AlphaConfig) -> SimulateResult:
        """
        模拟单个 Alpha

        Args:
            config: Alpha 配置

        Returns:
            SimulateResult: 模拟结果
        """
        url = f"{self.BASE_URL}/simulations"

        payload = {
            "type": "REGULAR",
            "settings": {
                "instrumentType": "EQUITY",
                "region": config.region.value,
                "universe": config.universe.value,
                "delay": config.delay.value,
                "decay": config.decay,
                "neutralization": config.neutralization,
                "truncation": config.truncation,
                "pasteurization": config.pasteurization,
                "testPeriod": "P0Y",
                "unitHandling": "VERIFY",
                "nanHandling": "ON",
                "language": "FASTEXPR",
                "visualization": config.visualization
            },
            "regular": config.expression
        }

        try:
            response = self._request(
                "post",
                url,
                json=payload,
                timeout=60
            )

            if response.status_code == 201:
                # 从 Location header 获取模拟进度 URL
                progress_url = response.headers.get("Location")
                if progress_url:
                    logger.info(f"Alpha 模拟已创建，轮询进度...")
                    result = self._wait_for_simulation_progress(
                        progress_url, max_wait=self.DEFAULT_SIMULATION_MAX_WAIT
                    )
                    if result.status == "TIMEOUT":
                        logger.warning("模拟超时，准备重试轮询...")
                        time.sleep(5)
                        result = self._wait_for_simulation_progress(
                            progress_url, max_wait=self.DEFAULT_SIMULATION_RETRY_WAIT
                        )
                    return result
                else:
                    error_msg = "未获取到模拟进度 URL"
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
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", str(error_data))
                except:
                    error_msg = response.text or f"HTTP {response.status_code}"
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

    def _wait_for_simulation_progress(self, progress_url: str, max_wait: int = 900) -> SimulateResult:
        """
        等待模拟完成（通过进度 URL）

        Args:
            progress_url: 模拟进度 URL（从 Location header 获取）
            max_wait: 最大等待时间（秒）

        Returns:
            SimulateResult: 模拟结果
        """
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                response = self._request(
                    "get",
                    progress_url,
                    timeout=30
                )

                if response.status_code == 200:
                    data = response.json()
                    
                    # 检查是否有 retry-after header，如果有说明还在进行中
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        wait_time = float(retry_after)
                        logger.info(f"模拟进行中... 等待 {wait_time:.1f} 秒")
                        time.sleep(wait_time)
                        continue
                    
                    # 模拟完成，获取 alpha ID 和结果
                    alpha_id = data.get("alpha", "")
                    if alpha_id:
                        return self._get_alpha_result(alpha_id)

                    status = str(data.get("status", "")).upper()
                    if status and status not in ["COMPLETE", "PASS", "FAIL"]:
                        time.sleep(5)
                        continue

                    progress = data.get("progress")
                    if isinstance(progress, (int, float)) and progress < 1:
                        time.sleep(5)
                        continue

                    ready = data.get("ready")
                    if ready is False:
                        time.sleep(5)
                        continue

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
                        error_message="未获取到 Alpha ID"
                    )

                time.sleep(5)

            except Exception as e:
                logger.warning(f"检查模拟状态出错: {e}")
                time.sleep(5)

        return SimulateResult(
            alpha_id="",
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

    def _get_alpha_result(self, alpha_id: str) -> SimulateResult:
        """
        获取 Alpha 的模拟结果

        Args:
            alpha_id: Alpha ID

        Returns:
            SimulateResult: 模拟结果
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}"

        try:
            response = self._request(
                "get",
                url,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Alpha 结果数据: {data}")
                status = data.get("status", "")
                # metrics 在 'is' 字段中
                is_data = data.get("is", {})
                metrics = is_data if is_data else {}
                
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
            else:
                error_msg = f"获取 Alpha 结果失败: HTTP {response.status_code}"
                logger.error(error_msg)
                return SimulateResult(
                    alpha_id=alpha_id,
                    status="ERROR",
                    sharpe=0,
                    fitness=0,
                    turnover=0,
                    returns=0,
                    drawdown=0,
                    margin=0,
                    is_submittable=False,
                    error_message=error_msg
                )

        except Exception as e:
            logger.error(f"获取 Alpha 结果异常: {e}")
            return SimulateResult(
                alpha_id=alpha_id,
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
                response = self._request(
                    "get",
                    url,
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
            response = self._request(
                "post",
                url,
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
            response = self._request(
                "get",
                url,
                params=params,
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
            response = self._request(
                "get",
                url,
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
            response = self._request(
                "get",
                url,
                params=params,
                timeout=30
            )

            if response.status_code == 200:
                return response.json().get("fields", [])
            return []

        except requests.exceptions.RequestException as e:
            logger.error(f"获取数据字段失败: {e}")
            return []
