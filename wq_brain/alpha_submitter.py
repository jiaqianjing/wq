"""
Alpha 提交器

负责自动提交和管理 Alpha，包括：
- 批量模拟和提交
- 结果过滤（根据 sharpe, fitness 等指标）
- 相关性检查
- 提交队列管理
"""

import threading
import time
import json
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import os

from .client import WorldQuantBrainClient, AlphaConfig, SimulateResult, Region, Universe, Delay

logger = logging.getLogger(__name__)

try:
    from .learning import AlphaDatabase, AlphaRecord
    LEARNING_ENABLED = True
except ImportError:
    LEARNING_ENABLED = False
    logger.warning("学习模块未启用")


@dataclass
class SubmissionCriteria:
    """提交标准/过滤器"""
    min_sharpe: float = 1.25
    min_fitness: float = 0.7
    max_turnover: float = 0.7

    def check(self, result: SimulateResult) -> bool:
        """检查结果是否符合提交标准"""
        return (
            result.sharpe >= self.min_sharpe and
            result.fitness >= self.min_fitness and
            result.turnover <= self.max_turnover
        )


@dataclass
class SubmissionRecord:
    """提交记录"""
    alpha_id: str
    expression: str
    alpha_type: str
    category: str
    simulate_result: SimulateResult
    submitted: bool
    submit_time: Optional[str] = None
    correlation_checked: bool = False
    correlation_passed: bool = True


@dataclass
class AlphaSettings:
    """Alpha 配置设置（用于策略解耦）"""
    delay: Delay = Delay.DELAY_1
    decay: int = 6
    neutralization: str = "SUBINDUSTRY"
    truncation: float = 0.01
    pasteurization: str = "ON"
    unit_neutral: bool = False
    visualization: bool = False


class AlphaSubmitter:
    """Alpha 提交器"""

    def __init__(self, client: WorldQuantBrainClient,
                 criteria: Optional[SubmissionCriteria] = None,
                 results_dir: str = "./results",
                 enable_learning: bool = True):
        self.client = client
        self.criteria = criteria or SubmissionCriteria()
        self.results_dir = results_dir
        self.submission_history: List[SubmissionRecord] = []

        # 确保结果目录存在
        os.makedirs(results_dir, exist_ok=True)

        # 初始化学习系统
        self.learning_enabled = enable_learning and LEARNING_ENABLED
        if self.learning_enabled:
            self.db = AlphaDatabase(f"{results_dir}/alpha_history.db")
            logger.info("学习系统已启用")
        else:
            self.db = None

    def simulate_and_submit(self, alphas: List[Dict],
                           region: Region = Region.USA,
                           universe: Universe = Universe.TOP3000,
                           auto_submit: bool = True,
                           check_correlation: bool = True,
                           max_correlation: float = 0.7,
                           settings: Optional[AlphaSettings] = None,
                           stop_event: Optional["threading.Event"] = None) -> List[SubmissionRecord]:
        """
        模拟并提交 Alpha 列表

        Args:
            alphas: Alpha 配置列表，每项包含 expression, name, category, type
            region: 交易区域
            universe: 股票池
            auto_submit: 是否自动提交符合条件的 Alpha
            check_correlation: 是否检查相关性
            max_correlation: 最大允许相关性

        Returns:
            List[SubmissionRecord]: 提交记录列表
        """
        records = []
        settings = settings or AlphaSettings()

        for i, alpha in enumerate(alphas, 1):
            if stop_event is not None and stop_event.is_set():
                logger.info("simulate_and_submit interrupted by stop_event at alpha %d/%d", i, len(alphas))
                break
            logger.info(f"处理 Alpha {i}/{len(alphas)}: {alpha.get('name', 'unknown')}")

            try:
                # 创建配置
                config = AlphaConfig(
                    expression=alpha["expression"],
                    region=region,
                    universe=universe,
                    delay=settings.delay,
                    decay=settings.decay,
                    neutralization=settings.neutralization,
                    truncation=settings.truncation,
                    pasteurization=settings.pasteurization,
                    unit_neutral=settings.unit_neutral,
                    visualization=settings.visualization,
                )

                # 模拟
                result = self.client.simulate_alpha(config, stop_event=stop_event)
                alpha_label = result.alpha_id or alpha.get("name", "unknown")

                record = SubmissionRecord(
                    alpha_id=result.alpha_id,
                    expression=alpha["expression"],
                    alpha_type=alpha.get("type", "unknown"),
                    category=alpha.get("category", "unknown"),
                    simulate_result=result,
                    submitted=False
                )

                # 检查是否符合标准
                if self.criteria.check(result):
                    logger.info(f"✓ Alpha {alpha_label} 符合提交标准 "
                              f"(Sharpe: {result.sharpe:.3f}, Fitness: {result.fitness:.3f})")

                    # 检查相关性
                    if check_correlation:
                        correlation = self.client.check_alpha_correlation(result.alpha_id)
                        max_corr = correlation.get("max_correlation", 0)
                        record.correlation_checked = True
                        record.correlation_passed = max_corr < max_correlation

                        if not record.correlation_passed:
                            logger.warning(f"✗ Alpha {alpha_label} 相关性过高 ({max_corr:.3f})，跳过提交")
                            records.append(record)
                            continue

                    # 自动提交
                    if auto_submit:
                        success = self.client.submit_alpha(result.alpha_id)
                        record.submitted = success
                        record.submit_time = datetime.now().isoformat()

                        if success:
                            logger.info(f"✓ Alpha {alpha_label} 提交成功")
                        else:
                            logger.error(f"✗ Alpha {alpha_label} 提交失败")
                else:
                    logger.info(f"✗ Alpha {alpha_label} 不符合标准 "
                              f"(Sharpe: {result.sharpe:.3f}, Fitness: {result.fitness:.3f})")

                records.append(record)

                # 保存到学习系统
                if self.learning_enabled and result.alpha_id:
                    self._save_to_learning_db(alpha, result, region, universe, settings, record.submitted)

                # 保存进度
                self._save_progress(records)

                # 避免请求过快
                time.sleep(2)

            except Exception as e:
                logger.error(f"处理 Alpha 时出错: {e}")
                continue

        self.submission_history.extend(records)
        return records

    def batch_submit_by_type(self, alphas_by_type: Dict[str, List[Dict]],
                            region: Region = Region.USA,
                            universe: Universe = Universe.TOP3000,
                            auto_submit: bool = True) -> Dict[str, List[SubmissionRecord]]:
        """
        按类型批量提交 Alpha

        Args:
            alphas_by_type: 按类型分类的 Alpha 字典
            region: 交易区域
            universe: 股票池
            auto_submit: 是否自动提交

        Returns:
            Dict[str, List[SubmissionRecord]]: 按类型的提交记录
        """
        results = {}

        for alpha_type, alphas in alphas_by_type.items():
            logger.info(f"\n{'='*50}")
            logger.info(f"处理 {alpha_type.upper()} 类型，共 {len(alphas)} 个")
            logger.info(f"{'='*50}")

            # 根据类型调整提交标准
            criteria = self._get_criteria_for_type(alpha_type)
            original_criteria = self.criteria
            self.criteria = criteria

            records = self.simulate_and_submit(
                alphas,
                region=region,
                universe=universe,
                auto_submit=auto_submit
            )

            results[alpha_type] = records
            self.criteria = original_criteria

        return results

    def _get_criteria_for_type(self, alpha_type: str) -> SubmissionCriteria:
        """根据 Alpha 类型获取提交标准"""
        criteria_map = {
            "atom": SubmissionCriteria(  # ATOMs 标准较低
                min_sharpe=1.0,
                min_fitness=0.6,
                max_turnover=0.8
            ),
            "regular": SubmissionCriteria(  # Regular 标准
                min_sharpe=1.25,
                min_fitness=0.7,
                max_turnover=0.7
            ),
            "power_pool": SubmissionCriteria(  # Power Pool 标准较高
                min_sharpe=1.5,
                min_fitness=0.8,
                max_turnover=0.6
            ),
            "superalpha": SubmissionCriteria(  # SuperAlpha 标准最高
                min_sharpe=1.75,
                min_fitness=0.85,
                max_turnover=0.5
            )
        }
        return criteria_map.get(alpha_type, self.criteria)

    def submit_pending_alphas(self) -> int:
        """
        提交所有待提交的 Alpha（之前模拟成功但未提交的）

        Returns:
            int: 成功提交的数量
        """
        submittable = self.client.get_submittable_alphas()
        submitted_count = 0

        for alpha in submittable:
            alpha_id = alpha.get("id")
            if self.client.submit_alpha(alpha_id):
                submitted_count += 1
                time.sleep(1)

        return submitted_count

    def _save_progress(self, records: List[SubmissionRecord]):
        """保存进度到文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.results_dir}/submission_progress_{timestamp}.json"

        data = []
        for record in records:
            record_dict = asdict(record)
            record_dict["simulate_result"] = asdict(record.simulate_result)
            data.append(record_dict)

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

        logger.debug(f"进度已保存到: {filename}")

    def _save_to_learning_db(self, alpha: Dict, result: SimulateResult,
                            region: Region, universe: Universe,
                            settings: AlphaSettings, submitted: bool):
        """保存结果到学习数据库"""
        if not self.db:
            return

        try:
            record = AlphaRecord(
                expression=alpha["expression"],
                template_name=alpha.get("name", "unknown"),
                category=alpha.get("category", "unknown"),
                alpha_type=alpha.get("type", "unknown"),
                params=alpha.get("params", {}),
                sharpe=result.sharpe,
                fitness=result.fitness,
                turnover=result.turnover,
                drawdown=result.drawdown,
                returns=result.returns,
                timestamp=datetime.now().isoformat(),
                alpha_id=result.alpha_id,
                status=result.status,
                submitted=submitted,
                region=region.value,
                universe=universe.value,
                delay=settings.delay.value
            )
            self.db.save_record(record)
            logger.debug(f"已保存到学习数据库: {result.alpha_id}")
        except Exception as e:
            logger.warning(f"保存到学习数据库失败: {e}")

    def generate_report(self, records: List[SubmissionRecord]) -> str:
        """
        生成提交报告

        Args:
            records: 提交记录列表

        Returns:
            str: 报告文本
        """
        total = len(records)
        passed = sum(1 for r in records if self.criteria.check(r.simulate_result))
        submitted = sum(1 for r in records if r.submitted)

        avg_sharpe = sum(r.simulate_result.sharpe for r in records) / total if total > 0 else 0
        avg_fitness = sum(r.simulate_result.fitness for r in records) / total if total > 0 else 0

        report = f"""
{'='*60}
Alpha 提交报告
{'='*60}
总数量: {total}
符合标准: {passed}
成功提交: {submitted}

平均指标:
  Sharpe: {avg_sharpe:.3f}
  Fitness: {avg_fitness:.3f}

各类型统计:
"""
        # 按类型统计
        by_type: Dict[str, List[SubmissionRecord]] = {}
        for r in records:
            by_type.setdefault(r.alpha_type, []).append(r)

        for alpha_type, type_records in by_type.items():
            type_passed = sum(1 for r in type_records if self.criteria.check(r.simulate_result))
            type_submitted = sum(1 for r in type_records if r.submitted)
            report += f"  {alpha_type}: {len(type_records)} 个, " \
                     f"通过 {type_passed}, 提交 {type_submitted}\n"

        report += f"\n{'='*60}\n"

        # 保存报告
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = f"{self.results_dir}/report_{timestamp}.txt"
        with open(report_file, 'w') as f:
            f.write(report)

        logger.info(f"报告已保存到: {report_file}")
        return report
