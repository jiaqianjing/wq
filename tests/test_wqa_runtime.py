import logging
from pathlib import Path
from types import SimpleNamespace

import requests

from wq_brain.alpha_generator import AlphaGenerator
from wq_brain.agent_runtime import (
    AgentRuntime,
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
from wq_brain.client import SimulateResult, SubmissionCheckResult, SubmissionResult
from wq_brain.source_collector import SourceCollector


ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Factor Timing with Regime Signals</title>
    <summary>Fresh q-fin paper.</summary>
    <id>https://arxiv.org/abs/2501.00001</id>
    <published>2026-03-29T00:00:00Z</published>
  </entry>
</feed>
"""


class DummyResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


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


def test_source_collector_cools_down_after_429_retry_after(monkeypatch, caplog) -> None:
    collector = SourceCollector(
        {
            "sources": {
                "papers": [
                    {
                        "name": "arxiv-qfin",
                        "url": "https://export.arxiv.org/api/query?search_query=cat:q-fin.ST",
                        "rate_limit_cooldown_seconds": 1800,
                    }
                ]
            }
        }
    )

    current_time = {"value": 0.0}
    requests_seen: list[dict[str, object]] = []
    responses = iter(
        [
            DummyResponse(429, headers={"Retry-After": "120"}),
            DummyResponse(200, text=ATOM_FEED),
        ]
    )

    def fake_get(url: str, timeout: int, headers: dict[str, str]) -> DummyResponse:
        requests_seen.append({"url": url, "timeout": timeout, "headers": headers})
        return next(responses)

    monkeypatch.setattr("wq_brain.source_collector.requests.get", fake_get)
    monkeypatch.setattr("wq_brain.source_collector.time.time", lambda: current_time["value"])

    with caplog.at_level(logging.INFO):
        assert collector.collect() == []
        current_time["value"] = 30.0
        assert collector.collect() == []
        current_time["value"] = 121.0
        items = collector.collect()

    assert len(requests_seen) == 2
    assert requests_seen[0]["headers"] == {"User-Agent": "wqa-source-collector/1.0"}
    assert len(items) == 1
    assert items[0].title == "Factor Timing with Regime Signals"
    assert "status=429" in caplog.text
    assert "skipping source arxiv-qfin during rate-limit cooldown" in caplog.text


def test_source_collector_uses_fallback_cooldown_without_retry_after(monkeypatch) -> None:
    collector = SourceCollector(
        {
            "sources": {
                "papers": [
                    {
                        "name": "arxiv-qfin",
                        "url": "https://export.arxiv.org/api/query?search_query=cat:q-fin.ST",
                        "rate_limit_cooldown_seconds": 45,
                    }
                ]
            }
        }
    )

    current_time = {"value": 0.0}
    call_count = {"value": 0}
    responses = iter([DummyResponse(429), DummyResponse(200, text=ATOM_FEED)])

    def fake_get(_url: str, timeout: int, headers: dict[str, str]) -> DummyResponse:
        call_count["value"] += 1
        assert timeout == 15
        assert headers["User-Agent"] == "wqa-source-collector/1.0"
        return next(responses)

    monkeypatch.setattr("wq_brain.source_collector.requests.get", fake_get)
    monkeypatch.setattr("wq_brain.source_collector.time.time", lambda: current_time["value"])

    assert collector.collect() == []
    current_time["value"] = 10.0
    assert collector.collect() == []
    current_time["value"] = 46.0
    items = collector.collect()

    assert call_count["value"] == 2
    assert len(items) == 1


def test_source_collector_applies_custom_user_agent(monkeypatch) -> None:
    collector = SourceCollector(
        {
            "sources": {
                "papers": [
                    {
                        "name": "custom-feed",
                        "url": "https://example.com/feed.xml",
                        "user_agent": "custom-agent/2.0",
                    }
                ]
            }
        }
    )
    seen_headers: list[dict[str, str]] = []

    def fake_get(_url: str, timeout: int, headers: dict[str, str]) -> DummyResponse:
        assert timeout == 15
        seen_headers.append(headers)
        return DummyResponse(200, text=ATOM_FEED)

    monkeypatch.setattr("wq_brain.source_collector.requests.get", fake_get)

    items = collector.collect()

    assert len(items) == 1
    assert seen_headers == [{"User-Agent": "custom-agent/2.0"}]


def test_source_collector_continues_after_non_429_http_error(monkeypatch, caplog) -> None:
    collector = SourceCollector(
        {
            "sources": {
                "papers": [
                    {"name": "broken-feed", "url": "https://example.com/broken.xml"},
                    {"name": "healthy-feed", "url": "https://example.com/healthy.xml"},
                ]
            }
        }
    )

    def fake_get(url: str, timeout: int, headers: dict[str, str]) -> DummyResponse:
        assert timeout == 15
        assert headers["User-Agent"] == "wqa-source-collector/1.0"
        if "broken" in url:
            return DummyResponse(500)
        return DummyResponse(200, text=ATOM_FEED)

    monkeypatch.setattr("wq_brain.source_collector.requests.get", fake_get)

    with caplog.at_level(logging.WARNING):
        items = collector.collect()

    assert len(items) == 1
    assert items[0].title == "Factor Timing with Regime Signals"
    assert "failed to fetch source broken-feed: status=500 url=https://example.com/broken.xml" in caplog.text


def test_reviewer_marks_worldquant_check_failures_blocked(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / ".wqa-smoke" / "config.yaml"
    init_runtime_config(config_path, force=True)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "state_dir: ./.wqa-smoke",
            f"state_dir: {tmp_path / 'runtime-state-reviewer'}",
        ),
        encoding="utf-8",
    )
    runtime = AgentRuntime(config_path)

    runtime.store.add_ideas(
        [
            {
                "title": "idea-blocked",
                "summary": "summary",
                "status": "reviewing",
            }
        ]
    )
    idea = runtime.store.list_recent_ideas(limit=1)[0]
    runtime.store.create_experiment(
        {
            "idea_id": idea["id"],
            "alpha_name": "blocked-alpha",
            "alpha_expression": "rank(close)",
            "status": "promising",
            "sharpe": 2.0,
            "fitness": 1.1,
            "turnover": 0.3,
            "wq_alpha_id": "alpha-123",
        }
    )

    class FakeClient:
        def submit_alpha_with_checks(self, alpha_id: str) -> SubmissionResult:
            assert alpha_id == "alpha-123"
            return SubmissionResult(
                alpha_id=alpha_id,
                submitted=False,
                reason="FAIL=PROD_CORRELATION",
                check_result=SubmissionCheckResult(
                    alpha_id=alpha_id,
                    ok=False,
                    pass_count=3,
                    fail_count=1,
                    warning_count=0,
                    pending_count=0,
                    checks=[{"name": "PROD_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.88}],
                    failed_checks=[{"name": "PROD_CORRELATION", "result": "FAIL", "limit": 0.7, "value": 0.88}],
                    warning_checks=[],
                    pending_checks=[],
                ),
            )

    monkeypatch.setattr(runtime, "_worldquant_client", lambda: FakeClient())

    summary = runtime.run_reviewer_cycle()
    experiment = runtime.store.list_recent_experiments(limit=1)[0]
    refreshed_idea = runtime.store.list_recent_ideas(limit=1)[0]

    assert summary == "accepted=0, submitted=0, blocked=1"
    assert experiment["status"] == "blocked"
    assert experiment["submission_result"] == "blocked:FAIL=PROD_CORRELATION"
    assert refreshed_idea["status"] == "blocked"


def test_engineer_refines_submission_blocked_experiment(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / ".wqa-smoke" / "config.yaml"
    init_runtime_config(config_path, force=True)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "state_dir: ./.wqa-smoke",
            f"state_dir: {tmp_path / 'runtime-state-engineer'}",
        ),
        encoding="utf-8",
    )
    runtime = AgentRuntime(config_path)

    runtime.store.add_ideas(
        [
            {
                "title": "idea-refine-blocked",
                "summary": "summary",
                "status": "blocked",
            }
        ]
    )
    idea = runtime.store.list_recent_ideas(limit=1)[0]
    runtime.store.create_experiment(
        {
            "idea_id": idea["id"],
            "alpha_name": "blocked-parent",
            "alpha_expression": "rank(close)",
            "status": "blocked",
            "sharpe": 2.0,
            "fitness": 1.02,
            "turnover": 0.31,
            "submission_result": "blocked:FAIL=PROD_CORRELATION",
            "wq_alpha_id": "alpha-parent",
        }
    )

    seen: dict[str, str] = {}

    def fake_generate(_provider: object, experiment: dict[str, object], _criteria: object) -> list[dict[str, str]]:
        seen["status"] = str(experiment["status"])
        seen["submission_result"] = str(experiment["submission_result"])
        return [
            {
                "name": "decorrelated_variant",
                "expression": "rank(ts_mean(close, 20))",
                "category": "PRICE_VOLUME",
                "type": "regular",
            }
        ]

    class FakeSubmitter:
        def __init__(self, *_args, **_kwargs):
            pass

        def simulate_and_submit(self, *args, **kwargs):
            return [
                SimpleNamespace(
                    expression="rank(ts_mean(close, 20))",
                    alpha_id="alpha-child",
                    category="PRICE_VOLUME",
                    simulate_result=SimulateResult(
                        alpha_id="alpha-child",
                        status="COMPLETE",
                        sharpe=1.9,
                        fitness=1.05,
                        turnover=0.28,
                        returns=0.12,
                        drawdown=0.04,
                        margin=0.01,
                        is_submittable=True,
                    ),
                )
            ]

    monkeypatch.setattr(runtime, "_generate_refinement_variants", fake_generate)
    monkeypatch.setattr("wq_brain.agent_runtime.AlphaSubmitter", FakeSubmitter)

    refined = runtime._refine_near_misses(provider=object(), client=object())
    recent = runtime.store.list_recent_experiments(limit=2)
    refreshed_idea = runtime.store.list_recent_ideas(limit=1)[0]

    assert refined >= 1
    assert seen == {
        "status": "blocked",
        "submission_result": "blocked:FAIL=PROD_CORRELATION",
    }
    assert any(item["alpha_name"] == "refined_decorrelated_variant" for item in recent)
    assert any(item["status"] == "promising" for item in recent)
    assert refreshed_idea["status"] == "reviewing"
