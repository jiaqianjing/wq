# CLAUDE.md

This repository has a single user-facing workflow: `wqa`.

## Project

`wqa` is a small multi-agent runtime for WorldQuant research automation:

- `researcher` collects fresh feeds and creates ideas
- `engineer` turns ideas into alpha candidates and runs simulations
- `reviewer` accepts promising results, submits to WorldQuant, and notifies via Telegram

## Commands

```bash
uv pip install -e .
uv run wqa init
uv run wqa start
uv run wqa status
uv run wqa stop
uv run wqa restart
uv run python -m pytest tests/test_wqa_runtime.py
```

## Important Files

- `wq_brain/agent_cli.py`: single CLI entrypoint
- `wq_brain/agent_runtime.py`: daemon, queue, dashboard, agent loops
- `wq_brain/client.py`: WorldQuant API client
- `wq_brain/alpha_generator.py`: alpha template generator
- `wq_brain/alpha_submitter.py`: simulation and submission orchestration
- `wq_brain/learning.py`: minimal learning store and template weighting
- `docs/agent_lab.md`: operational notes

## Working Style

- Prefer simplifying over preserving old flows.
- Do not reintroduce `main.py`, `smart_generate.py`, or compatibility commands.
- Keep the project centered on `wqa`.
