# 项目结构

```
wq/
├── wq_brain/                   # 核心模块
│   ├── __init__.py            # 模块初始化
│   ├── client.py              # WorldQuant Brain API 客户端
│   ├── alpha_generator.py     # Alpha 表达式生成器
│   ├── alpha_submitter.py     # Alpha 提交和管理
│   ├── strategy.py            # 策略配置和执行
│   └── learning.py            # 学习系统（数据库、分析、智能生成）
│
├── docs/                       # 文档
│   ├── README.md              # 文档入口
│   ├── LEARNING_README.md     # 学习系统概览
│   ├── learning_system.md     # 学习系统详细指南
│   ├── learning_examples.md   # 学习系统示例
│   ├── IMPLEMENTATION_SUMMARY.md  # 实施总结
│   └── strategies/            # 策略配置示例
│       └── YYYY-MM-DD/
│           └── strategy.yaml
│
├── results/                    # 结果输出（被 git 忽略）
│   ├── README.md              # 目录说明
│   ├── .gitkeep               # 保持目录结构
│   ├── alpha_history.db       # 学习系统数据库
│   ├── submission_progress_*.json  # 提交进度记录
│   ├── report_*.txt           # 报告
│   └── archive/               # 归档的旧文件
│
├── main.py                     # 主入口脚本
├── smart_generate.py           # 学习系统 CLI
├── config.yaml                 # 配置文件
├── .env.example                # 环境变量示例
├── .env                        # 环境变量（被 git 忽略）
├── .gitignore                  # Git 忽略规则
├── pyproject.toml              # 项目配置
├── uv.lock                     # 依赖锁定
├── README.md                   # 项目说明
└── CLAUDE.md                   # Claude Code 指南
```

## 核心文件说明

### 入口脚本

- **main.py**: 主命令行工具
  - `generate`: 生成 Alpha 表达式
  - `simulate`: 模拟 Alpha
  - `submit`: 模拟并提交
  - `pending`: 提交待处理 Alpha
  - `strategy`: 按策略执行

- **smart_generate.py**: 学习系统命令行工具
  - `analyze`: 分析历史数据
  - `suggest`: 获取智能建议
  - `run`: 执行智能生成

### 核心模块

- **client.py**: API 客户端
  - 认证和会话管理
  - Alpha 模拟和提交
  - 相关性检查

- **alpha_generator.py**: 生成器
  - 基于模板生成 Alpha
  - 支持 4 种类型（atom, regular, power_pool, superalpha）
  - 参数化配置

- **alpha_submitter.py**: 提交器
  - 批量模拟和提交
  - 结果过滤
  - 自动保存到学习数据库

- **learning.py**: 学习系统
  - `AlphaDatabase`: SQLite 数据存储
  - `AlphaAnalyzer`: 统计分析
  - `SmartGenerator`: 智能生成

- **strategy.py**: 策略系统
  - YAML 配置加载
  - 策略执行

### 配置文件

- **config.yaml**: 默认配置
  - 认证信息（使用环境变量）
  - 交易设置
  - 提交标准
  - 生成数量

- **.env**: 环境变量
  - `WQB_USERNAME`: WorldQuant Brain 用户名
  - `WQB_PASSWORD`: WorldQuant Brain 密码

### 文档

- **README.md**: 项目概览和使用说明
- **CLAUDE.md**: Claude Code 开发指南
- **docs/**: 详细文档
  - 学习系统文档
  - 策略示例
  - 实施总结

## 数据流

```
1. 生成 Alpha
   ↓
2. 配置参数
   ↓
3. 模拟（API）
   ↓
4. 过滤结果
   ↓
5. 提交（可选）
   ↓
6. 保存到学习数据库
   ↓
7. 统计分析
   ↓
8. 智能生成（下一轮）
```

## 维护

### 清理缓存
```bash
# 清理 Python 缓存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

# 清理 linter 缓存
rm -rf .ruff_cache
```

### 归档结果
```bash
# 归档 30 天前的文件
cd results
find . -name "*.json" -mtime +30 -exec mv {} archive/ \;
find . -name "*.txt" -mtime +30 -exec mv {} archive/ \;
```

### 备份数据库
```bash
# 备份学习数据库
cp results/alpha_history.db backups/alpha_history_$(date +%Y%m%d).db
```

## Git 工作流

```bash
# 添加新功能
git add wq_brain/
git add main.py smart_generate.py
git add docs/

# 提交（结果文件会被自动忽略）
git commit -m "feat: add learning system"

# 推送
git push origin main
```

## 注意事项

1. **敏感信息**: `.env` 文件包含认证信息，已被 git 忽略
2. **结果文件**: `results/` 目录内容被 git 忽略，避免提交大文件
3. **数据库**: `alpha_history.db` 被 git 忽略，需要单独备份
4. **缓存**: 各种缓存目录被 git 忽略，可以安全删除
