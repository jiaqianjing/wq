# WQA

`wqa` 是这个项目唯一保留的用户入口。

它会启动一个尽量简单的多 agent 量化研究运行时：

- `researcher` 抓最新论文 / 研报 / 市场 feed，结合历史回测结果生成 ideas
- `engineer` 消费 ideas，生成 alpha，并调用 WorldQuant 做模拟回测
- `reviewer` 验收 promising alpha，提交到 WorldQuant，并通过 Telegram 发送通知
- 后台自动启动本地 dashboard，展示 ideas、experiments、feedback 和 agent 状态

## 安装

```bash
uv pip install -e .
```

## 唯一命令

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

这会生成 [`.wqa/config.yaml`](/Users/jiaqianjing/workspace/quant/wq/.wqa/config.yaml)。

你只需要关心这些配置：

- `integrations.worldquant.username`
- `integrations.worldquant.password`
- `providers.gemini.model_name`
- `providers.gemini.api_key`
- `providers.kimi.model_name`
- `providers.kimi.api_key`
- `integrations.telegram.bot_token`
- `integrations.telegram.chat_id`

## 运行后会得到什么

- 一个后台 daemon
- 一个本地 dashboard，默认地址是 `http://127.0.0.1:8765`
- 一个运行目录 `.wqa/`

`.wqa/` 里主要有：

- `runtime.db`: ideas / experiments / events / agent heartbeat
- `alpha_history.db`: 回测学习记录
- `submission_checks.jsonl`: submission 检查日志
- `logs/`: daemon 日志

## 项目结构

```text
wq_brain/
  agent_cli.py
  agent_runtime.py
  client.py
  alpha_generator.py
  alpha_submitter.py
  learning.py
docs/
  agent_lab.md
tests/
  test_wqa_runtime.py
```

## 说明

- 不再保留旧的 `main.py` / `smart_generate.py` / `wq-brain` 兼容入口
- 如果没有配置 LLM 或 WorldQuant 凭证，系统会自动降级到 fallback 流程，并把无法执行的实验标成 `blocked`
- Idea 入队会按 `title + source_url` 去重，避免重复 source 让队列失控
