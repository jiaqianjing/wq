# WQA Agent Lab

`wqa` 是新的统一管理命令，用于启动一个多 agent 的量化研究运行时。

## 设计目标

- `researcher` agent 自动拉取最新论文、研报、市场 RSS/Atom 源，结合历史回测结果生成 idea 队列。
- `engineer` agent 消费 idea，生成 Alpha 表达式并调用现有 WorldQuant 模拟流程做回测。
- `reviewer` agent 验收 promising alpha，在满足条件时自动提交到 WorldQuant，并通过 Telegram 发送通知。
- 后台自动启动 dashboard，展示 ideas、experiments、agent heartbeat 和事件日志。

## 命令

```bash
uv run wqa init
uv run wqa start
uv run wqa status
uv run wqa stop
uv run wqa restart
```

## 初始化

```bash
uv run wqa init
```

会生成 [`.wqa/config.yaml`](/Users/jiaqianjing/workspace/quant/wq/.wqa/config.yaml) 模板。你需要按需填入：

- `integrations.worldquant.username`
- `integrations.worldquant.password`
- `integrations.telegram.bot_token`
- `integrations.telegram.chat_id`
- `providers.gemini.model_name`
- `providers.gemini.api_key`
- `providers.kimi.model_name`
- `providers.kimi.api_key`

## 运行时文件

默认会落到 `.wqa/` 目录：

- `runtime.db`: idea / experiment / event / agent 状态数据库
- `alpha_history.db`: 复用现有学习系统的历史记录
- `submission_checks.jsonl`: WorldQuant submission 检查日志
- `logs/wqa.out.log`
- `logs/wqa.err.log`

## Dashboard

启动后会自动起一个本地 HTTP 服务，默认地址：

```text
http://127.0.0.1:8765
```

页面包含：

- 任务总览
- agent 状态
- idea 队列
- experiment 列表
- event log

## 当前实现边界

- Researcher 的数据抓取现在支持通用 RSS/Atom 和 arXiv Atom API。
- Gemini 通过 Google Gemini `generateContent` 接口调用。
- Kimi 通过 OpenAI-compatible `chat/completions` 方式调用，默认 `base_url` 为 `https://api.moonshot.cn/v1`。
- 如果缺少 LLM 或 WorldQuant 配置，系统会自动降级到本地 fallback 流程，并将实验标记为 `blocked` 或使用启发式 ideas。
