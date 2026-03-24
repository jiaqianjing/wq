"""Dashboard HTTP server for WQA runtime monitoring."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from wq_brain.agent_runtime import RuntimeStore, read_config_snapshot, read_log_tail

logger = logging.getLogger(__name__)


def render_dashboard_html() -> str:
    _html_path = Path(__file__).parent / "static" / "dashboard.html"
    return _html_path.read_text(encoding="utf-8")


class DashboardServer:
    def __init__(self, store: RuntimeStore, host: str, port: int, config_path: Path, log_path: Path):
        self.store = store
        self.host = host
        self.port = port
        self.config_path = config_path
        self.log_path = log_path
        self._server = None
        self._thread = None

    def start(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        store = self.store
        config_path = self.config_path
        log_path = self.log_path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                try:
                    if self.path == "/api/summary":
                        self._send_json(store.summary())
                        return
                    if self.path == "/api/ideas":
                        self._send_json(store.list_recent_ideas())
                        return
                    if self.path == "/api/experiments":
                        self._send_json(store.list_recent_experiments())
                        return
                    if self.path == "/api/events":
                        self._send_json(store.list_recent_events())
                        return
                    if self.path == "/api/feedback":
                        self._send_json(store.list_feedback())
                        return
                    if self.path == "/api/reflections":
                        self._send_json(store.list_recent_reflections())
                        return
                    if self.path == "/api/config":
                        self._send_json(read_config_snapshot(config_path))
                        return
                    if self.path == "/api/logs":
                        self._send_json({"tail": read_log_tail(log_path, lines=40)})
                        return
                    self._send_html(render_dashboard_html())
                except BrokenPipeError:
                    pass
                except Exception:
                    logger.exception("dashboard request error: %s", self.path)
                    try:
                        self._send_json({"error": "internal server error"}, status=500)
                    except BrokenPipeError:
                        pass

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send_json(self, payload: Any, *, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="dashboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
