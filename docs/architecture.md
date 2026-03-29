# WQA 架构文档

## 概览

WQA 是一个多 agent 量化研究自动化系统，围绕 WorldQuant BRAIN 平台构建。三个 agent 协作完成从信号发现到 alpha 提交的完整闭环。

```
                    ┌─────────────┐
                    │  RSS/Atom   │
                    │  (arXiv等)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Researcher │  ← LLM 生成 idea
                    │  (gemini)   │
                    └──────┬──────┘
                           │ idea 队列 (SQLite)
                    ┌──────▼──────┐
                    │  Engineer   │  ← LLM 生成表达式 → WQ 模拟
                    │  (kimi)     │
                    └──────┬──────┘
                           │ experiment 记录
                    ┌──────▼──────┐
                    │  Reviewer   │  ← 评审 + 提交 + 近似修复
                    │  (kimi)     │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  WorldQuant  │
                    │  Submission  │
                    └─────────────┘
```

## 模块结构

```
wq_brain/
├── agent_cli.py          # CLI 入口，所有 wqa 子命令
├── agent_runtime.py      # 核心：daemon、agent 循环、知识库、RuntimeStore
├── dashboard.py          # Dashboard HTTP 服务器（从 agent_runtime 拆出）
├── client.py             # WorldQuant BRAIN API 客户端
├── alpha_generator.py    # Alpha 模板生成器（历史遗留，部分功能已迁移到 agent_runtime）
├── alpha_submitter.py    # 模拟提交流程、SubmissionCriteria
├── learning.py           # 历史记录数据库、模板权重学习
└── static/               # Dashboard 静态资源
```

## 核心类

### `AgentRuntime` (agent_runtime.py)

系统的中枢。初始化时加载配置、知识库、创建 LLM provider 和 WQ client。

关键方法：

| 方法 | 作用 |
|---|---|
| `run_foreground()` | 启动 daemon 主循环，拉起三个 agent 线程 + dashboard |
| `_agent_loop(name)` | 单个 agent 的无限循环，按 interval 调度 |
| `run_researcher_cycle()` | 检查队列深度 → 抓取 RSS → LLM 生成 idea → 入队 |
| `run_engineer_cycle()` | 消费 idea → LLM 生成表达式 → WQ 模拟 → 记录结果 |
| `run_reviewer_cycle()` | Phase 1: 评审+提交 → Phase 2: 修复近似失败的实验 |
| `_criteria()` | 返回提交标准，优先使用 account-info 同步的真实 WQ 门槛 |
| `_brain_knowledge_prompt(section)` | 格式化知识库某个 section 用于 prompt 注入 |
| `_account_profile_prompt()` | 格式化账号信息用于 prompt 注入 |

### `RuntimeStore` (agent_runtime.py)

SQLite 持久层，管理所有运行时状态。

表结构：

| 表 | 用途 |
|---|---|
| `source_items` | 抓取的 RSS/论文原始条目 |
| `ideas` | 研究 idea 队列，状态流转: queued → claimed → done/failed |
| `experiments` | Alpha 实验记录，含表达式、模拟结果、提交状态 |
| `events` | 全局事件日志 |
| `agent_status` | 各 agent 最新心跳和状态 |
| `reflections` | Researcher 的反思记录（失败模式、改进方向） |

### `WorldQuantBrainClient` (client.py)

封装 BRAIN API 的 HTTP 客户端。

关键能力：
- Session 认证（cookie-based）
- 提交模拟（`POST /simulations`），轮询等待结果
- 提交 alpha（`POST /alphas/{id}/submit`），轮询 submission checks
- 获取 operators、data fields、用户信息等元数据

### `SubmissionCriteria` (alpha_submitter.py)

```python
@dataclass
class SubmissionCriteria:
    min_sharpe: float = 1.25
    min_fitness: float = 0.7
    max_turnover: float = 0.7
```

`check(result)` 判断模拟结果是否达标。默认值是 fallback，运行时会被 `account-info` 同步的真实 WQ 门槛覆盖。

### `AlphaSubmitter` (alpha_submitter.py)

批量提交流程：筛选达标实验 → 调用 WQ 模拟 → 检查 submission checks → 记录结果。

### `AlphaDatabase` / `AlphaAnalyzer` (learning.py)

历史 alpha 记录和分析。`AlphaAnalyzer` 提供模板权重学习，让 engineer 倾向于使用历史表现好的模式。

## 数据流

```
1. SourceCollector.collect()
   ↓ RSS/Atom → SourceItem 列表
2. RuntimeStore.add_source_items()
   ↓ 去重入库
3. Researcher: LLM(sources + reflection + knowledge) → ideas
   ↓ (跳过如果 queued >= max_queued_ideas)
4. RuntimeStore.add_ideas()
   ↓ 去重入队
5. Engineer: claim_ideas() → LLM(idea + knowledge) → expressions
   ↓
6. WorldQuantBrainClient.simulate() → SimulateResult
   ↓
7. RuntimeStore.create_experiment(result)
   ↓
8. Reviewer: claim_promising_experiments()
   ↓ Phase 1: check criteria → submit
   ↓ Phase 2: find near-misses → LLM refine → re-simulate
9. AlphaSubmitter.submit() → WorldQuant
```

## LLM Provider 体系

```python
BaseLLMProvider          # 抽象基类，generate(system, user) → str
├── GeminiProvider       # Google Gemini API (generateContent)
├── KimiProvider         # Moonshot Kimi (OpenAI-compatible chat/completions)
└── DisabledLLMProvider  # 无 API key 时的 fallback，返回空
```

每个 agent 可以配置不同的 `llm_profile`，在 config 的 `providers` 段定义。

## 知识库系统

知识库文件：`.wqa/brain_knowledge.yaml`

数据来源：

| Section | 来源 | 命令 |
|---|---|---|
| `account_profile` | WQ API 探测 | `wqa account-info` |
| `operators_by_category` | WQ `/operators` | `wqa sync-knowledge` |
| `top_data_fields` | WQ `/data-fields` | `wqa sync-knowledge` |
| `proven_alphas` | 手动维护 | 直接编辑 yaml |
| `platform_tips` | 手动维护 | 直接编辑 yaml |

注入点：Researcher 和 Engineer 的 system prompt 中，通过 `_brain_knowledge_prompt()` 和 `_account_profile_prompt()` 格式化后拼接。

## Reviewer 两阶段流程

**Phase 1 — 评审与提交**：
- 从 experiments 中 claim 状态为 promising 的记录
- 用 `SubmissionCriteria.check()` 判断是否达标
- 达标后先跑 WorldQuant submission check，再执行 submit
- 如果 submission check 失败或 pending，实验标记为 `blocked`，并记录平台返回原因（例如 `FAIL=PROD_CORRELATION`）
- 只有真正提交成功的实验才标记为 `submitted`

**Phase 2 — 近似修复**：
- 查找两类可修复对象：
  - sharpe >= criteria * 0.6 但因 turnover/fitness 被拒的 `rejected` 实验
  - 因 submission check 失败而被标记为 `blocked` 的实验（例如 `FAIL=PROD_CORRELATION`）
- 用 LLM 生成修复变体（加 neutralize、拉长窗口、加 trade_when 等）
- 重新模拟修复后的表达式

## Dashboard

`DashboardServer`（`dashboard.py`）在 daemon 启动时拉起一个 HTTP 服务（默认 `127.0.0.1:8765`）。

端点：
- `GET /` — HTML 页面，展示全局概览
- `GET /api/status` — JSON，包含 summary、ideas、experiments、events、feedback、reflections、config snapshot、log tail
