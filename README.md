# WQA

WQA is a lightweight multi-agent runtime for WorldQuant-style quantitative research automation.

## Background

Systematic alpha research usually breaks down into a repetitive loop:

1. Read new papers, market commentary, and research notes.
2. Turn those signals into concrete alpha ideas.
3. Implement and backtest the ideas.
4. Keep only the ideas that survive objective evaluation.
5. Record the results so the next round improves instead of starting from zero.

Most teams handle that loop with a mix of ad hoc scripts, notebooks, spreadsheets, and manual review. That works for exploration, but it becomes hard to scale, hard to observe, and hard to iterate consistently.

WQA packages that workflow into a small agent system with one operational entrypoint: `wqa`.

## What The Project Does

WQA runs three cooperating agents:

- `researcher`: collects fresh external inputs such as papers or feeds, then converts them into actionable alpha ideas.
- `engineer`: consumes queued ideas, generates candidate alpha expressions, and sends them through WorldQuant simulation.
- `reviewer`: evaluates promising candidates, submits acceptable alphas to WorldQuant, and can notify through Telegram.

The system also keeps a lightweight feedback loop:

- research ideas are stored in a queue
- experiments and backtest results are recorded
- accepted and rejected attempts remain visible
- historical performance influences later idea implementation

## Core Features

- Multi-agent workflow for research, implementation, and review
- Single command-line entrypoint: `wqa`
- Background daemon mode for continuous execution
- Built-in local dashboard for queue, experiment, agent monitoring, charts, researcher reflection traces, masked config snapshot, and recent log tail
- Configurable LLM backends per agent
- WorldQuant simulation and submission integration
- Telegram notification support
- Experiment persistence with SQLite
- Basic learning loop from previous alpha outcomes

## Architecture

The runtime is intentionally small and centered on a few modules:

- `wq_brain/agent_cli.py`: command-line entrypoint
- `wq_brain/agent_runtime.py`: daemon, queue orchestration, and agent loops
- `wq_brain/dashboard.py`: local dashboard HTTP server
- `wq_brain/client.py`: WorldQuant API client
- `wq_brain/alpha_generator.py`: alpha candidate generation
- `wq_brain/alpha_submitter.py`: simulation and submission workflow
- `wq_brain/learning.py`: lightweight result history and template weighting

## Operational Model

After startup, WQA:

1. loads runtime configuration from `.wqa/config.yaml`
2. starts a background daemon
3. launches a local dashboard
4. runs the researcher, engineer, and reviewer loops continuously
5. stores state in `.wqa/`

The local dashboard is available by default at:

[http://127.0.0.1:8765](http://127.0.0.1:8765)

## Quick Start

Install the project:

```bash
uv pip install -e .
```

Initialize configuration:

```bash
uv run wqa init
```

Then edit [`.wqa/config.yaml`](/Users/jiaqianjing/workspace/quant/wq/.wqa/config.yaml) and provide:

- WorldQuant credentials
- at least one LLM provider configuration
- optional Telegram bot settings

Run the system:

```bash
uv run wqa start
uv run wqa status
```

Stop or restart it:

```bash
uv run wqa stop
uv run wqa restart
```

## Knowledge Base

WQA maintains a local knowledge base (`.wqa/brain_knowledge.yaml`) that feeds platform-specific context into every agent prompt — proven alpha patterns, operator definitions, popular data fields, account permissions, and practical tips.

### Account Info

Probe your WorldQuant account to discover permissions, real submission thresholds, and available regions/delays:

```bash
uv run wqa account-info
```

This detects:

- Genius level and onboarding status
- SUPER alpha permission
- Available regions (USA, CHN, EUR, ASI)
- Available delays per region (0, 1)
- Real platform submission checks (Sharpe, Fitness, Turnover limits, etc.)

The results are saved to `brain_knowledge.yaml` as `account_profile` and automatically used by:

- `_criteria()` — overrides config thresholds with real WQ limits
- Researcher and Engineer prompts — injected as context so LLM knows account constraints

Run after account changes (e.g. level upgrade) and restart the daemon:

```bash
uv run wqa account-info
uv run wqa restart
```

### Syncing from WorldQuant BRAIN

Pull the latest operators and data fields directly from the BRAIN API:

```bash
uv run wqa sync-knowledge
```

This authenticates with your WorldQuant credentials and fetches:

- **All platform operators** (84+) with definitions, grouped by category (Arithmetic, Time Series, Cross Sectional, Group, etc.)
- **Top 200 data fields** ranked by community usage (`alphaCount`), covering fundamental, model, sentiment, news, and options categories

The results are saved to:

| File | Contents |
|---|---|
| `.wqa/brain_knowledge.yaml` | Merged knowledge base used by agents |
| `.wqa/brain_operators.json` | Raw operator reference |
| `.wqa/brain_datafields.json` | Raw data field reference |

Run `sync-knowledge` periodically (e.g. weekly) to pick up newly added fields, then restart the daemon:

```bash
uv run wqa sync-knowledge
uv run wqa restart
```

### Manual curation

You can also edit `.wqa/brain_knowledge.yaml` directly to add:

- `proven_alphas`: expressions with known good performance to use as few-shot examples
- `platform_tips`: turnover control, expression quality, settings optimization advice

The sync command merges API data into the file without overwriting manually curated sections.

## Why It Exists

The goal of WQA is not to replace quantitative judgment. The goal is to make the research loop more structured, more observable, and easier to repeat:

- fewer one-off scripts
- clearer ownership between research and execution
- faster iteration on ideas
- a persistent record of what worked and what failed

## Repository Contents

- [`docs/architecture.md`](/Users/jiaqianjing/workspace/quant/wq/docs/architecture.md): system architecture, class overview, data flow diagrams
- [`docs/configuration.md`](/Users/jiaqianjing/workspace/quant/wq/docs/configuration.md): complete config reference with all fields explained
- [`docs/cli.md`](/Users/jiaqianjing/workspace/quant/wq/docs/cli.md): CLI command reference and typical workflows
- [`docs/development.md`](/Users/jiaqianjing/workspace/quant/wq/docs/development.md): development guide, extension points, WQ API notes
- [`docs/agent_lab.md`](/Users/jiaqianjing/workspace/quant/wq/docs/agent_lab.md): operational notes
- [`tests/test_wqa_runtime.py`](/Users/jiaqianjing/workspace/quant/wq/tests/test_wqa_runtime.py): runtime smoke tests
- [`results/README.md`](/Users/jiaqianjing/workspace/quant/wq/results/README.md): notes about generated runtime artifacts
