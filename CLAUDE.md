# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a WorldQuant Brain Alpha automation system that generates, simulates, and submits quantitative trading alpha factors to the WorldQuant Brain platform. The system supports four alpha types (ATOMs, Regular, Power Pool, SuperAlphas) with automated backtesting, filtering, and submission workflows.

## Commands

### Setup
```bash
# Install dependencies
pip install -e .
# or with uv
uv pip install -e .

# Configure credentials
cp .env.example .env
# Edit .env with your WorldQuant Brain credentials
```

### Main Commands
```bash
# Generate alpha expressions only (no simulation)
python main.py generate -t regular -c 10
python main.py generate -t 101 -c 20 -o alphas.json

# Simulate alphas without submission
python main.py simulate -t regular -c 10
python main.py simulate -t power_pool -c 5 -r CHN

# Simulate and submit alphas
python main.py submit -t regular -c 5
python main.py submit -t all -c 5 --min-sharpe 1.5 --min-fitness 0.8

# Submit pending alphas (previously simulated but not submitted)
python main.py pending

# Execute strategy from YAML config
python main.py strategy -f docs/strategies/2026-02-06/strategy.yaml --templates alphas.json --run --report report.txt

# Learning System (NEW)
python smart_generate.py analyze                    # Analyze historical data
python smart_generate.py suggest -t regular -c 10   # Get smart suggestions
python smart_generate.py run -t regular -c 10       # Smart generation based on history
```

### Testing
```bash
# Run tests (if available)
pytest

# Code formatting
black .

# Linting
ruff check .
```

## Architecture

### Core Modules (`wq_brain/`)

**`client.py`** - WorldQuant Brain API client
- Handles authentication with JWT token management and auto-refresh
- Implements retry logic for expired credentials (see `_should_retry_auth` and `_request` methods)
- Key classes:
  - `WorldQuantBrainClient`: Main API client with methods for simulate, submit, check correlation
  - `AlphaConfig`: Configuration for alpha parameters (region, universe, delay, neutralization, etc.)
  - `SimulateResult`: Dataclass containing simulation metrics (sharpe, fitness, turnover, drawdown)
- Important: Simulation uses progress polling via `_wait_for_simulation_progress` with configurable timeouts and retry logic

**`alpha_generator.py`** - Alpha expression generator
- Based on WorldQuant "101 Formulaic Alphas" paper principles
- Generates four types of alphas with different complexity levels:
  - ATOMs: Simple 1-2 field expressions
  - Regular: Standard technical indicators
  - Power Pool: Complex combinations with advanced indicators
  - SuperAlphas: Multi-factor combinations
- Uses template-based generation with parameter substitution
- Key method: `generate_all_types()` returns dict of `{alpha_type: [alpha_dicts]}`

**`alpha_submitter.py`** - Submission orchestration
- Manages simulation, filtering, and submission workflow
- `SubmissionCriteria`: Configurable thresholds for sharpe, fitness, turnover, drawdown
- `AlphaSettings`: Encapsulates alpha configuration (delay, decay, neutralization, etc.) for strategy decoupling
- Key methods:
  - `simulate_and_submit()`: Main workflow for batch processing
  - `batch_submit_by_type()`: Handles multiple alpha types with type-specific criteria
  - `generate_report()`: Creates summary of submission results
- Maintains `submission_history` and saves results to `results/` directory

**`strategy.py`** - Strategy configuration and execution
- `StrategySpec`: Dataclass for strategy parameters loaded from YAML
- Decouples strategy configuration from execution logic
- Functions:
  - `load_strategy()`: Loads YAML strategy config
  - `build_alphas_by_type()`: Generates alphas based on strategy spec
  - `run_strategy()`: Executes full simulation/submission workflow

**`learning.py`** - Learning system (NEW)
- Implements feedback loop: simulation results → statistical analysis → improved generation
- Key classes:
  - `AlphaDatabase`: SQLite storage for all simulation results with indexed queries
  - `AlphaRecord`: Complete record of alpha (expression, template, params, metrics, config)
  - `AlphaAnalyzer`: Statistical analysis (template success rates, parameter distributions, top performers)
  - `SmartGenerator`: Intelligent generation based on historical patterns
- Strategy: 70% exploit (high success templates), 20% balanced, 10% explore
- Weight calculation: success_rate + sample_bonus + performance_bonus
- Automatically enabled in `AlphaSubmitter` when available

### Entry Point

**`main.py`** - CLI interface
- Commands: `generate`, `simulate`, `submit`, `pending`, `strategy`
- Loads credentials from `.env` via `python-dotenv`
- Handles command-line argument parsing and delegates to appropriate functions

**`smart_generate.py`** - Learning system CLI (NEW)
- Commands: `analyze`, `suggest`, `run`
- `analyze`: Generate statistical reports on historical data
- `suggest`: Get smart recommendations for next batch
- `run`: Execute intelligent generation based on learned patterns
- Integrates with `AlphaSubmitter` to automatically save results to learning database

### Configuration

**`config.yaml`** - Default configuration
- Authentication (uses environment variable substitution: `${WQB_USERNAME}`)
- Trading settings (region, universe, delay)
- Submission criteria per alpha type
- Generation counts and other settings

**`.env`** - Credentials (not committed)
- `WQB_USERNAME`: WorldQuant Brain username
- `WQB_PASSWORD`: WorldQuant Brain password
- Optional proxy settings

### Data Flow

1. **Generation**: `AlphaGenerator` creates alpha expressions from templates
2. **Configuration**: Expressions wrapped in `AlphaConfig` with trading parameters
3. **Simulation**: `WorldQuantBrainClient.simulate_alpha()` submits to API and polls for results
4. **Filtering**: `SubmissionCriteria.check()` validates simulation metrics
5. **Submission**: Passing alphas submitted via `WorldQuantBrainClient.submit_alpha()`
6. **Recording**: Results saved to `results/` as JSON with `SubmissionRecord` objects
7. **Learning** (NEW): Results automatically saved to `AlphaDatabase` for analysis
8. **Analysis** (NEW): `AlphaAnalyzer` computes statistics on templates, categories, parameters
9. **Smart Generation** (NEW): `SmartGenerator` uses statistics to guide next batch

### Important Implementation Details

**Authentication Flow**:
- Initial auth via `authenticate()` returns JWT token with 23-hour expiry
- `_ensure_authenticated()` checks expiry and refreshes if needed
- `_request()` wrapper automatically retries on 401/403 with re-authentication
- Recent fix (commit 7b2d10d) improved retry logic for expired credentials

**Simulation Polling**:
- API returns 201 with `Location` header pointing to progress URL
- `_wait_for_simulation_progress()` polls with configurable timeout (default 120s)
- On timeout, retries once with 60s timeout before giving up
- Checks `Retry-After` header for optimal polling interval
- Once complete, fetches full alpha result via `_get_alpha_result()`

**Alpha Types & Criteria**:
Each alpha type has different submission thresholds (see `config.yaml`):
- ATOMs: sharpe≥1.0, fitness≥0.6, turnover≤0.8
- Regular: sharpe≥1.25, fitness≥0.7, turnover≤0.7
- Power Pool: sharpe≥1.5, fitness≥0.8, turnover≤0.6
- SuperAlphas: sharpe≥1.75, fitness≥0.85, turnover≤0.5

**Strategy System**:
- YAML configs in `docs/strategies/YYYY-MM-DD/strategy.yaml`
- Allows reproducible batch runs with documented parameters
- Supports diversification, correlation checking, and custom alpha settings
- Used for tracking experiments and iterating on generation strategies

**Learning System** (NEW):
- SQLite database at `results/alpha_history.db` stores all simulation results
- Automatic data collection: every simulation saves expression, template, params, metrics
- Statistical analysis identifies successful patterns:
  - Template success rates (which templates work best)
  - Parameter distributions (which parameter values perform well)
  - Category performance (momentum vs mean reversion vs volume, etc.)
- Smart generation strategy:
  - 70% exploit: use high-success templates with optimal parameters
  - 20% balanced: explore medium-success templates
  - 10% explore: random exploration to discover new patterns
- Weight calculation considers success rate, sample size, and average performance
- Iterative improvement: each batch improves based on previous results
- See `docs/learning_system.md` for detailed usage guide

## Documentation

**`docs/`** - Strategy notes and retrospectives
- `docs/README.md`: Entry point for documentation
- `docs/strategies/YYYY-MM-DD/`: Date-stamped strategy runs with YAML configs and notes
- Used to accumulate experience and refine generation strategies

**`results/`** - Simulation and submission results
- JSON files with timestamp-based naming
- Contains `SubmissionRecord` objects with full simulation metrics
- Used for analysis and debugging

## Key Constraints

- **API Rate Limits**: WorldQuant Brain API has rate limits; avoid excessive requests
- **Correlation Checking**: System checks alpha correlation to avoid submitting highly correlated alphas (default max: 0.7)
- **Simulation Timeouts**: Default 120s with 60s retry; very complex alphas may timeout
- **Region Support**: USA, CHN, EUR, JPN, TWN, KOR, GBR, DEU
- **Universe Options**: TOP100, TOP200, TOP500, TOP1000, TOP2000, TOP3000

## Development Notes

- Python 3.10+ required
- Dependencies: requests, python-dotenv, pyyaml
- Dev dependencies: pytest, black, ruff
- Logs written to `wq_brain.log` and stdout
- All API interactions go through `WorldQuantBrainClient._request()` for consistent auth handling
