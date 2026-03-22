from pathlib import Path

from wq_brain.agent_runtime import RuntimeStore, init_runtime_config, runtime_status


def test_init_runtime_config_uses_parent_directory(tmp_path: Path) -> None:
    config_path = tmp_path / ".wqa-smoke" / "config.yaml"
    init_runtime_config(config_path, force=True)
    text = config_path.read_text(encoding="utf-8")
    assert "state_dir: ./.wqa-smoke" in text


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
