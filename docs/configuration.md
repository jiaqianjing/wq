# WQA 配置参考

## 配置文件

路径：`.wqa/config.yaml`

通过 `wqa init` 生成模板，支持 `${ENV_VAR}` 环境变量展开。

## 完整配置结构

```yaml
app:
  state_dir: ./.wqa                # 运行时数据目录
  dashboard_host: 127.0.0.1        # Dashboard 监听地址
  dashboard_port: 8765             # Dashboard 端口
  loop_sleep_seconds: 10           # Agent 循环基础间隔
  default_claim_limit: 3           # 每次 claim 的默认数量

providers:
  gemini:
    provider: gemini
    model_name: gemini-2.5-pro     # Gemini 模型名
    api_key: ${GEMINI_API_KEY}
  kimi:
    provider: kimi
    model_name: moonshot-v1-8k     # Kimi 模型名
    api_key: ${KIMI_API_KEY}
    base_url: https://api.moonshot.cn/v1  # OpenAI-compatible 端点
  siliconflow:
    provider: siliconflow
    model_name: deepseek-ai/DeepSeek-V3  # SiliconFlow 模型名
    api_key: ${SILICONFLOW_API_KEY}
    base_url: https://api.siliconflow.cn/v1  # OpenAI-compatible 端点
  anthropic:
    provider: anthropic
    model_name: claude-opus-4-20250514  # Anthropic 建议使用显式快照模型名
    api_key: ${ANTHROPIC_API_KEY}
    base_url: https://api.anthropic.com

agents:
  researcher:
    enabled: true
    interval_seconds: 900          # 每 15 分钟跑一轮
    llm_profile: gemini            # 引用 providers 中的 key
    idea_batch_size: 4             # 每轮生成几个 idea
    max_queued_ideas: 20           # 队列上限，达到后 researcher 暂停生产
  engineer:
    enabled: true
    interval_seconds: 300          # 每 5 分钟跑一轮
    llm_profile: kimi
    alpha_batch_size: 4            # 每轮生成几个表达式
  reviewer:
    enabled: true
    interval_seconds: 180          # 每 3 分钟跑一轮
    llm_profile: kimi

integrations:
  worldquant:
    username: ${WQB_USERNAME}
    password: ${WQB_PASSWORD}
    disable_proxy: true            # 忽略系统代理，排查 ProxyError / Read timed out 时可开启
    region: USA                    # 默认区域
    universe: TOP3000              # 默认 universe
    auto_submit: true              # 达标后自动提交
  telegram:
    enabled: false
    bot_token: ${TG_BOT_TOKEN}
    chat_id: ${TG_CHAT_ID}

sources:
  papers:
    - name: arxiv-qfin
      kind: atom                   # atom | rss
      url: https://export.arxiv.org/api/query?search_query=cat:q-fin.ST&...
      timeout_seconds: 15          # 单次请求超时
      user_agent: wqa-source-collector/1.0  # 自定义 UA，建议对外部源显式声明
      honor_retry_after: true      # 429 时优先遵守服务端 Retry-After
      rate_limit_cooldown_seconds: 1800  # 429 且无 Retry-After 时的默认冷却
  reports: []
  market: []
```

## 提交门槛

提交门槛不在 config 中配置。运行 `wqa account-info` 后，系统从 WQ 平台获取真实门槛并保存到 `brain_knowledge.yaml`。

当前你的账号（GOLD Consultant）的真实门槛：

| Check | 值 |
|---|---|
| LOW_SHARPE | ≥ 1.58 |
| LOW_FITNESS | ≥ 1.0 |
| HIGH_TURNOVER | ≤ 0.7 |
| LOW_TURNOVER | ≥ 0.01 |
| LOW_SUB_UNIVERSE_SHARPE | ≥ -0.07 |
| LOW_2Y_SHARPE | ≥ 1.58 |

如果没有运行过 `account-info`，系统使用保守默认值（Sharpe ≥ 1.25, Fitness ≥ 0.7, Turnover ≤ 0.7）。

## 环境变量

通过 shell 环境变量传入，系统在 `config.yaml` 中通过 `${VAR}` 语法自动展开：

```bash
GEMINI_API_KEY=your_key
KIMI_API_KEY=your_key
SILICONFLOW_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
WQB_USERNAME=your_email
WQB_PASSWORD=your_password
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
```

## 添加新的 LLM Provider

在 `providers` 段添加新条目，`provider` 字段决定使用哪个 Provider 类：

- `gemini` → `GeminiProvider`（Google Gemini generateContent API）
- `kimi` → `KimiProvider`（OpenAI-compatible chat/completions，需要 `base_url`）
- `siliconflow` → `SiliconFlowProvider`（OpenAI-compatible chat/completions，默认 `https://api.siliconflow.cn/v1`）
- `anthropic` → `AnthropicProvider`（Anthropic Messages API，建议配置显式快照模型名）

然后在 `agents` 段的 `llm_profile` 引用新 key 即可。

## 添加新的数据源

在 `sources.papers` 列表中添加：

```yaml
sources:
  papers:
    - name: my-source
      kind: atom          # atom 或 rss
      url: https://...
      timeout_seconds: 15
      user_agent: wqa-source-collector/1.0
      honor_retry_after: true
      rate_limit_cooldown_seconds: 1800
```

`SourceCollector` 会在每个 researcher 周期自动抓取所有配置的源。

### Source 限流与冷却

- 所有 `papers` / `reports` / `market` 源共享同一套 429 处理逻辑。
- 如果某个源返回 `429 Too Many Requests`：
  - 优先读取响应头里的 `Retry-After`
  - 如果没有 `Retry-After`，回退到 `rate_limit_cooldown_seconds`
  - 冷却期间后续 researcher 周期会直接跳过该源，不会每轮重复请求
- `honor_retry_after: false` 时，系统忽略服务端的 `Retry-After`，固定使用 `rate_limit_cooldown_seconds`
- `user_agent` 允许你为外部源声明明确的请求标识；未配置时默认使用 `wqa-source-collector/1.0`
- 冷却状态保存在运行进程内存中；如果 runtime 重启，冷却状态会重新开始计算
