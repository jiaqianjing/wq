# CLAUDE.md

This repository has a single user-facing workflow: `wqa`.

## Project

`wqa` is a multi-agent runtime for WorldQuant BRAIN alpha research automation:

- `researcher` collects RSS/Atom feeds, uses LLM to generate alpha ideas with reflection from past failures
- `engineer` turns ideas into FASTEXPR alpha expressions, runs WQ simulations
- `reviewer` Phase 1: submits passing alphas; Phase 2: LLM-refines near-miss experiments

## Commands

```bash
uv pip install -e .
uv run wqa init
uv run wqa start / stop / restart / status
uv run wqa account-info       # probe WQ account permissions and real submission thresholds
uv run wqa sync-knowledge     # pull operators and data fields from WQ API
uv run python -m pytest tests/ -q
```

## Important Files

- `wq_brain/agent_cli.py` — CLI entrypoint, all subcommands
- `wq_brain/agent_runtime.py` — daemon, agent loops, RuntimeStore, dashboard, knowledge base, LLM providers
- `wq_brain/client.py` — WorldQuant BRAIN API client (session auth, simulate, submit)
- `wq_brain/alpha_submitter.py` — submission orchestration, SubmissionCriteria
- `wq_brain/alpha_generator.py` — alpha template generator
- `wq_brain/learning.py` — history database and template weighting

## Documentation

- `docs/architecture.md` — system architecture, class overview, data flow
- `docs/configuration.md` — config reference, all fields explained
- `docs/cli.md` — CLI command reference, typical workflows
- `docs/development.md` — dev guide, extension points, WQ API notes
- `docs/agent_lab.md` — operational notes (Chinese)

## Key Design Points

- Submission thresholds come from `wqa account-info` (real WQ checks), not from config
- Knowledge base (`.wqa/brain_knowledge.yaml`) is injected into all agent prompts
- RuntimeStore uses SQLite for ideas, experiments, events, agent status, reflections
- Each agent can use a different LLM provider (configured via `llm_profile`)

## Working Style

- Prefer simplifying over preserving old flows
- Do not reintroduce `main.py`, `smart_generate.py`, or compatibility commands
- Keep the project centered on `wqa`
