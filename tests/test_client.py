import logging

from wq_brain.client import WorldQuantBrainClient


def test_client_disables_proxy_from_config() -> None:
    client = WorldQuantBrainClient("demo@example.com", "secret", disable_proxy=True)
    assert client.proxy_disabled is True
    assert client.session.trust_env is False


def test_wait_for_simulation_progress_timeout_reports_elapsed(monkeypatch, caplog) -> None:
    client = WorldQuantBrainClient("demo@example.com", "secret")

    class DummyResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"status": "RUNNING"}

    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: DummyResponse())

    clock = iter([0.0, 0.0, 3.0, 3.0, 6.0, 6.0])
    monkeypatch.setattr("wq_brain.client.time.time", lambda: next(clock))
    monkeypatch.setattr("wq_brain.client.time.sleep", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.INFO):
        result = client._wait_for_simulation_progress("https://example.com/sim", max_wait=5)

    assert result.status == "TIMEOUT"
    assert result.error_message == "模拟超时 after 6s and 1 polls"
    assert "模拟尚未完成" in caplog.text


def test_wait_for_simulation_progress_stops_on_error_status(monkeypatch, caplog) -> None:
    client = WorldQuantBrainClient("demo@example.com", "secret")

    class DummyResponse:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"status": "ERROR", "message": "invalid expression"}

    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: DummyResponse())
    monkeypatch.setattr("wq_brain.client.time.time", lambda: 0.0)
    monkeypatch.setattr("wq_brain.client.time.sleep", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.WARNING):
        result = client._wait_for_simulation_progress("https://example.com/sim", max_wait=60)

    assert result.status == "ERROR"
    assert result.error_message == "invalid expression"
    assert "模拟失败，停止轮询" in caplog.text
