from pathlib import Path

from wq_brain.alpha_generator import AlphaGenerator
from wq_brain.agent_runtime import (
    AnthropicProvider,
    RuntimeStore,
    SiliconFlowProvider,
    create_llm_provider,
    describe_llm_profile,
    extract_json,
    init_runtime_config,
    normalize_fastexpr_operators,
    read_config_snapshot,
    read_log_tail,
    runtime_status,
)


def test_init_runtime_config_uses_parent_directory(tmp_path: Path) -> None:
    config_path = tmp_path / ".wqa-smoke" / "config.yaml"
    init_runtime_config(config_path, force=True)
    text = config_path.read_text(encoding="utf-8")
    assert "state_dir: ./.wqa-smoke" in text
    assert "model_name: claude-opus-4-20250514" in text


def test_runtime_store_deduplicates_ideas(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    payload = [
        {
            "title": "idea-a",
            "summary": "summary",
            "rationale": "why",
            "source_url": "https://example.com/a",
            "status": "queued",
        },
        {
            "title": "idea-a",
            "summary": "summary",
            "rationale": "why",
            "source_url": "https://example.com/a",
            "status": "queued",
        },
    ]
    assert store.add_ideas(payload) == 1
    assert len(store.list_recent_ideas()) == 1


def test_runtime_status_reports_paths(tmp_path: Path) -> None:
    config_path = tmp_path / ".wqa-smoke" / "config.yaml"
    init_runtime_config(config_path, force=True)
    status = runtime_status(config_path)
    assert status["config_path"].endswith("config.yaml")
    assert status["state_dir"].endswith(".wqa-smoke")
    assert "summary" in status


def test_read_config_snapshot_redacts_sensitive_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
providers:
  gemini:
    api_key: super-secret-key
integrations:
  worldquant:
    username: demo-user
    password: demo-password
  telegram:
    bot_token: telegram-secret
    chat_id: "123456"
        """.strip(),
        encoding="utf-8",
    )
    snapshot = read_config_snapshot(config_path)
    assert snapshot["providers"]["gemini"]["api_key"] == "su***ey"
    assert snapshot["integrations"]["worldquant"]["username"] == "demo-user"
    assert snapshot["integrations"]["worldquant"]["password"] == "de***rd"
    assert snapshot["integrations"]["telegram"]["bot_token"] == "te***et"
    assert snapshot["integrations"]["telegram"]["chat_id"] == "***"


def test_read_log_tail_returns_recent_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "wqa.err.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert read_log_tail(log_path, lines=2) == "two\nthree"


def test_runtime_store_lists_recent_reflections(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    store.add_event(
        level="info",
        kind="researcher_reflection",
        message="reflection headline",
        payload={
            "improvement_directions": ["reduce turnover"],
            "discarded_motifs": [{"motif": "mean_reversion", "reason": "weak Sharpe"}],
            "adjustment_map": [{"observation": "weak Sharpe", "response": "simplify expression"}],
            "queued_idea_lineage": [{"idea_title": "idea-a", "parent_alpha": "mean_reversion"}],
        },
    )
    reflections = store.list_recent_reflections()
    assert len(reflections) == 1
    assert reflections[0]["message"] == "reflection headline"
    assert reflections[0]["payload"]["improvement_directions"] == ["reduce turnover"]
    assert reflections[0]["payload"]["discarded_motifs"][0]["motif"] == "mean_reversion"
    assert reflections[0]["payload"]["queued_idea_lineage"][0]["idea_title"] == "idea-a"


def test_create_llm_provider_supports_siliconflow() -> None:
    provider = create_llm_provider(
        {
            "providers": {
                "sf": {
                    "provider": "siliconflow",
                    "model_name": "deepseek-ai/DeepSeek-V3",
                    "api_key": "demo-key",
                }
            }
        },
        "sf",
    )
    assert isinstance(provider, SiliconFlowProvider)


def test_describe_llm_profile_reports_siliconflow_model() -> None:
    config = {
        "providers": {
            "sf": {
                "provider": "siliconflow",
                "model_name": "moonshotai/Kimi-K2-Thinking",
                "api_key": "demo-key",
                "base_url": "https://api.siliconflow.cn/v1",
            }
        }
    }
    provider = create_llm_provider(config, "sf")
    info = describe_llm_profile(config, "sf", provider)
    assert info == {
        "profile": "sf",
        "provider": "siliconflow",
        "model_name": "moonshotai/Kimi-K2-Thinking",
        "base_url": "https://api.siliconflow.cn/v1",
    }


def test_create_llm_provider_normalizes_anthropic_model_alias() -> None:
    config = {
        "providers": {
            "anth": {
                "provider": "anthropic",
                "model_name": "claude-opus-4",
                "api_key": "demo-key",
                "base_url": "https://api.anthropic.com",
            }
        }
    }
    provider = create_llm_provider(config, "anth")
    assert isinstance(provider, AnthropicProvider)
    assert provider.model_name == "claude-opus-4-20250514"

    info = describe_llm_profile(config, "anth", provider)
    assert info == {
        "profile": "anth",
        "provider": "anthropic",
        "model_name": "claude-opus-4-20250514",
        "base_url": "https://api.anthropic.com",
    }


def test_extract_json_ignores_surrounding_text() -> None:
    raw = """Here is the strategy summary.

```json
{"headline":"focus hybrid signals","winning_patterns":["rank + ts_mean"]}
```

Additional notes follow.
"""
    assert extract_json(raw) == '{"headline":"focus hybrid signals","winning_patterns":["rank + ts_mean"]}'


def test_alpha_templates_use_supported_std_operator() -> None:
    generator = AlphaGenerator()
    templates = (
        generator.regular_templates
        + generator.power_pool_templates
        + generator.atom_templates
        + generator.superalpha_templates
    )
    assert all("ts_std(" not in template.expression for template in templates)


def test_normalize_fastexpr_operators_rewrites_legacy_aliases() -> None:
    expression = "rank(ts_std(close, 20)) + ts_std (returns, 5)"
    assert normalize_fastexpr_operators(expression) == "rank(ts_std_dev(close, 20)) + ts_std_dev (returns, 5)"
