"""
Alpha 学习系统

功能：
1. 结构化存储所有模拟结果
2. 统计分析成功/失败模式
3. 基于历史数据调整生成策略
4. 为进化算法预留接口
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import defaultdict, Counter
import logging

logger = logging.getLogger(__name__)


@dataclass
class AlphaRecord:
    """Alpha 完整记录"""
    # 基本信息
    expression: str
    template_name: str
    category: str
    alpha_type: str

    # 模板参数
    params: Dict

    # 性能指标
    sharpe: float
    fitness: float
    turnover: float
    drawdown: float
    returns: float

    # 元数据
    timestamp: str
    alpha_id: str
    status: str
    submitted: bool

    # 配置
    region: str
    universe: str
    delay: int


class AlphaDatabase:
    """Alpha 结果数据库"""

    def __init__(self, db_path: str = "./results/alpha_history.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alphas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expression TEXT NOT NULL,
                template_name TEXT,
                category TEXT,
                alpha_type TEXT,
                params TEXT,
                sharpe REAL,
                fitness REAL,
                turnover REAL,
                drawdown REAL,
                returns REAL,
                timestamp TEXT,
                alpha_id TEXT,
                status TEXT,
                submitted INTEGER,
                region TEXT,
                universe TEXT,
                delay INTEGER
            )
        """)

        # 创建索引加速查询
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_template ON alphas(template_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_category ON alphas(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_type ON alphas(alpha_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sharpe ON alphas(sharpe)")

        conn.commit()
        conn.close()

    def save_record(self, record: AlphaRecord):
        """保存单条记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO alphas (
                expression, template_name, category, alpha_type, params,
                sharpe, fitness, turnover, drawdown, returns,
                timestamp, alpha_id, status, submitted,
                region, universe, delay
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.expression,
            record.template_name,
            record.category,
            record.alpha_type,
            json.dumps(record.params),
            record.sharpe,
            record.fitness,
            record.turnover,
            record.drawdown,
            record.returns,
            record.timestamp,
            record.alpha_id,
            record.status,
            1 if record.submitted else 0,
            record.region,
            record.universe,
            record.delay
        ))

        conn.commit()
        conn.close()

    def save_batch(self, records: List[AlphaRecord]):
        """批量保存"""
        for record in records:
            self.save_record(record)

    def get_all_records(self, limit: Optional[int] = None) -> List[AlphaRecord]:
        """获取所有记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = "SELECT * FROM alphas ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        records = []
        for row in rows:
            records.append(AlphaRecord(
                expression=row[1],
                template_name=row[2],
                category=row[3],
                alpha_type=row[4],
                params=json.loads(row[5]) if row[5] else {},
                sharpe=row[6],
                fitness=row[7],
                turnover=row[8],
                drawdown=row[9],
                returns=row[10],
                timestamp=row[11],
                alpha_id=row[12],
                status=row[13],
                submitted=bool(row[14]),
                region=row[15],
                universe=row[16],
                delay=row[17]
            ))

        return records

    def query(self, filters: Dict) -> List[AlphaRecord]:
        """条件查询"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        where_clauses = []
        params = []

        for key, value in filters.items():
            if key in ['sharpe', 'fitness', 'turnover', 'drawdown', 'returns']:
                # 数值范围查询
                if isinstance(value, tuple):
                    where_clauses.append(f"{key} BETWEEN ? AND ?")
                    params.extend(value)
                else:
                    where_clauses.append(f"{key} >= ?")
                    params.append(value)
            else:
                where_clauses.append(f"{key} = ?")
                params.append(value)

        query = "SELECT * FROM alphas"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY timestamp DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        records = []
        for row in rows:
            records.append(AlphaRecord(
                expression=row[1],
                template_name=row[2],
                category=row[3],
                alpha_type=row[4],
                params=json.loads(row[5]) if row[5] else {},
                sharpe=row[6],
                fitness=row[7],
                turnover=row[8],
                drawdown=row[9],
                returns=row[10],
                timestamp=row[11],
                alpha_id=row[12],
                status=row[13],
                submitted=bool(row[14]),
                region=row[15],
                universe=row[16],
                delay=row[17]
            ))

        return records


class AlphaAnalyzer:
    """Alpha 统计分析器"""

    def __init__(self, db: AlphaDatabase):
        self.db = db

    def analyze_templates(self, min_sharpe: float = 1.0) -> Dict:
        """分析模板成功率"""
        records = self.db.get_all_records()

        template_stats = defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'avg_sharpe': 0,
            'avg_fitness': 0,
            'avg_turnover': 0,
            'sharpe_sum': 0,
            'fitness_sum': 0,
            'turnover_sum': 0
        })

        for record in records:
            if record.status not in ['COMPLETE', 'PASS']:
                continue

            stats = template_stats[record.template_name]
            stats['total'] += 1
            stats['sharpe_sum'] += record.sharpe
            stats['fitness_sum'] += record.fitness
            stats['turnover_sum'] += record.turnover

            if record.sharpe >= min_sharpe:
                stats['success'] += 1

        # 计算平均值和成功率
        result = {}
        for template, stats in template_stats.items():
            if stats['total'] > 0:
                result[template] = {
                    'total': stats['total'],
                    'success': stats['success'],
                    'success_rate': stats['success'] / stats['total'],
                    'avg_sharpe': stats['sharpe_sum'] / stats['total'],
                    'avg_fitness': stats['fitness_sum'] / stats['total'],
                    'avg_turnover': stats['turnover_sum'] / stats['total']
                }

        # 按成功率排序
        result = dict(sorted(result.items(), key=lambda x: x[1]['success_rate'], reverse=True))
        return result

    def analyze_categories(self, min_sharpe: float = 1.0) -> Dict:
        """分析类别成功率"""
        records = self.db.get_all_records()

        category_stats = defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'avg_sharpe': 0,
            'sharpe_sum': 0
        })

        for record in records:
            if record.status not in ['COMPLETE', 'PASS']:
                continue

            stats = category_stats[record.category]
            stats['total'] += 1
            stats['sharpe_sum'] += record.sharpe

            if record.sharpe >= min_sharpe:
                stats['success'] += 1

        result = {}
        for category, stats in category_stats.items():
            if stats['total'] > 0:
                result[category] = {
                    'total': stats['total'],
                    'success': stats['success'],
                    'success_rate': stats['success'] / stats['total'],
                    'avg_sharpe': stats['sharpe_sum'] / stats['total']
                }

        result = dict(sorted(result.items(), key=lambda x: x[1]['success_rate'], reverse=True))
        return result

    def analyze_parameters(self, template_name: str) -> Dict:
        """分析特定模板的参数分布"""
        records = self.db.query({'template_name': template_name})

        param_stats = defaultdict(lambda: defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'sharpe_sum': 0
        }))

        for record in records:
            if record.status not in ['COMPLETE', 'PASS']:
                continue

            for param_name, param_value in record.params.items():
                stats = param_stats[param_name][param_value]
                stats['total'] += 1
                stats['sharpe_sum'] += record.sharpe

                if record.sharpe >= 1.0:
                    stats['success'] += 1

        # 计算每个参数值的成功率
        result = {}
        for param_name, values in param_stats.items():
            result[param_name] = {}
            for value, stats in values.items():
                if stats['total'] > 0:
                    result[param_name][value] = {
                        'total': stats['total'],
                        'success': stats['success'],
                        'success_rate': stats['success'] / stats['total'],
                        'avg_sharpe': stats['sharpe_sum'] / stats['total']
                    }

            # 按成功率排序
            result[param_name] = dict(sorted(
                result[param_name].items(),
                key=lambda x: x[1]['success_rate'],
                reverse=True
            ))

        return result

    def get_top_performers(self, limit: int = 10, min_sharpe: float = 1.5) -> List[AlphaRecord]:
        """获取表现最好的 Alpha"""
        records = self.db.query({'sharpe': min_sharpe})
        records.sort(key=lambda x: x.sharpe, reverse=True)
        return records[:limit]

    def generate_report(self, output_path: Optional[str] = None) -> str:
        """生成分析报告"""
        records = self.db.get_all_records()

        if not records:
            return "暂无历史数据"

        # 基本统计
        total = len(records)
        successful = len([r for r in records if r.sharpe >= 1.0])
        submitted = len([r for r in records if r.submitted])

        # 模板分析
        template_stats = self.analyze_templates()

        # 类别分析
        category_stats = self.analyze_categories()

        # Top performers
        top_alphas = self.get_top_performers(limit=5)

        # 生成报告
        report = []
        report.append("=" * 80)
        report.append("Alpha 学习系统 - 统计分析报告")
        report.append("=" * 80)
        report.append(f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"数据范围: {records[-1].timestamp} 至 {records[0].timestamp}")
        report.append(f"\n总计: {total} 个 Alpha")
        report.append(f"成功 (Sharpe≥1.0): {successful} 个 ({successful/total*100:.1f}%)")
        report.append(f"已提交: {submitted} 个 ({submitted/total*100:.1f}%)")

        # 模板分析
        report.append("\n" + "=" * 80)
        report.append("模板成功率分析 (Top 10)")
        report.append("=" * 80)
        report.append(f"{'模板名称':<30} {'总数':>8} {'成功':>8} {'成功率':>10} {'平均Sharpe':>12}")
        report.append("-" * 80)

        for i, (template, stats) in enumerate(list(template_stats.items())[:10], 1):
            report.append(
                f"{template:<30} {stats['total']:>8} {stats['success']:>8} "
                f"{stats['success_rate']*100:>9.1f}% {stats['avg_sharpe']:>12.3f}"
            )

        # 类别分析
        report.append("\n" + "=" * 80)
        report.append("类别成功率分析")
        report.append("=" * 80)
        report.append(f"{'类别':<20} {'总数':>8} {'成功':>8} {'成功率':>10} {'平均Sharpe':>12}")
        report.append("-" * 80)

        for category, stats in category_stats.items():
            report.append(
                f"{category:<20} {stats['total']:>8} {stats['success']:>8} "
                f"{stats['success_rate']*100:>9.1f}% {stats['avg_sharpe']:>12.3f}"
            )

        # Top performers
        report.append("\n" + "=" * 80)
        report.append("表现最佳的 Alpha (Top 5)")
        report.append("=" * 80)

        for i, alpha in enumerate(top_alphas, 1):
            report.append(f"\n{i}. {alpha.template_name} ({alpha.category})")
            report.append(f"   Sharpe: {alpha.sharpe:.3f} | Fitness: {alpha.fitness:.3f} | Turnover: {alpha.turnover:.3f}")
            report.append(f"   表达式: {alpha.expression}")
            report.append(f"   参数: {alpha.params}")

        report.append("\n" + "=" * 80)

        report_text = "\n".join(report)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report_text)
            logger.info(f"报告已保存到: {output_path}")

        return report_text


class SmartGenerator:
    """智能生成器（基于统计学习）"""

    def __init__(self, analyzer: AlphaAnalyzer):
        self.analyzer = analyzer

    def get_template_weights(self, alpha_type: str) -> Dict[str, float]:
        """
        获取模板采样权重

        基于历史成功率调整权重：
        - 成功率高的模板权重增加
        - 成功率低的模板权重降低
        - 未测试的模板保持默认权重
        """
        template_stats = self.analyzer.analyze_templates()

        weights = {}
        for template, stats in template_stats.items():
            # 基础权重 = 成功率
            base_weight = stats['success_rate']

            # 考虑样本量：样本少的模板给予探索机会
            sample_bonus = 1.0 / (1.0 + stats['total'] / 10.0)

            # 考虑平均性能
            performance_bonus = max(0, (stats['avg_sharpe'] - 1.0) / 2.0)

            # 综合权重
            weights[template] = base_weight + sample_bonus * 0.3 + performance_bonus * 0.2

        # 归一化
        total_weight = sum(weights.values())
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}

        return weights

    def get_parameter_distribution(self, template_name: str) -> Dict[str, Dict]:
        """
        获取参数采样分布

        返回每个参数的推荐值及其权重
        """
        param_stats = self.analyzer.analyze_parameters(template_name)

        distributions = {}
        for param_name, values in param_stats.items():
            # 计算每个值的权重
            value_weights = {}
            for value, stats in values.items():
                # 权重 = 成功率 * 平均性能
                weight = stats['success_rate'] * (1.0 + stats['avg_sharpe'] / 2.0)
                value_weights[value] = weight

            # 归一化
            total = sum(value_weights.values())
            if total > 0:
                value_weights = {k: v / total for k, v in value_weights.items()}

            distributions[param_name] = value_weights

        return distributions

    def suggest_next_batch(self, alpha_type: str, count: int = 10) -> List[Dict]:
        """
        建议下一批要生成的 Alpha

        策略：
        - 70% 使用高成功率模板
        - 20% 探索中等成功率模板
        - 10% 随机探索
        """
        template_weights = self.get_template_weights(alpha_type)

        if not template_weights:
            logger.warning("暂无历史数据，使用随机生成")
            return []

        suggestions = []

        # 按权重排序
        sorted_templates = sorted(template_weights.items(), key=lambda x: x[1], reverse=True)

        # 70% 高成功率
        high_performers = sorted_templates[:max(1, len(sorted_templates) // 3)]
        for _ in range(int(count * 0.7)):
            template = self._weighted_choice(high_performers)
            suggestions.append({'template': template, 'strategy': 'exploit'})

        # 20% 中等成功率
        mid_performers = sorted_templates[len(sorted_templates)//3:2*len(sorted_templates)//3]
        if mid_performers:
            for _ in range(int(count * 0.2)):
                template = self._weighted_choice(mid_performers)
                suggestions.append({'template': template, 'strategy': 'balanced'})

        # 10% 随机探索
        for _ in range(count - len(suggestions)):
            template = self._weighted_choice(sorted_templates)
            suggestions.append({'template': template, 'strategy': 'explore'})

        return suggestions

    def _weighted_choice(self, items: List[Tuple[str, float]]) -> str:
        """加权随机选择"""
        import random

        if not items:
            return None

        total = sum(weight for _, weight in items)
        r = random.uniform(0, total)

        cumsum = 0
        for item, weight in items:
            cumsum += weight
            if r <= cumsum:
                return item

        return items[-1][0]
