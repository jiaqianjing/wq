# WorldQuant Brain Alpha 自动提交系统

一个完整的自动化工具，用于生成、模拟和提交 WorldQuant Brain 平台上的 Alpha 因子。

## 功能特性

- **四种 Alpha 类型支持**:
  - **ATOMs**: 简单、基础的 Alpha 表达式
  - **Regular Alphas**: 常规 Alpha 表达式
  - **Power Pool Alphas**: 高潜力 Alpha 表达式
  - **SuperAlphas**: 组合多个简单 Alpha 的复杂表达式

- **自动化流程**:
  - 自动生成 Alpha 表达式
  - 自动模拟回测
  - 根据指标（Sharpe, Fitness, Turnover）自动筛选
  - 相关性检查避免重复
  - 自动提交符合条件的 Alpha

- **灵活配置**:
  - 支持多种交易区域（USA, CHN, EUR, JPN, TWN, KOR, GBR, DEU）
  - 可自定义提交标准
  - 支持批量操作

## 项目结构

```
.
├── wq_brain/              # 核心模块
│   ├── __init__.py
│   ├── client.py          # API 客户端
│   ├── alpha_generator.py # Alpha 生成器
│   └── alpha_submitter.py # Alpha 提交器
├── docs/                  # 量化探索笔记
├── main.py                # 主入口脚本
├── config.yaml            # 配置文件
├── .env.example           # 环境变量示例
├── pyproject.toml         # 项目配置
└── results/               # 结果输出目录
```

## 安装

1. 克隆仓库:
```bash
git clone <repo-url>
cd wq
```

2. 安装依赖:
```bash
pip install -e .
# 或使用 uv
uv pip install -e .
```

3. 配置环境变量:
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的 WorldQuant Brain 账号信息
```

## 使用方法

### 1. 生成 Alpha 表达式（不模拟）

```bash
# 生成 Regular Alpha
python main.py generate -t regular -c 10

# 生成 WorldQuant 101 Alpha 变体
python main.py generate -t 101 -c 20 -o alphas.json

# 生成所有类型
python main.py generate -t all -c 5
```

### 2. 模拟 Alpha（不提交）

```bash
# 模拟 10 个 Regular Alpha
python main.py simulate -t regular -c 10

# 模拟 Power Pool Alpha（中国市场）
python main.py simulate -t power_pool -c 5 -r CHN
```

### 3. 模拟并提交 Alpha

```bash
# 提交 5 个 Regular Alpha
python main.py submit -t regular -c 5

# 提交所有类型并自定义标准
python main.py submit -t all -c 5 --min-sharpe 1.5 --min-fitness 0.8

# 仅提交 Power Pool Alpha（高标准）
python main.py submit -t power_pool -c 5 --min-sharpe 1.5
```

### 4. 提交待处理 Alpha

```bash
# 提交之前模拟成功但尚未提交的 Alpha
python main.py pending
```

## 文档与笔记

`docs/` 用于记录每次批量生成与模拟的策略配置，以及对应的心得与复盘，方便积累经验并调整下一次生成策略。

入口文档: `docs/README.md`

## Alpha 类型说明

### ATOMs
简单、基础的 Alpha 表达式，通常只使用 1-2 个数据字段。

**提交标准**:
- Sharpe >= 1.0
- Fitness >= 0.6
- Turnover <= 0.8

### Regular Alphas
常规 Alpha 表达式，使用标准的技术指标和公式。

**提交标准**:
- Sharpe >= 1.25
- Fitness >= 0.7
- Turnover <= 0.7

### Power Pool Alphas
高潜力 Alpha 表达式，使用更复杂的组合和高级技术指标。

**提交标准**:
- Sharpe >= 1.5
- Fitness >= 0.8
- Turnover <= 0.6

### SuperAlphas
组合 Alpha，由多个简单 Alpha 组合而成，通常具有更高的稳定性。

**提交标准**:
- Sharpe >= 1.75
- Fitness >= 0.85
- Turnover <= 0.5

## Alpha 模板示例

### 动量类
```python
# 简单反转
-ts_returns(close, 5)

# 高级动量
-ts_rank(ts_corr(rank(close), rank(volume), 10), 5)
```

### 均值回归类
```python
# 价格偏离均值
(close - ts_mean(close, 20)) / ts_std(close, 20)

# VWAP 偏离
(close - vwap) / close
```

### 成交量类
```python
# 成交量与价格相关性
-ts_corr(volume, close, 20)

# 成交量突破
rank(volume / ts_mean(volume, 20))
```

### 波动率类
```python
# 波动率
-ts_std(ts_returns(close, 1), 20)

# 收益偏度
ts_skewness(ts_returns(close, 1), 60)
```

## 配置文件

编辑 `config.yaml` 来自定义默认设置：

```yaml
# 认证信息 (建议使用环境变量)
auth:
  username: ${WQB_USERNAME}
  password: ${WQB_PASSWORD}

# 交易设置
trading:
  region: USA
  universe: TOP3000
  delay: 1

# 提交标准
criteria:
  atom:
    min_sharpe: 1.0
    min_fitness: 0.6
    max_turnover: 0.8
  # ...
```

## 注意事项

1. **API 限制**: WorldQuant Brain API 有速率限制，请避免过于频繁的请求
2. **相关性检查**: 系统会自动检查 Alpha 之间的相关性，避免提交高度相关的 Alpha
3. **结果保存**: 所有模拟结果和提交记录都会保存在 `results/` 目录
4. **日志**: 运行日志保存在 `wq_brain.log`

## 扩展开发

### 添加自定义 Alpha 模板

在 `alpha_generator.py` 中添加新的模板：

```python
AlphaTemplate(
    name="my_custom_alpha",
    expression="rank(close) * ts_std(volume, {window})",
    category="custom",
    description="我的自定义 Alpha",
    params={"window": [10, 20, 60]}
)
```

### 使用自定义筛选标准

```python
from wq_brain.alpha_submitter import SubmissionCriteria

criteria = SubmissionCriteria(
    min_sharpe=2.0,
    min_fitness=0.9,
    max_turnover=0.5
)
submitter = AlphaSubmitter(client, criteria=criteria)
```

## License

MIT License
