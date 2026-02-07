# 🧠 Alpha 学习系统

## 核心理念

**从"盲目生成"到"智能进化"**

传统方式：随机生成 → 模拟 → 丢弃结果 → 再次随机生成（无进步）

学习系统：生成 → 模拟 → **分析学习** → **智能生成** → 持续改进 ✨

## 三大核心功能

### 1. 📊 数据收集与存储

- 自动保存所有模拟结果到 SQLite 数据库
- 记录完整信息：表达式、模板、参数、性能指标、配置
- 支持高效查询和分析

### 2. 📈 统计分析

- **模板成功率**: 哪些模板表现最好？
- **参数优化**: 哪些参数值最有效？
- **类别分析**: 动量、均值回归、成交量等哪个类别更优？
- **Top Performers**: 学习最佳 Alpha 的特征

### 3. 🎯 智能生成

基于历史数据，采用三种策略：

- **70% 利用 (Exploit)**: 使用成功率最高的模板和参数
- **20% 平衡 (Balanced)**: 探索中等成功率的选项
- **10% 探索 (Explore)**: 随机探索，发现新可能

## 快速开始

### 第一步：积累数据

```bash
# 运行普通模拟，系统自动保存结果
python main.py simulate -t regular -c 50
```

### 第二步：分析学习

```bash
# 查看统计报告
python smart_generate.py analyze

# 获取智能建议
python smart_generate.py suggest -t regular -c 20
```

### 第三步：智能生成

```bash
# 基于学习结果生成
python smart_generate.py run -t regular -c 20
```

## 工作原理

### 权重计算公式

```
模板权重 = 成功率 + 样本量奖励 + 性能奖励

其中：
- 成功率 = 历史成功次数 / 总次数
- 样本量奖励 = 1 / (1 + 样本数/10) × 0.3  # 给少样本模板探索机会
- 性能奖励 = max(0, (平均Sharpe - 1.0) / 2.0) × 0.2
```

### 参数分布优化

对每个模板的每个参数，计算各个值的权重：

```
参数值权重 = 成功率 × (1 + 平均Sharpe / 2.0)
```

然后按权重采样，优先选择高性能参数值。

## 预期效果

### 实验数据（模拟）

| 轮次 | 方式 | 总数 | 成功数 | 成功率 | 平均Sharpe |
|------|------|------|--------|--------|------------|
| 1    | 随机 | 50   | 8      | 16%    | 0.85       |
| 2    | 智能 | 20   | 6      | 30%    | 1.12       |
| 3    | 智能 | 20   | 9      | 45%    | 1.28       |
| 4    | 智能 | 20   | 11     | 55%    | 1.42       |

**成功率提升 3.4 倍！**

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    AlphaSubmitter                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  simulate_and_submit()                           │  │
│  │    ↓                                             │  │
│  │  _save_to_learning_db()  ← 自动保存             │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   AlphaDatabase                         │
│  - SQLite 存储                                          │
│  - 索引优化查询                                         │
│  - save_record() / query()                             │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   AlphaAnalyzer                         │
│  - analyze_templates()      模板成功率                 │
│  - analyze_categories()     类别分析                   │
│  - analyze_parameters()     参数优化                   │
│  - get_top_performers()     最佳Alpha                  │
│  - generate_report()        统计报告                   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   SmartGenerator                        │
│  - get_template_weights()   计算模板权重               │
│  - get_parameter_distribution()  参数分布              │
│  - suggest_next_batch()     建议下一批                 │
│  - 70% exploit / 20% balanced / 10% explore            │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                  AlphaGenerator                         │
│  基于权重和分布生成新的 Alpha                           │
└─────────────────────────────────────────────────────────┘
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `wq_brain/learning.py` | 核心学习模块 |
| `smart_generate.py` | 学习系统 CLI |
| `docs/learning_system.md` | 详细使用指南 |
| `docs/learning_examples.md` | 示例和最佳实践 |
| `results/alpha_history.db` | SQLite 数据库 |

## 命令速查

```bash
# 分析
python smart_generate.py analyze                    # 查看报告
python smart_generate.py analyze -o report.txt      # 保存报告
python smart_generate.py analyze --template xxx     # 分析特定模板

# 建议
python smart_generate.py suggest -t regular -c 10   # 获取建议

# 运行
python smart_generate.py run -t regular -c 10       # 智能生成
python smart_generate.py run -t regular -c 10 --submit  # 生成并提交
```

## 数据库查询

```bash
# 查看总记录数
sqlite3 results/alpha_history.db "SELECT COUNT(*) FROM alphas;"

# 查看成功率最高的模板
sqlite3 results/alpha_history.db "
SELECT template_name, COUNT(*) as total,
       SUM(CASE WHEN sharpe >= 1.0 THEN 1 ELSE 0 END) as success,
       ROUND(AVG(sharpe), 3) as avg_sharpe
FROM alphas
GROUP BY template_name
ORDER BY success DESC
LIMIT 10;
"

# 查看最佳 Alpha
sqlite3 results/alpha_history.db "
SELECT template_name, sharpe, fitness, expression
FROM alphas
WHERE sharpe >= 1.5
ORDER BY sharpe DESC
LIMIT 5;
"
```

## 未来扩展

### 阶段 2: 参数优化（计划中）

- 贝叶斯优化参数空间
- 自动调整参数采样分布
- 多目标优化（Sharpe + Fitness + Turnover）

### 阶段 3: 进化算法（计划中）

- 变异：修改成功 Alpha 的部分参数
- 交叉：组合两个高性能 Alpha
- 种群管理：保留精英，淘汰弱者

### 阶段 4: 机器学习（计划中）

- 特征提取：从表达式中提取特征
- 预测模型：预测 Sharpe/Fitness
- 强化学习：学习生成策略

## 最佳实践

1. **积累足够数据**: 至少 100 个样本后再使用智能生成
2. **定期分析**: 每次运行后查看报告，了解改进情况
3. **渐进式优化**: 不要一次性生成太多，小批量迭代更有效
4. **多类型并行**: 每种 Alpha 类型独立积累数据和优化
5. **备份数据库**: 定期备份 `alpha_history.db`

## 故障排除

### 学习系统未启用

```
WARNING - 学习模块未启用
```

**解决**: 检查 `wq_brain/learning.py` 是否存在且无语法错误

### 暂无历史数据

```
暂无历史数据
```

**解决**: 先运行普通模拟积累数据
```bash
python main.py simulate -t regular -c 20
```

### 数据库损坏

**解决**: 删除并重新创建
```bash
rm results/alpha_history.db
python main.py simulate -t regular -c 10
```

## 贡献

欢迎提出改进建议！可以考虑的方向：

- 更复杂的权重计算算法
- 可视化分析工具
- 实时监控面板
- 参数优化算法
- 进化算法实现

## License

MIT License
