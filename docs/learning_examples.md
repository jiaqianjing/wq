# Alpha 学习系统示例

## 示例 1: 完整工作流

```bash
# 步骤 1: 初始探索（积累数据）
echo "=== 第一轮：随机生成 50 个 Regular Alpha ==="
python main.py simulate -t regular -c 50

# 步骤 2: 分析结果
echo "=== 分析历史数据 ==="
python smart_generate.py analyze -o reports/round1_analysis.txt

# 步骤 3: 查看建议
echo "=== 获取智能建议 ==="
python smart_generate.py suggest -t regular -c 20

# 步骤 4: 智能生成
echo "=== 第二轮：基于学习的智能生成 ==="
python smart_generate.py run -t regular -c 20

# 步骤 5: 再次分析，对比改进
echo "=== 对比分析 ==="
python smart_generate.py analyze -o reports/round2_analysis.txt
diff reports/round1_analysis.txt reports/round2_analysis.txt
```

## 示例 2: 参数优化

```bash
# 分析特定模板的参数效果
python smart_generate.py analyze --template momentum_reversal

# 输出示例：
# 参数: window
# 值              总数     成功   成功率  平均Sharpe
# --------------------------------------------------------
# 20              15       12     80.0%      1.456
# 10              12        8     66.7%      1.234
# 30              10        5     50.0%      1.123

# 结论：window=20 表现最好，下次生成时应该增加这个参数的权重
```

## 示例 3: 多类型并行优化

```bash
# 为每种类型独立积累数据和优化
for type in atom regular power_pool; do
    echo "=== 处理 $type 类型 ==="

    # 初始探索
    python main.py simulate -t $type -c 30

    # 分析
    python smart_generate.py analyze -o reports/${type}_analysis.txt

    # 智能生成
    python smart_generate.py run -t $type -c 20 --submit
done
```

## 示例 4: 查询数据库

```bash
# 查看成功率最高的模板
sqlite3 results/alpha_history.db << EOF
SELECT
  template_name,
  COUNT(*) as total,
  SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) as success,
  ROUND(AVG(sharpe), 3) as avg_sharpe,
  ROUND(100.0 * SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate
FROM alphas
WHERE status IN ('COMPLETE', 'PASS')
GROUP BY template_name
HAVING total >= 5
ORDER BY success_rate DESC, avg_sharpe DESC
LIMIT 10;
EOF

# 查看最佳 Alpha
sqlite3 results/alpha_history.db << EOF
SELECT
  template_name,
  category,
  sharpe,
  fitness,
  turnover,
  expression
FROM alphas
WHERE sharpe >= 1.5
ORDER BY sharpe DESC
LIMIT 5;
EOF

# 查看参数分布
sqlite3 results/alpha_history.db << EOF
SELECT
  template_name,
  params,
  sharpe,
  fitness
FROM alphas
WHERE template_name = 'momentum_reversal'
  AND sharpe >= 1.0
ORDER BY sharpe DESC;
EOF
```

## 示例 5: 渐进式改进

```python
# 可以编写脚本自动化迭代优化
import subprocess
import time

def run_iteration(iteration, alpha_type, count):
    print(f"\n{'='*60}")
    print(f"迭代 {iteration}: {alpha_type} 类型")
    print(f"{'='*60}")

    # 智能生成
    subprocess.run([
        'python', 'smart_generate.py', 'run',
        '-t', alpha_type,
        '-c', str(count)
    ])

    # 分析
    report_file = f'reports/iteration_{iteration}_{alpha_type}.txt'
    subprocess.run([
        'python', 'smart_generate.py', 'analyze',
        '-o', report_file
    ])

    # 等待避免 API 限制
    time.sleep(10)

    return report_file

# 运行 5 轮迭代
for i in range(1, 6):
    report = run_iteration(i, 'regular', 20)
    print(f"报告已保存: {report}")
```

## 示例 6: 成功率追踪

```bash
# 创建脚本追踪成功率变化
cat > track_success_rate.sh << 'EOF'
#!/bin/bash

echo "日期,总数,成功数,成功率,平均Sharpe" > success_rate_history.csv

sqlite3 results/alpha_history.db << SQL
SELECT
  DATE(timestamp) as date,
  COUNT(*) as total,
  SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) as success,
  ROUND(100.0 * SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate,
  ROUND(AVG(sharpe), 3) as avg_sharpe
FROM alphas
WHERE status IN ('COMPLETE', 'PASS')
GROUP BY DATE(timestamp)
ORDER BY date;
SQL

echo "成功率历史已保存到 success_rate_history.csv"
EOF

chmod +x track_success_rate.sh
./track_success_rate.sh
```

## 预期效果

### 第一轮（随机生成）
```
总数量: 50
符合标准: 8 (16%)
平均 Sharpe: 0.85
```

### 第二轮（智能生成）
```
总数量: 20
符合标准: 6 (30%)  ← 成功率提升
平均 Sharpe: 1.12   ← 平均性能提升
```

### 第三轮（持续优化）
```
总数量: 20
符合标准: 9 (45%)  ← 继续提升
平均 Sharpe: 1.28
```

## 关键指标

监控以下指标来评估学习效果：

1. **成功率**: 符合提交标准的比例
2. **平均 Sharpe**: 整体性能水平
3. **提交率**: 实际提交的比例
4. **模板多样性**: 避免过度集中在少数模板

## 调试技巧

### 查看学习系统是否启用
```bash
python main.py simulate -t regular -c 1 2>&1 | grep "学习系统"
# 应该看到: "学习系统已启用"
```

### 检查数据库
```bash
sqlite3 results/alpha_history.db "SELECT COUNT(*) FROM alphas;"
# 应该返回记录数
```

### 验证权重计算
```python
from wq_brain.learning import AlphaDatabase, AlphaAnalyzer, SmartGenerator

db = AlphaDatabase()
analyzer = AlphaAnalyzer(db)
smart_gen = SmartGenerator(analyzer)

# 查看模板权重
weights = smart_gen.get_template_weights('regular')
for template, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"{template}: {weight:.4f}")
```
