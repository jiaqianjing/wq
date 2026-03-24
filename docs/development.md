# WQA 开发指南

## 项目约定

- 保持简洁，不引入不必要的抽象
- 不恢复已删除的旧模块（`main.py`、`smart_generate.py`）
- 所有功能围绕 `wqa` 命令展开
- 提交门槛从平台同步，不硬编码在 config 中

## 本地开发

```bash
uv pip install -e .
uv run python -m pytest tests/ -q
```

## 测试

```bash
uv run python -m pytest tests/test_wqa_runtime.py -q
```

测试覆盖：RuntimeStore CRUD、idea 去重、experiment 状态流转、config 加载。测试使用内存 SQLite，不依赖外部服务。

## 添加新 Agent

1. 在 `AgentRuntime` 中添加 `run_<name>_cycle()` 方法
2. 在 `_agent_loop()` 的调度逻辑中注册
3. 在 config 模板的 `agents` 段添加配置
4. 在 `_init_db()` 中添加需要的表（如果有）

## 添加新 LLM Provider

1. 继承 `BaseLLMProvider`，实现 `generate(system_prompt, user_prompt) -> str`
2. 在 `create_llm_provider()` 中添加 `elif provider == "your_name"` 分支
3. 在 config 的 `providers` 段添加配置

## 添加新知识库 Section

1. 在 `sync_brain_knowledge()` 或 `sync_account_info()` 中获取数据
2. 写入 `brain_knowledge.yaml` 的新 key
3. 在 `_brain_knowledge_prompt()` 中已有通用格式化逻辑（list/dict 自动处理）
4. 在 agent prompt 中通过 `self._brain_knowledge_prompt("your_section")` 注入

## 关键设计决策

### 为什么 agent_runtime.py 这么大？

有意为之。所有 agent 逻辑、存储、dashboard 集中在一个文件中，减少跨文件跳转。当文件增长到难以维护时再拆分。

### 为什么不用 config 配置提交门槛？

WQ 平台的门槛会随账号等级变化（GOLD/PLATINUM 不同），手动配置容易过时。`account-info` 从 API 直接获取真实 checks，保证一致性。

### 为什么 Reviewer 有两个阶段？

Phase 1 处理明确达标的实验。Phase 2 处理"差一点"的实验（sharpe 够但 turnover 超标等），用 LLM 生成修复变体重新模拟，提高整体通过率。

### 为什么 Researcher 有反思机制？

每轮 researcher 会回顾上一轮的失败模式（哪些 idea 类型总是失败、哪些 motif 已经被丢弃），注入到下一轮 prompt 中，避免重复犯错。

## WorldQuant API 备忘

| 端点 | 方法 | 用途 |
|---|---|---|
| `/authentication` | POST | Session 认证 |
| `/simulations` | POST | 提交模拟 |
| `/simulations/{id}` | GET | 轮询模拟状态 |
| `/alphas/{id}/submit` | POST | 提交 alpha |
| `/users/self` | GET | 用户信息 |
| `/users/self/alphas` | GET | Alpha 列表（含 checks） |
| `/users/self/consultant` | GET | Consultant 统计 |
| `/operators` | GET | 全部 operators |
| `/data-fields` | GET | Data fields（支持 category/region/delay 过滤） |

### 注意事项

- 认证是 cookie-based session，不是 token
- 模拟是异步的，需要轮询 `simulations/{id}` 等待完成
- `data-fields` 的 delay=1 可能返回 `{"message": ...}` 而非 `{"count": ...}`，需要兼容处理
- SUPER 类型模拟需要额外权限，大部分账号不可用
- CHN 区域对部分账号不可用（返回 0 fields）
