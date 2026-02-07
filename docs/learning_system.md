# Alpha 学习系统使用指南

## 概述

学习系统通过分析历史模拟结果，自动识别成功模式，指导下一次生成。

## 工作流程

```
1. 模拟 Alpha → 2. 保存结果到数据库 → 3. 统计分析 → 4. 调整生成策略 → 回到步骤1
```

## 快速开始

### 1. 首次运行（积累数据）

```bash
# 使用普通方式生成并模拟，系统会自动保存结果
python main.py simulate -t regular -c 20

# 或者提交
python main.py submit -t regular -c 20
```

**重要**: 学习系统会自动启用，所有模拟结果都会保存到 `results/alpha_history.db`

### 2. 分析历史数据

```bash
# 查看统计报告
python smart_generate.py analyze

# 保存报告到文件
python smart_generate.py analyze -o report.txt

# 分析特定模板的参数效果
python smart_generate.py analyze --template momentum_reversal
```

报告包含：
- 总体统计（成功率、提交率）
- 模板成功率排名
- 类别成功率分析
- Top 5 表现最佳的 Alpha

### 3. 获取智能建议

```bash
# 建议下一批要生成的模板
python smart_generate.py suggest -t regular -c 10

# 建议 Power Pool 类型
python smart_generate.py suggest -t power_pool -c 5
```

输出包含：
- 策略分布（利用/平衡/探索）
- 推荐模板列表
- 模板权重分析

### 4. 执行智能生成

```bash
# 基于历史数据智能生成（仅模拟）
python smart_generate.py run -t regular -c 10

# 智能生成并自动提交
python smart_generate.py run -t regular -c 10 --submit

# 指定区域
python smart_generate.py run -t regular -c 10 -r CHN
```

## 学习机制详解

### 数据收集

每次模拟后，系统自动保存：
- Alpha 表达式
- 模板名称和参数
- 性能指标（sharpe, fitness, turnover, drawdown）
- 配置信息（region, universe, delay）
- 提交状态

### 统计分析

系统分析以下维度：

1. **模板成功率**
   - 每个模板的总数、成功数、成功率
   - 平均 Sharpe、Fitness、Turnover

2. **类别成功率**
   - 动量、均值回归、成交量等类别的表现

3. **参数效果**
   - 每个参数值的成功率和平均性能
   - 识别最优参数范围

4. **Top Performers**
   - 表现最好的 Alpha 及其特征

### 智能生成策略

基于统计结果，系统采用三种策略：

1. **利用 (Exploit) - 70%**
   - 使用成功率最高的模板
   - 使用最优参数分布

2. **平衡 (Balanced) - 20%**
   - 使用中等成功率的模板
   - 探索次优参数

3. **探索 (Explore) - 10%**
   - 随机选择模板
   - 发现新的可能性

### 权重计算

模板权重 = 成功率 + 样本量奖励 + 性能奖励

- **成功率**: 历史成功比例
- **样本量奖励**: 样本少的模板获得探索机会
- **性能奖励**: 基于平均 Sharpe 的额外加分

## 数据库查询

可以直接查询 SQLite 数据库：

```bash
sqlite3 results/alpha_history.db

# 查看所有记录
SELECT * FROM alphas ORDER BY sharpe DESC LIMIT 10;

# 查看特定模板
SELECT * FROM alphas WHERE template_name = 'momentum_reversal';

# 统计成功率
SELECT
  template_name,
  COUNT(*) as total,
  SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) as success,
  AVG(sharpe) as avg_sharpe
FROM alphas
GROUP BY template_name
ORDER BY success DESC;
```

## 最佳实践

### 1. 积累足够数据

- 建议至少模拟 100 个 Alpha 后再使用智能生成
- 每种类型都需要独立积累数据

### 2. 定期分析

```bash
# 每次运行后查看报告
python smart_generate.py analyze

# 关注成功率变化趋势
```

### 3. 渐进式优化

```bash
# 第1轮：随机生成，积累数据
python main.py simulate -t regular -c 50

# 第2轮：分析并调整
python smart_generate.py analyze
python smart_generate.py suggest -t regular -c 20

# 第3轮：智能生成
python smart_generate.py run -t regular -c 20

# 第4轮：继续优化
python smart_generate.py analyze
python smart_generate.py run -t regular -c 20
```

### 4. 多类型并行

```bash
# 为每种类型独立积累数据
python main.py simulate -t atom -c 30
python main.py simulate -t regular -c 30
python main.py simulate -t power_pool -c 30

# 分别分析
python smart_generate.py suggest -t atom -c 10
python smart_generate.py suggest -t regular -c 10
python smart_generate.py suggest -t power_pool -c 10
```

## 进阶功能（未来）

### 阶段2：参数优化

- 基于历史数据自动调整参数采样分布
- 贝叶斯优化参数空间

### 阶段3：进化算法

- 对成功的 Alpha 进行变异
- 交叉组合高性能 Alpha
- 种群管理和淘汰机制

### 阶段4：机器学习

- 训练预测模型（预测 Sharpe/Fitness）
- 特征工程（提取 Alpha 表达式特征）
- 强化学习生成策略

## 故障排除

### 问题：学习系统未启用

```
WARNING - 学习模块未启用
```

**解决**: 确保 `learning.py` 文件存在且无语法错误

### 问题：数据库为空

```
暂无历史数据
```

**解决**: 先运行普通模拟积累数据
```bash
python main.py simulate -t regular -c 20
```

### 问题：权重计算异常

**解决**: 检查数据库中是否有有效的成功记录（sharpe >= 1.0）

## 示例工作流

```bash
# Day 1: 初始探索
python main.py simulate -t regular -c 50
python smart_generate.py analyze -o day1_report.txt

# Day 2: 智能生成
python smart_generate.py suggest -t regular -c 20
python smart_generate.py run -t regular -c 20
python smart_generate.py analyze -o day2_report.txt

# Day 3: 继续优化
python smart_generate.py run -t regular -c 20 --submit
python smart_generate.py analyze -o day3_report.txt

# 对比报告，观察成功率提升
diff day1_report.txt day3_report.txt
```

## 数据管理

### 备份数据库

```bash
cp results/alpha_history.db results/alpha_history_backup_$(date +%Y%m%d).db
```

### 清理旧数据

```bash
sqlite3 results/alpha_history.db "DELETE FROM alphas WHERE timestamp < '2026-01-01';"
```

### 导出数据

```bash
sqlite3 results/alpha_history.db ".mode csv" ".output alphas.csv" "SELECT * FROM alphas;"
```
