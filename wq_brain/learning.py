"""
Alpha 学习系统

功能：
1. 结构化存储所有模拟结果
2. 基于历史数据估计模板权重
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass
from collections import defaultdict
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

    def list_records(self) -> List[AlphaRecord]:
        """获取所有记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alphas ORDER BY timestamp DESC")
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
    """Alpha 模板统计分析器"""

    def __init__(self, db: AlphaDatabase):
        self.db = db

    def analyze_templates(self, min_sharpe: float = 1.0) -> Dict:
        """分析模板成功率"""
        records = self.db.list_records()

        template_stats = defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'avg_sharpe': 0,
            'sharpe_sum': 0,
        })

        for record in records:
            if record.status not in ['COMPLETE', 'PASS']:
                continue

            stats = template_stats[record.template_name]
            stats['total'] += 1
            stats['sharpe_sum'] += record.sharpe

            if record.sharpe >= min_sharpe:
                stats['success'] += 1

        result = {}
        for template, stats in template_stats.items():
            if stats['total'] > 0:
                result[template] = {
                    'total': stats['total'],
                    'success': stats['success'],
                    'success_rate': stats['success'] / stats['total'],
                    'avg_sharpe': stats['sharpe_sum'] / stats['total'],
                }

        result = dict(sorted(result.items(), key=lambda x: x[1]['success_rate'], reverse=True))
        return result


class SmartGenerator:
    """基于历史表现估计模板权重"""

    def __init__(self, analyzer: AlphaAnalyzer):
        self.analyzer = analyzer

    def get_template_weights(self, alpha_type: str) -> Dict[str, float]:
        """获取模板采样权重。"""
        template_stats = self.analyzer.analyze_templates()

        weights = {}
        for template, stats in template_stats.items():
            base_weight = stats['success_rate']
            sample_bonus = 1.0 / (1.0 + stats['total'] / 10.0)
            performance_bonus = max(0, (stats['avg_sharpe'] - 1.0) / 2.0)
            weights[template] = base_weight + sample_bonus * 0.3 + performance_bonus * 0.2

        total_weight = sum(weights.values())
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}

        return weights
