# WorldQuant Brain Alpha 自动提交系统 - AI Agent 指南

> 本文档为 AI 编程助手准备的项目指南，包含项目架构、技术栈、开发规范等关键信息。

---

## 项目概述

这是一个用于 WorldQuant Brain 平台的 Alpha 因子自动化生成、模拟和提交系统。

**核心功能**:
- 自动生成多种类型的 Alpha 表达式（ATOMs、Regular、Power Pool、SuperAlphas）
- 通过 WorldQuant Brain API 进行 Alpha 模拟回测
- 根据 Sharpe、Fitness、Turnover 等指标自动筛选
- 相关性检查避免重复提交
- 自动提交符合条件的 Alpha

---

## 技术栈

| 组件 | 版本/工具 |
|------|----------|
| Python | >= 3.10（当前指定 3.12） |
| 包管理器 | `uv`（现代 Python 包管理器） |
| HTTP 客户端 | `requests` |
| 配置解析 | `pyyaml`, `python-dotenv` |
| 代码格式化 | `black` |
| 代码检查 | `ruff` |
| 测试框架 | `pytest` |

---

## 项目结构

```
.
├── wq_brain/                      # 核心模块
│   ├── __init__.py               # 模块导出
│   ├── client.py                 # API 客户端（认证、模拟、提交）
│   ├── alpha_generator.py        # Alpha 表达式生成器
│   └── alpha_submitter.py        # Alpha 提交器（筛选、提交、报告）
├── main.py                        # CLI 主入口
├── config.yaml                    # YAML 配置文件
├── pyproject.toml                 # 项目元数据（uv 使用）
├── uv.lock                        # 依赖锁定文件
├── .python-version                # Python 版本指定（3.12）
├── .env.example                   # 环境变量模板
├── .env                           # 本地环境变量（敏感信息，gitignored）
├── .gitignore                     # Git 忽略规则
└── results/                       # 运行结果输出目录（自动生成）
```

---

## 安装与运行

### 1. 环境准备

```bash
# 确保已安装 uv
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv pip install -e .
```

### 2. 配置认证信息

```bash
cp .env.example .env
# 编辑 .env 填入 WorldQuant Brain 账号
# WQB_USERNAME=your_username
# WQB_PASSWORD=your_password
```

### 3. 运行命令

```bash
# 仅生成 Alpha 表达式（不模拟）
python main.py generate -t regular -c 10
python main.py generate -t 101 -c 20 -o alphas.json

# 仅模拟 Alpha（不提交）
python main.py simulate -t regular -c 10 -r USA

# 模拟并提交符合条件的 Alpha
python main.py submit -t power_pool -c 5 -r CHN
python main.py submit -t all -c 5 --min-sharpe 1.5 --min-fitness 0.8

# 提交待处理的 Alpha（之前模拟成功但未提交）
python main.py pending
```

---

## 代码组织

### 模块职责

| 模块 | 职责 |
|------|------|
| `client.py` | WorldQuant Brain API 客户端，处理认证、模拟、提交、相关性检查 |
| `alpha_generator.py` | 生成 Alpha 表达式模板，支持 4 种类型（ATOMs/Regular/Power Pool/SuperAlpha） |
| `alpha_submitter.py` | 管理模拟和提交流程，包含筛选标准、批量处理、报告生成 |
| `main.py` | CLI 入口，命令行参数解析，命令调度 |

### 核心数据类

```python
# client.py
@dataclass
class AlphaConfig:
    expression: str
    region: Region = Region.USA
    universe: Unviverse = Unviverse.TOP3000
    delay: Delay = Delay.DELAY_1
    decay: int = 0
    neutralization: str = "SUBINDUSTRY"  # MARKET, INDUSTRY, SUBINDUSTRY, SECTOR, NONE
    truncation: float = 0.08
    pasteurization: str = "ON"

@dataclass  
class SimulateResult:
    alpha_id: str
    status: str
    sharpe: float
    fitness: float
    turnover: float
    returns: float
    drawdown: float
    margin: float
    is_submittable: bool

# alpha_submitter.py
@dataclass
class SubmissionCriteria:
    min_sharpe: float = 1.25
    min_fitness: float = 0.7
    max_turnover: float = 0.7
    max_drawdown: float = 0.1
    min_returns: float = 0.0
```

### Alpha 类型与提交标准

| 类型 | Sharpe | Fitness | Turnover | Drawdown |
|------|--------|---------|----------|----------|
| ATOMs | >= 1.0 | >= 0.6 | <= 0.8 | <= 0.15 |
| Regular | >= 1.25 | >= 0.7 | <= 0.7 | <= 0.1 |
| Power Pool | >= 1.5 | >= 0.8 | <= 0.6 | <= 0.08 |
| SuperAlpha | >= 1.75 | >= 0.85 | <= 0.5 | <= 0.05 |

---

## 代码风格规范

### 命名规范
- **类名**: PascalCase（如 `AlphaGenerator`, `SubmissionCriteria`）
- **函数/方法**: snake_case（如 `simulate_and_submit`, `generate_atoms`）
- **常量**: UPPER_CASE（如 `DATA_FIELDS`, `TS_OPERATORS`）
- **私有方法**: 单下划线前缀（如 `_fill_template`, `_save_progress`）

### 类型注解
- 必须使用类型注解
- 使用 `typing` 模块：
  - `List[Dict]`, `Optional[str]`, `Callable`
  - 返回类型明确标注

示例:
```python
def generate_regular_alphas(self, count: int = 10) -> List[Dict]:
    """生成 Regular Alphas"""
    pass
```

### 文档字符串
- 使用 `"""docstring"""` 格式
- 包含 Args、Returns 说明
- 中文注释和文档

示例:
```python
def simulate_alpha(self, config: AlphaConfig) -> SimulateResult:
    """
    模拟单个 Alpha

    Args:
        config: Alpha 配置

    Returns:
        SimulateResult: 模拟结果
    """
```

### 日志规范
- 使用 `logging` 模块，避免 `print()`
- 日志格式：`'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`
- 日志级别：
  - `INFO`: 正常流程信息
  - `WARNING`: 警告（如相关性过高）
  - `ERROR`: 错误（如认证失败）
  - `DEBUG`: 调试信息（如进度保存）

---

## 配置文件

### config.yaml 结构

```yaml
auth:
  username: ${WQB_USERNAME}      # 环境变量引用
  password: ${WQB_PASSWORD}

trading:
  region: USA                     # USA, CHN, EUR, JPN, TWN, KOR, GBR, DEU
  universe: TOP3000               # TOP100, TOP200, TOP500, TOP1000, TOP2000, TOP3000
  delay: 1                        # 0 或 1

criteria:
  atom: { min_sharpe: 1.0, min_fitness: 0.6, max_turnover: 0.8, max_drawdown: 0.15 }
  regular: { min_sharpe: 1.25, min_fitness: 0.7, max_turnover: 0.7, max_drawdown: 0.1 }
  power_pool: { min_sharpe: 1.5, min_fitness: 0.8, max_turnover: 0.6, max_drawdown: 0.08 }
  superalpha: { min_sharpe: 1.75, min_fitness: 0.85, max_turnover: 0.5, max_drawdown: 0.05 }

generation:
  atoms: 10
  regular: 10
  power_pool: 10
  superalphas: 5

settings:
  auto_submit: true
  check_correlation: true
  max_correlation: 0.7
  results_dir: "./results"
  log_level: INFO
```

### 环境变量

```bash
# 必需
WQB_USERNAME=your_username
WQB_PASSWORD=your_password

# 可选
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

---

## 测试策略

### 当前状态
- 项目已配置 `pytest` 但未创建测试目录
- 依赖中包含 `pytest>=8.0.0`

### 建议添加的测试
```
tests/
├── __init__.py
├── test_client.py           # API 客户端测试（需 mock）
├── test_alpha_generator.py  # Alpha 生成器测试
├── test_alpha_submitter.py  # 提交器逻辑测试
└── conftest.py              # 共享 fixtures
```

### 运行测试
```bash
# 使用 uv 运行 pytest
uv run pytest

# 格式化代码
uv run black wq_brain/ main.py

# 代码检查
uv run ruff check wq_brain/ main.py
```

---

## 安全注意事项

### 凭证管理
- ✅ **正确做法**: 使用 `.env` 文件 + `python-dotenv` 加载
- ❌ **禁止做法**: 硬编码密码到代码或配置文件中
- `.env` 已添加到 `.gitignore`，不会被提交

### API 安全
- Token 有效期约 23 小时（82800 秒），自动刷新
- 请求间隔控制（`time.sleep(2)`），避免触发速率限制
- 敏感操作（提交前）进行相关性检查

### 数据安全
- 结果文件保存到 `results/` 目录（已 gitignored）
- 日志文件 `wq_brain.log` 包含运行记录（已 gitignored）
- 避免在日志中打印完整密码

---

## 开发工作流

### 添加新的 Alpha 模板

在 `alpha_generator.py` 的对应模板列表中添加:

```python
AlphaTemplate(
    name="custom_alpha",
    expression="rank(close) * ts_std(volume, {window})",
    category="custom",
    description="自定义 Alpha",
    params={"window": [10, 20, 60]}
)
```

### 添加新的 Alpha 类型

1. 在 `alpha_generator.py` 添加模板初始化方法
2. 在 `alpha_submitter.py` 的 `_get_criteria_for_type()` 添加筛选标准
3. 在 `main.py` 的 CLI choices 中添加类型选项

### 修改提交标准

可通过以下方式修改:
- 命令行参数: `--min-sharpe 1.5 --min-fitness 0.8`
- 配置文件: 编辑 `config.yaml` 中的 `criteria` 部分
- 代码层面: 创建自定义 `SubmissionCriteria` 实例

---

## 常用命令速查

```bash
# 安装
uv pip install -e .

# 运行帮助
python main.py --help
python main.py simulate --help

# 生成 Alpha
python main.py generate -t regular -c 10

# 模拟 Alpha
python main.py simulate -t power_pool -c 5 -r CHN

# 提交 Alpha
python main.py submit -t all -c 5

# 代码格式化
uv run black wq_brain/ main.py

# 代码检查
uv run ruff check wq_brain/ main.py

# 运行测试（如有）
uv run pytest
```

---

## 依赖管理

项目使用 `uv` 管理依赖:

```bash
# 添加依赖
uv add package_name

# 添加开发依赖
uv add --dev package_name

# 更新锁定文件
uv lock

# 同步依赖
uv sync
```

依赖定义在 `pyproject.toml`:
```toml
[project]
dependencies = [
    "requests>=2.31.0",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0.1",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0.0",
    "black>=24.0.0",
    "ruff>=0.3.0",
]
```

---

## 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| 认证失败 | 用户名/密码错误 | 检查 `.env` 文件中的凭证 |
| 模拟超时 | API 响应慢 | 增加 `max_wait` 参数或检查网络 |
| 相关性检查失败 | 与现有 Alpha 过于相似 | 调整模板参数或跳过相关性检查 |
| 提交被拒绝 | 不符合提交标准 | 检查 Sharpe/Fitness/Turnover 指标 |
| 导入错误 | 依赖未安装 | 运行 `uv pip install -e .` |

---

## 外部资源

- **WorldQuant Brain 平台**: https://platform.worldquantbrain.com
- **API 基础地址**: `https://api.worldquantbrain.com`
- **101 Formulaic Alphas**: 论文参考（已实现部分变体）

---

*文档版本: 0.1.0 | 最后更新: 2026-01-31*
