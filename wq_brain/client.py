"""
WorldQuant Brain API 客户端
处理认证、请求和会话管理
"""

import json
import os
import time
import requests
from requests.auth import HTTPBasicAuth
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class Region(Enum):
    """支持的交易区域"""
    GLB = "GLB"
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
    TOPDIV3000 = "TOPDIV3000"
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


@dataclass
class SubmissionCheckResult:
    """提交前检查结果"""
    alpha_id: str
    ok: bool
    pass_count: int
    fail_count: int
    warning_count: int
    pending_count: int
    checks: List[Dict[str, Any]]
    failed_checks: List[Dict[str, Any]]
    warning_checks: List[Dict[str, Any]]
    pending_checks: List[Dict[str, Any]]
    error_message: Optional[str] = None


@dataclass
class SubmissionResult:
    """完整提交流程结果（check -> submit -> status confirm）"""
    alpha_id: str
    submitted: bool
    reason: str
    check_result: Optional[SubmissionCheckResult] = None
    submit_http_status: Optional[int] = None


class WorldQuantBrainClient:
    """WorldQuant Brain API 客户端"""

    BASE_URL = "https://api.worldquantbrain.com"
    DEFAULT_SIMULATION_MAX_WAIT = 120
    DEFAULT_SIMULATION_RETRY_WAIT = 60
    DEFAULT_CHECK_MAX_WAIT = 180

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = requests.Session()
        # 某些环境下系统代理会导致 API 连接不稳定，可按需关闭
        if os.getenv("WQB_DISABLE_PROXY", "").lower() in {"1", "true", "yes", "on"}:
            self.session.trust_env = False
        self.auth_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.use_bearer_auth: bool = False
        self.token_expiry: float = 0
        self.submission_log_path = os.getenv(
            "WQB_SUBMISSION_LOG", "./results/submission_checks.jsonl"
        )
        log_dir = os.path.dirname(self.submission_log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    @staticmethod
    def _extract_checks(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        is_data = payload.get("is", {})
        if isinstance(is_data, dict):
            checks = is_data.get("checks", [])
            if isinstance(checks, list):
                return checks
        return []

    @staticmethod
    def _split_checks(
        checks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        passed = [c for c in checks if c.get("result") == "PASS"]
        failed = [c for c in checks if c.get("result") == "FAIL"]
        warning = [c for c in checks if c.get("result") == "WARNING"]
        pending = [c for c in checks if c.get("result") == "PENDING"]
        return passed, failed, warning, pending

    @staticmethod
    def _infer_submission_category(
        expression: str = "", preferred: Optional[str] = None
    ) -> str:
        valid = {
            "PRICE_REVERSION",
            "PRICE_MOMENTUM",
            "VOLUME",
            "FUNDAMENTAL",
            "ANALYST",
            "PRICE_VOLUME",
            "RELATION",
            "SENTIMENT",
        }
        if preferred and preferred in valid:
            return preferred

        text = f"{preferred or ''} {expression or ''}".lower()
        if any(k in text for k in ["reversal", "reversion", "mean_reversion", "bollinger"]):
            return "PRICE_REVERSION"
        if "momentum" in text:
            return "PRICE_MOMENTUM"
        if any(k in text for k in ["volume", "adv", "turnover"]):
            if any(k in text for k in ["price", "close", "open", "high", "low", "vwap"]):
                return "PRICE_VOLUME"
            return "VOLUME"
        if any(k in text for k in ["fnd", "fundamental", "assets", "liabilities", "revenue", "roe", "pb"]):
            return "FUNDAMENTAL"
        if any(k in text for k in ["corr", "covariance", "relation"]):
            return "RELATION"
        if "analyst" in text:
            return "ANALYST"
        if "sentiment" in text:
            return "SENTIMENT"
        return "PRICE_VOLUME"

    def _append_submission_log(self, record: Dict[str, Any]):
        payload = {"ts": time.time(), **record}
        try:
            with open(self.submission_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入提交日志失败: {e}")

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
        if response.status_code == 401:
            return True
        if response.status_code != 403:
            return False
        try:
            data = response.json()
        except Exception:
            return False
        detail = str(data.get("detail", "")).lower()
        message = str(data.get("message", "")).lower()
        return (
            "incorrect authentication credentials" in detail
            or "incorrect authentication credentials" in message
            or "authentication credentials were not provided" in detail
            or "authentication credentials were not provided" in message
        )

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
                checks = metrics.get("checks", []) if isinstance(metrics, dict) else []
                is_submittable = bool(data.get("is.submittable", False))
                # 某些接口不返回 is.submittable，尝试由 checks 推断
                if not is_submittable and isinstance(checks, list) and checks:
                    has_fail = any(c.get("result") == "FAIL" for c in checks)
                    has_pending = any(c.get("result") == "PENDING" for c in checks)
                    is_submittable = not has_fail and not has_pending
                
                return SimulateResult(
                    alpha_id=alpha_id,
                    status=status,
                    sharpe=metrics.get("sharpe", 0),
                    fitness=metrics.get("fitness", 0),
                    turnover=metrics.get("turnover", 0),
                    returns=metrics.get("returns", 0),
                    drawdown=metrics.get("drawdown", 0),
                    margin=metrics.get("margin", 0),
                    is_submittable=is_submittable
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

    def set_alpha_properties(
        self, alpha_id: str, name: Optional[str], category: Optional[str]
    ) -> Tuple[bool, str]:
        url = f"{self.BASE_URL}/alphas/{alpha_id}"
        payload: Dict[str, Any] = {}
        if name:
            payload["name"] = name
        if category:
            payload["category"] = category

        if not payload:
            return True, "no-update"

        try:
            response = self._request("patch", url, json=payload, timeout=30)
            if response.status_code == 200:
                return True, "updated"
            msg = response.text[:300] if response.text else f"HTTP {response.status_code}"
            return False, msg
        except requests.exceptions.RequestException as e:
            return False, str(e)

    def run_submission_check(
        self, alpha_id: str, max_wait: int = DEFAULT_CHECK_MAX_WAIT
    ) -> SubmissionCheckResult:
        """
        显式触发并等待提交检查完成（对应网页 Check Submission 按钮）。
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}/check"
        start = time.time()

        while time.time() - start < max_wait:
            try:
                response = self._request("get", url, timeout=30)
                if response.status_code != 200:
                    return SubmissionCheckResult(
                        alpha_id=alpha_id,
                        ok=False,
                        pass_count=0,
                        fail_count=0,
                        warning_count=0,
                        pending_count=0,
                        checks=[],
                        failed_checks=[],
                        warning_checks=[],
                        pending_checks=[],
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    time.sleep(float(retry_after))
                    continue

                raw = response.text.strip()
                if not raw:
                    time.sleep(1)
                    continue

                payload = response.json()
                checks = self._extract_checks(payload)
                passed, failed, warning, pending = self._split_checks(checks)
                return SubmissionCheckResult(
                    alpha_id=alpha_id,
                    ok=len(failed) == 0 and len(pending) == 0,
                    pass_count=len(passed),
                    fail_count=len(failed),
                    warning_count=len(warning),
                    pending_count=len(pending),
                    checks=checks,
                    failed_checks=failed,
                    warning_checks=warning,
                    pending_checks=pending,
                )
            except Exception as e:
                logger.warning(f"提交检查轮询异常: {e}")
                time.sleep(2)

        return SubmissionCheckResult(
            alpha_id=alpha_id,
            ok=False,
            pass_count=0,
            fail_count=0,
            warning_count=0,
            pending_count=1,
            checks=[],
            failed_checks=[],
            warning_checks=[],
            pending_checks=[{"name": "CHECK_TIMEOUT", "result": "PENDING"}],
            error_message=f"check timeout>{max_wait}s",
        )

    def _wait_for_submitted_status(self, alpha_id: str, max_wait: int = 20) -> bool:
        """
        提交后确认状态，避免“接口返回成功但网页仍 UNSUBMITTED”。
        """
        url = f"{self.BASE_URL}/alphas/{alpha_id}"
        start = time.time()
        while time.time() - start < max_wait:
            try:
                response = self._request("get", url, timeout=30)
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "SUBMITTED" or payload.get("dateSubmitted"):
                        return True
                time.sleep(2)
            except Exception:
                time.sleep(2)
        return False

    def submit_alpha_with_checks(
        self,
        alpha_id: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
        check_max_wait: int = DEFAULT_CHECK_MAX_WAIT,
    ) -> SubmissionResult:
        """
        完整提交流程：设置属性 -> Check Submission -> Submit Alpha -> 状态确认
        """
        try:
            alpha_resp = self._request("get", f"{self.BASE_URL}/alphas/{alpha_id}", timeout=30)
            alpha_data = alpha_resp.json() if alpha_resp.status_code == 200 else {}
        except Exception:
            alpha_data = {}

        expression = ""
        regular_data = alpha_data.get("regular", {})
        if isinstance(regular_data, dict):
            expression = regular_data.get("code", "")
        elif isinstance(regular_data, str):
            expression = regular_data

        existing_name = alpha_data.get("name", "")
        final_name = name or existing_name
        if not final_name or str(final_name).strip().lower() in {"anonymous", "currently 'anonymous'"}:
            final_name = f"alpha_{alpha_id}"

        existing_category = alpha_data.get("category")
        final_category = self._infer_submission_category(
            expression=expression, preferred=category or existing_category
        )

        ok, update_reason = self.set_alpha_properties(alpha_id, final_name, final_category)
        if not ok:
            reason = f"set properties failed: {update_reason}"
            self._append_submission_log(
                {
                    "alpha_id": alpha_id,
                    "submitted": False,
                    "phase": "set_properties",
                    "reason": reason,
                    "name": final_name,
                    "category": final_category,
                }
            )
            return SubmissionResult(alpha_id=alpha_id, submitted=False, reason=reason)

        check_result = self.run_submission_check(alpha_id, max_wait=check_max_wait)
        if not check_result.ok:
            fail_names = [c.get("name") for c in check_result.failed_checks]
            pending_names = [c.get("name") for c in check_result.pending_checks]
            parts = []
            if fail_names:
                parts.append(f"FAIL={','.join(fail_names)}")
            if pending_names:
                parts.append(f"PENDING={','.join(pending_names)}")
            reason = "; ".join(parts) if parts else (check_result.error_message or "check failed")
            self._append_submission_log(
                {
                    "alpha_id": alpha_id,
                    "submitted": False,
                    "phase": "check",
                    "reason": reason,
                    "name": final_name,
                    "category": final_category,
                    "check_result": asdict(check_result),
                }
            )
            return SubmissionResult(
                alpha_id=alpha_id,
                submitted=False,
                reason=reason,
                check_result=check_result,
            )

        url = f"{self.BASE_URL}/alphas/{alpha_id}/submit"
        try:
            response = self._request("post", url, timeout=30)
            submit_status = response.status_code
            if submit_status in (200, 201):
                confirmed = self._wait_for_submitted_status(alpha_id)
                if confirmed:
                    self._append_submission_log(
                        {
                            "alpha_id": alpha_id,
                            "submitted": True,
                            "phase": "submit",
                            "reason": "submitted",
                            "name": final_name,
                            "category": final_category,
                            "submit_http_status": submit_status,
                            "check_result": asdict(check_result),
                        }
                    )
                    return SubmissionResult(
                        alpha_id=alpha_id,
                        submitted=True,
                        reason="submitted",
                        check_result=check_result,
                        submit_http_status=submit_status,
                    )
                reason = "submit accepted but status still UNSUBMITTED"
                self._append_submission_log(
                    {
                        "alpha_id": alpha_id,
                        "submitted": False,
                        "phase": "submit",
                        "reason": reason,
                        "name": final_name,
                        "category": final_category,
                        "submit_http_status": submit_status,
                        "check_result": asdict(check_result),
                    }
                )
                return SubmissionResult(
                    alpha_id=alpha_id,
                    submitted=False,
                    reason=reason,
                    check_result=check_result,
                    submit_http_status=submit_status,
                )

            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}
            checks = self._extract_checks(payload)
            _, failed, _, pending = self._split_checks(checks)
            parts = []
            if failed:
                parts.append("FAIL=" + ",".join(c.get("name", "UNKNOWN") for c in failed))
            if pending:
                parts.append("PENDING=" + ",".join(c.get("name", "UNKNOWN") for c in pending))
            reason = "; ".join(parts) if parts else (response.text[:300] if response.text else f"HTTP {submit_status}")
            self._append_submission_log(
                {
                    "alpha_id": alpha_id,
                    "submitted": False,
                    "phase": "submit",
                    "reason": reason,
                    "name": final_name,
                    "category": final_category,
                    "submit_http_status": submit_status,
                    "check_result": asdict(check_result),
                }
            )
            return SubmissionResult(
                alpha_id=alpha_id,
                submitted=False,
                reason=reason,
                check_result=check_result,
                submit_http_status=submit_status,
            )
        except requests.exceptions.RequestException as e:
            reason = str(e)
            self._append_submission_log(
                {
                    "alpha_id": alpha_id,
                    "submitted": False,
                    "phase": "submit",
                    "reason": reason,
                    "name": final_name,
                    "category": final_category,
                    "check_result": asdict(check_result),
                }
            )
            return SubmissionResult(
                alpha_id=alpha_id,
                submitted=False,
                reason=reason,
                check_result=check_result,
            )

    def submit_alpha(self, alpha_id: str) -> bool:
        """
        提交 Alpha（包含 Check Submission 与状态确认）

        Args:
            alpha_id: Alpha ID

        Returns:
            bool: 是否提交成功
        """
        result = self.submit_alpha_with_checks(alpha_id)
        if result.submitted:
            logger.info(f"Alpha {alpha_id} 提交成功")
        else:
            logger.error(f"提交失败: {result.reason}")
        return result.submitted

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
