"""
Multi-agent runtime for quant idea research, implementation, and review.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote
from xml.etree import ElementTree

import requests

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .alpha_generator import AlphaGenerator
from .alpha_submitter import AlphaSubmitter, SubmissionCriteria
from .client import Region, Unviverse, WorldQuantBrainClient
from .learning import AlphaAnalyzer, AlphaDatabase, SmartGenerator

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def load_yaml_config(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml is required to use the agent runtime")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return expand_env(raw)


DEFAULT_CONFIG_TEMPLATE = """# wqa: WorldQuant Agent Lab
# 运行前建议：
# 1. 填入 WorldQuant Brain 账号
# 2. 至少选择一个 LLM profile（gemini 或 kimi）
# 3. 如需通知，填入 Telegram bot token 与 chat_id

app:
  state_dir: ./.wqa
  dashboard_host: 127.0.0.1
  dashboard_port: 8765
  loop_sleep_seconds: 10
  default_claim_limit: 3

providers:
  gemini:
    provider: gemini
    model_name: gemini-2.5-pro
    api_key: ${GEMINI_API_KEY}
  kimi:
    provider: kimi
    model_name: moonshot-v1-8k
    api_key: ${KIMI_API_KEY}
    base_url: https://api.moonshot.cn/v1

agents:
  researcher:
    enabled: true
    interval_seconds: 900
    llm_profile: gemini
    idea_batch_size: 4
  engineer:
    enabled: true
    interval_seconds: 300
    llm_profile: kimi
    alpha_batch_size: 4
  reviewer:
    enabled: true
    interval_seconds: 180
    llm_profile: kimi

integrations:
  worldquant:
    username: ${WQB_USERNAME}
    password: ${WQB_PASSWORD}
    region: USA
    universe: TOP3000
    auto_submit: true
  telegram:
    enabled: false
    bot_token: ${TG_BOT_TOKEN}
    chat_id: ${TG_CHAT_ID}

sources:
  papers:
    - name: arxiv-qfin
      kind: atom
      url: https://export.arxiv.org/api/query?search_query=cat:q-fin.ST&sortBy=submittedDate&sortOrder=descending&max_results=8
  reports: []
  market: []

thresholds:
  min_sharpe: 1.25
  min_fitness: 0.7
  max_turnover: 0.7
  max_drawdown: 0.12
  min_returns: 0.0
"""


@dataclass
class SourceItem:
    source_kind: str
    title: str
    summary: str
    url: str
    published_at: str


class RuntimeStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._cache_lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        ensure_directory(db_path.parent)
        self._init_db()
        self._refresh_cache()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _refresh_cache(self) -> None:
        summary = self._summary_from_db()
        ideas = self._recent_ideas_from_db(limit=20)
        experiments = self._recent_experiments_from_db(limit=20)
        events = self._recent_events_from_db(limit=40)
        feedback = self._feedback_from_db(limit=12)
        with self._cache_lock:
            self._cache = {
                "summary": summary,
                "ideas": ideas,
                "experiments": experiments,
                "events": events,
                "feedback": feedback,
            }

    def _cached(self, key: str) -> Any:
        with self._cache_lock:
            return copy.deepcopy(self._cache.get(key))

    def _init_db(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT NOT NULL UNIQUE,
                published_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                rationale TEXT,
                source_kind TEXT,
                source_title TEXT,
                source_url TEXT,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                agent_notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ideas_status_priority ON ideas(status, priority, created_at);

            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idea_id INTEGER NOT NULL,
                alpha_name TEXT,
                alpha_expression TEXT,
                implementation_notes TEXT,
                status TEXT NOT NULL,
                sharpe REAL,
                fitness REAL,
                turnover REAL,
                drawdown REAL,
                returns REAL,
                wq_alpha_id TEXT,
                submitted INTEGER NOT NULL DEFAULT 0,
                submission_result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (idea_id) REFERENCES ideas(id)
            );
            CREATE INDEX IF NOT EXISTS idx_experiments_status_updated ON experiments(status, updated_at);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_status (
                agent_name TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                summary TEXT,
                last_heartbeat TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()
        self._refresh_cache()

    def add_source_items(self, items: Iterable[SourceItem]) -> int:
        conn = self._connect()
        cur = conn.cursor()
        inserted = 0
        now = utc_now()
        for item in items:
            try:
                cur.execute(
                    """
                    INSERT INTO sources (source_kind, title, summary, url, published_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.source_kind,
                        item.title,
                        item.summary,
                        item.url,
                        item.published_at,
                        now,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                continue
        conn.commit()
        conn.close()
        self._refresh_cache()
        return inserted

    def recent_sources(self, limit: int = 12) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM sources ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def add_ideas(self, ideas: List[Dict[str, Any]]) -> int:
        conn = self._connect()
        cur = conn.cursor()
        inserted = 0
        now = utc_now()
        for idea in ideas:
            existing = cur.execute(
                """
                SELECT id FROM ideas
                WHERE title = ? AND COALESCE(source_url, '') = COALESCE(?, '')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (idea["title"], idea.get("source_url")),
            ).fetchone()
            if existing:
                continue
            cur.execute(
                """
                INSERT INTO ideas (
                    title, summary, rationale, source_kind, source_title, source_url,
                    status, priority, agent_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idea["title"],
                    idea["summary"],
                    idea.get("rationale", ""),
                    idea.get("source_kind"),
                    idea.get("source_title"),
                    idea.get("source_url"),
                    idea.get("status", "queued"),
                    int(idea.get("priority", 50)),
                    idea.get("agent_notes", ""),
                    now,
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
        self._refresh_cache()
        return inserted

    def claim_ideas(self, limit: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT * FROM ideas
            WHERE status = 'queued'
            ORDER BY priority ASC, created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        claimed: List[Dict[str, Any]] = []
        now = utc_now()
        for row in rows:
            cur.execute(
                "UPDATE ideas SET status = 'engineering', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            claimed.append(dict(row))
        conn.commit()
        conn.close()
        self._refresh_cache()
        return claimed

    def update_idea(
        self,
        idea_id: int,
        *,
        status: str,
        agent_notes: Optional[str] = None,
    ) -> None:
        conn = self._connect()
        conn.execute(
            """
            UPDATE ideas
            SET status = ?, agent_notes = COALESCE(?, agent_notes), updated_at = ?
            WHERE id = ?
            """,
            (status, agent_notes, utc_now(), idea_id),
        )
        conn.commit()
        conn.close()
        self._refresh_cache()

    def create_experiment(self, payload: Dict[str, Any]) -> int:
        conn = self._connect()
        cur = conn.cursor()
        now = utc_now()
        cur.execute(
            """
            INSERT INTO experiments (
                idea_id, alpha_name, alpha_expression, implementation_notes, status,
                sharpe, fitness, turnover, drawdown, returns, wq_alpha_id,
                submitted, submission_result, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["idea_id"],
                payload.get("alpha_name"),
                payload.get("alpha_expression"),
                payload.get("implementation_notes", ""),
                payload["status"],
                payload.get("sharpe"),
                payload.get("fitness"),
                payload.get("turnover"),
                payload.get("drawdown"),
                payload.get("returns"),
                payload.get("wq_alpha_id"),
                int(bool(payload.get("submitted", False))),
                payload.get("submission_result"),
                now,
                now,
            ),
        )
        experiment_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        self._refresh_cache()
        return experiment_id

    def update_experiment(self, experiment_id: int, **updates: Any) -> None:
        if not updates:
            return
        columns = []
        values = []
        for key, value in updates.items():
            columns.append(f"{key} = ?")
            values.append(value)
        columns.append("updated_at = ?")
        values.append(utc_now())
        values.append(experiment_id)
        conn = self._connect()
        conn.execute(
            f"UPDATE experiments SET {', '.join(columns)} WHERE id = ?",
            tuple(values),
        )
        conn.commit()
        conn.close()
        self._refresh_cache()

    def claim_promising_experiments(self, limit: int) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT e.*, i.title AS idea_title, i.summary AS idea_summary
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            WHERE e.status = 'promising'
            ORDER BY e.updated_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        now = utc_now()
        claimed = []
        for row in rows:
            cur.execute(
                "UPDATE experiments SET status = 'reviewing', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            claimed.append(dict(row))
        conn.commit()
        conn.close()
        self._refresh_cache()
        return claimed

    def add_event(
        self,
        *,
        level: str,
        kind: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO events (level, kind, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (level, kind, message, json.dumps(payload or {}, ensure_ascii=False), utc_now()),
        )
        conn.commit()
        conn.close()
        self._refresh_cache()

    def set_agent_status(self, agent_name: str, state: str, summary: str) -> None:
        now = utc_now()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO agent_status (agent_name, state, summary, last_heartbeat, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
                state = excluded.state,
                summary = excluded.summary,
                last_heartbeat = excluded.last_heartbeat,
                updated_at = excluded.updated_at
            """,
            (agent_name, state, summary, now, now),
        )
        conn.commit()
        conn.close()
        self._refresh_cache()

    def list_agent_status(self) -> List[Dict[str, Any]]:
        cached = self._cached("summary")
        if cached:
            return cached.get("agents", [])
        return self._agent_status_from_db()

    def _agent_status_from_db(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM agent_status ORDER BY agent_name ASC"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_recent_ideas(self, limit: int = 20) -> List[Dict[str, Any]]:
        cached = self._cached("ideas")
        if cached is not None:
            return cached[:limit]
        return self._recent_ideas_from_db(limit)

    def _recent_ideas_from_db(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM ideas ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_recent_experiments(self, limit: int = 20) -> List[Dict[str, Any]]:
        cached = self._cached("experiments")
        if cached is not None:
            return cached[:limit]
        return self._recent_experiments_from_db(limit)

    def _recent_experiments_from_db(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT e.*, i.title AS idea_title
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            ORDER BY e.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_recent_events(self, limit: int = 40) -> List[Dict[str, Any]]:
        cached = self._cached("events")
        if cached is not None:
            return cached[:limit]
        return self._recent_events_from_db(limit)

    def _recent_events_from_db(self, limit: int = 40) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def list_feedback(self, limit: int = 12) -> List[Dict[str, Any]]:
        cached = self._cached("feedback")
        if cached is not None:
            return cached[:limit]
        return self._feedback_from_db(limit)

    def _feedback_from_db(self, limit: int = 12) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT i.title AS idea_title, e.alpha_name, e.status, e.sharpe, e.fitness,
                   e.turnover, e.drawdown, e.returns, e.submission_result, e.updated_at
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            ORDER BY e.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def summary(self) -> Dict[str, Any]:
        cached = self._cached("summary")
        if cached:
            return cached
        return self._summary_from_db()

    def _summary_from_db(self) -> Dict[str, Any]:
        conn = self._connect()
        counts = {}
        for table in ("sources", "ideas", "experiments", "events"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        idea_status = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM ideas GROUP BY status"
            ).fetchall()
        }
        experiment_status = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM experiments GROUP BY status"
            ).fetchall()
        }
        best_experiment_row = conn.execute(
            """
            SELECT e.id, e.alpha_name, e.sharpe, e.fitness, e.turnover, e.status, i.title AS idea_title
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            WHERE e.sharpe IS NOT NULL
            ORDER BY e.sharpe DESC, e.updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        best_experiment = dict(best_experiment_row) if best_experiment_row else None
        latest_accept_row = conn.execute(
            """
            SELECT e.id, e.status, e.submission_result, i.title AS idea_title, e.alpha_name, e.updated_at
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            WHERE e.status IN ('accepted', 'submitted')
            ORDER BY e.updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_accept = dict(latest_accept_row) if latest_accept_row else None
        conn.close()
        return {
            "counts": counts,
            "idea_status": idea_status,
            "experiment_status": experiment_status,
            "agents": self._agent_status_from_db(),
            "best_experiment": best_experiment,
            "latest_accept": latest_accept,
        }


class BaseLLMProvider:
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class DisabledLLMProvider(BaseLLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("LLM provider is not configured")


class GeminiProvider(BaseLLMProvider):
    def __init__(self, model_name: str, api_key: str):
        self.model_name = model_name
        self.api_key = api_key

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model_name)}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "") for part in parts if part.get("text"))


class KimiProvider(BaseLLMProvider):
    def __init__(self, model_name: str, api_key: str, base_url: str):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.4,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


def create_llm_provider(config: Dict[str, Any], profile_name: Optional[str]) -> BaseLLMProvider:
    providers = config.get("providers", {})
    if not profile_name or profile_name not in providers:
        return DisabledLLMProvider()
    profile = providers[profile_name]
    provider = profile.get("provider", "").lower().strip()
    model_name = profile.get("model_name", "")
    api_key = profile.get("api_key", "")
    if not model_name or not api_key:
        return DisabledLLMProvider()
    if provider == "gemini":
        return GeminiProvider(model_name=model_name, api_key=api_key)
    if provider == "kimi":
        return KimiProvider(
            model_name=model_name,
            api_key=api_key,
            base_url=profile.get("base_url", "https://api.moonshot.cn/v1"),
        )
    return DisabledLLMProvider()


class TelegramNotifier:
    def __init__(self, config: Dict[str, Any]):
        self.enabled = bool(config.get("enabled"))
        self.bot_token = config.get("bot_token", "")
        self.chat_id = config.get("chat_id", "")

    def send(self, text: str) -> None:
        if not (self.enabled and self.bot_token and self.chat_id):
            return
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()


class SourceCollector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def collect(self) -> List[SourceItem]:
        items: List[SourceItem] = []
        for kind in ("papers", "reports", "market"):
            for source in self.config.get("sources", {}).get(kind, []):
                try:
                    items.extend(self._fetch_source(kind, source))
                except Exception as exc:  # pragma: no cover
                    logger.warning("failed to fetch source %s: %s", source, exc)
        return items

    def _fetch_source(self, kind: str, source: Dict[str, Any]) -> List[SourceItem]:
        url = source.get("url", "")
        if not url:
            return []
        timeout_seconds = int(source.get("timeout_seconds", 15))
        response = requests.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        items: List[SourceItem] = []

        atom_entries = root.findall("{http://www.w3.org/2005/Atom}entry")
        if atom_entries:
            for entry in atom_entries:
                title = entry.findtext("{http://www.w3.org/2005/Atom}title", default="").strip()
                summary = entry.findtext("{http://www.w3.org/2005/Atom}summary", default="").strip()
                published = entry.findtext("{http://www.w3.org/2005/Atom}published", default=utc_now())
                link_node = entry.find("{http://www.w3.org/2005/Atom}id")
                url_value = link_node.text.strip() if link_node is not None and link_node.text else url
                if title:
                    items.append(
                        SourceItem(
                            source_kind=kind,
                            title=title,
                            summary=summary,
                            url=url_value,
                            published_at=published,
                        )
                    )
            return items

        for entry in root.findall(".//item"):
            title = (entry.findtext("title") or "").strip()
            summary = (entry.findtext("description") or "").strip()
            published = (entry.findtext("pubDate") or utc_now()).strip()
            link = (entry.findtext("link") or url).strip()
            if title:
                items.append(
                    SourceItem(
                        source_kind=kind,
                        title=title,
                        summary=summary,
                        url=link,
                        published_at=published,
                    )
                )
        return items


class DashboardServer:
    def __init__(self, store: RuntimeStore, host: str, port: int):
        self.store = store
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        store = self.store

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
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
                self._send_html(render_dashboard_html())

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send_json(self, payload: Any) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
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


def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WQA Dashboard</title>
  <style>
    :root {
      --bg: #f4f0e8;
      --panel: rgba(255, 252, 247, 0.88);
      --ink: #1d2a35;
      --muted: #64707a;
      --line: rgba(29, 42, 53, 0.12);
      --accent: #0f766e;
      --accent-soft: rgba(15, 118, 110, 0.12);
      --warn: #b45309;
      --good: #166534;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.18), transparent 35%),
        radial-gradient(circle at top right, rgba(180,83,9,0.12), transparent 32%),
        linear-gradient(135deg, #f8f4ed, var(--bg));
      min-height: 100vh;
    }
    main {
      width: min(1200px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: clamp(32px, 4vw, 56px);
      line-height: 0.95;
      letter-spacing: -0.03em;
    }
    .subhead {
      color: var(--muted);
      max-width: 780px;
      margin-bottom: 24px;
      font-size: 16px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px 18px 14px;
      backdrop-filter: blur(12px);
      box-shadow: 0 12px 30px rgba(29, 42, 53, 0.08);
    }
    h2 {
      margin: 0 0 12px;
      font-size: 20px;
    }
    .stat {
      display: flex;
      justify-content: space-between;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      font-size: 15px;
    }
    .stat:last-child { border-bottom: 0; }
    .pill {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      margin-left: 8px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      padding: 9px 0;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 600; }
    .mono { font-family: "SFMono-Regular", "Menlo", monospace; }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
  </style>
</head>
<body>
<main>
  <h1>WQA Agent Lab</h1>
  <div class="subhead">Researcher scans fresh sources and backtest feedback, Engineer turns ideas into candidate alphas, Reviewer accepts and submits promising signals while the board keeps the whole loop visible.</div>
  <div class="grid">
    <section class="card">
      <h2>Summary</h2>
      <div id="summary">Loading...</div>
    </section>
    <section class="card">
      <h2>Agent Status</h2>
      <div id="agents">Loading...</div>
    </section>
    <section class="card">
      <h2>Best Result</h2>
      <div id="best">Loading...</div>
    </section>
  </div>
  <div class="grid" style="margin-top:16px;">
    <section class="card">
      <h2>Ideas Queue</h2>
      <div id="ideas">Loading...</div>
    </section>
    <section class="card">
      <h2>Experiments</h2>
      <div id="experiments">Loading...</div>
    </section>
  </div>
  <section class="card" style="margin-top:16px;">
    <h2>Feedback Loop</h2>
    <div id="feedback">Loading...</div>
  </section>
  <section class="card" style="margin-top:16px;">
    <h2>Event Log</h2>
    <div id="events">Loading...</div>
  </section>
</main>
<script>
async function getJSON(path) {
  const response = await fetch(path);
  return response.json();
}

function renderSummary(summary) {
  const lines = [];
  const counts = summary.counts || {};
  for (const [key, value] of Object.entries(counts)) {
    lines.push(`<div class="stat"><span>${key}</span><strong>${value}</strong></div>`);
  }
  if ((summary.latest_accept || {}).idea_title) {
    lines.push(`<div class="stat"><span>latest accept</span><strong>${summary.latest_accept.idea_title}</strong></div>`);
  }
  document.querySelector("#summary").innerHTML = lines.join("") || "<div>No data</div>";
}

function renderAgents(items) {
  const html = (items || []).map(item => (
    `<div class="stat"><div><strong>${item.agent_name}</strong><span class="pill">${item.state}</span><div>${item.summary || ""}</div></div><div class="mono">${item.last_heartbeat || ""}</div></div>`
  )).join("");
  document.querySelector("#agents").innerHTML = html || "<div>No agent heartbeat yet</div>";
}

function renderBest(item) {
  if (!item) {
    document.querySelector("#best").innerHTML = "<div>No completed experiment yet</div>";
    return;
  }
  document.querySelector("#best").innerHTML = `
    <div class="stat"><span>idea</span><strong>${item.idea_title}</strong></div>
    <div class="stat"><span>alpha</span><strong>${item.alpha_name || "-"}</strong></div>
    <div class="stat"><span>sharpe</span><strong class="good">${item.sharpe ?? "-"}</strong></div>
    <div class="stat"><span>fitness</span><strong>${item.fitness ?? "-"}</strong></div>
    <div class="stat"><span>turnover</span><strong>${item.turnover ?? "-"}</strong></div>
  `;
}

function renderIdeas(items) {
  const rows = (items || []).map(item => (
    `<tr><td><strong>${item.title}</strong><div>${item.summary}</div></td><td>${item.status}</td><td>${item.priority}</td></tr>`
  )).join("");
  document.querySelector("#ideas").innerHTML = `<table><thead><tr><th>Idea</th><th>Status</th><th>P</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderExperiments(items) {
  const rows = (items || []).map(item => (
    `<tr><td><strong>${item.idea_title}</strong><div class="mono">${item.alpha_name || ""}</div></td><td>${item.status}</td><td class="${(item.status === "submitted" || item.status === "accepted") ? "good" : ""}">${item.sharpe ?? "-"}</td></tr>`
  )).join("");
  document.querySelector("#experiments").innerHTML = `<table><thead><tr><th>Experiment</th><th>Status</th><th>Sharpe</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderFeedback(items) {
  const rows = (items || []).map(item => (
    `<tr><td><strong>${item.idea_title}</strong><div class="mono">${item.alpha_name || ""}</div></td><td>${item.status}</td><td>${item.sharpe ?? "-"}</td><td>${item.turnover ?? "-"}</td></tr>`
  )).join("");
  document.querySelector("#feedback").innerHTML = `<table><thead><tr><th>Idea</th><th>Status</th><th>Sharpe</th><th>Turnover</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderEvents(items) {
  const rows = (items || []).map(item => (
    `<div class="stat"><div><strong>${item.kind}</strong><div>${item.message}</div></div><div class="mono">${item.created_at}</div></div>`
  )).join("");
  document.querySelector("#events").innerHTML = rows || "<div>No events yet</div>";
}

async function refresh() {
  const [summary, ideas, experiments, feedback, events] = await Promise.all([
    getJSON("/api/summary"),
    getJSON("/api/ideas"),
    getJSON("/api/experiments"),
    getJSON("/api/feedback"),
    getJSON("/api/events"),
  ]);
  renderSummary(summary);
  renderAgents(summary.agents || []);
  renderBest(summary.best_experiment);
  renderIdeas(ideas);
  renderExperiments(experiments);
  renderFeedback(feedback);
  renderEvents(events);
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class AgentRuntime:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = load_yaml_config(config_path)
        self.state_dir = ensure_directory(Path(self.config["app"]["state_dir"]).resolve())
        self.runtime_db = self.state_dir / "runtime.db"
        self.pid_file = self.state_dir / "wqa.pid"
        self.meta_file = self.state_dir / "runtime.json"
        self.log_dir = ensure_directory(self.state_dir / "logs")
        self.store = RuntimeStore(self.runtime_db)
        self.stop_event = threading.Event()
        self.dashboard = DashboardServer(
            self.store,
            host=self.config["app"].get("dashboard_host", "127.0.0.1"),
            port=int(self.config["app"].get("dashboard_port", 8765)),
        )
        self.collector = SourceCollector(self.config)
        self.notifier = TelegramNotifier(self.config.get("integrations", {}).get("telegram", {}))
        self.alpha_generator = AlphaGenerator()
        self.learning_db = AlphaDatabase(str(self.state_dir / "alpha_history.db"))

    def _worldquant_client(self) -> Optional[WorldQuantBrainClient]:
        wq_config = self.config.get("integrations", {}).get("worldquant", {})
        username = wq_config.get("username", "")
        password = wq_config.get("password", "")
        if not username or not password:
            return None
        client = WorldQuantBrainClient(username, password)
        if not client.authenticate():
            raise RuntimeError("WorldQuant authentication failed")
        client.submission_log_path = str(self.state_dir / "submission_checks.jsonl")
        return client

    def _criteria(self) -> SubmissionCriteria:
        thresholds = self.config.get("thresholds", {})
        return SubmissionCriteria(
            min_sharpe=float(thresholds.get("min_sharpe", 1.25)),
            min_fitness=float(thresholds.get("min_fitness", 0.7)),
            max_turnover=float(thresholds.get("max_turnover", 0.7)),
            max_drawdown=float(thresholds.get("max_drawdown", 0.12)),
            min_returns=float(thresholds.get("min_returns", 0.0)),
        )

    def write_runtime_metadata(self) -> None:
        payload = {
            "pid": os.getpid(),
            "started_at": utc_now(),
            "config_path": str(self.config_path),
            "state_dir": str(self.state_dir),
            "log_dir": str(self.log_dir),
            "dashboard_url": f"http://{self.config['app'].get('dashboard_host', '127.0.0.1')}:{self.config['app'].get('dashboard_port', 8765)}",
        }
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        self.meta_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def remove_runtime_metadata(self) -> None:
        for path in (self.pid_file, self.meta_file):
            if path.exists():
                path.unlink()

    def run_foreground(self) -> None:
        self.write_runtime_metadata()
        self.dashboard.start()
        self.store.add_event(level="info", kind="runtime", message="wqa daemon started")
        self._install_signal_handlers()

        threads = []
        for agent_name in ("researcher", "engineer", "reviewer"):
            if self.config.get("agents", {}).get(agent_name, {}).get("enabled", True):
                thread = threading.Thread(
                    target=self._agent_loop,
                    args=(agent_name,),
                    name=f"{agent_name}-agent",
                    daemon=True,
                )
                thread.start()
                threads.append(thread)

        while not self.stop_event.is_set():
            time.sleep(float(self.config["app"].get("loop_sleep_seconds", 10)))

        for thread in threads:
            thread.join(timeout=5)
        self.dashboard.stop()
        self.remove_runtime_metadata()

    def _install_signal_handlers(self) -> None:
        def handle_stop(signum: int, _frame: Any) -> None:
            logger.info("received signal %s, stopping runtime", signum)
            self.stop_event.set()

        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)

    def _agent_loop(self, agent_name: str) -> None:
        agent_config = self.config.get("agents", {}).get(agent_name, {})
        interval_seconds = max(int(agent_config.get("interval_seconds", 300)), 10)
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.store.set_agent_status(agent_name, "running", "starting cycle")
                if agent_name == "researcher":
                    summary = self.run_researcher_cycle()
                elif agent_name == "engineer":
                    summary = self.run_engineer_cycle()
                else:
                    summary = self.run_reviewer_cycle()
                self.store.set_agent_status(agent_name, "idle", summary)
            except Exception as exc:  # pragma: no cover
                logger.exception("%s cycle failed", agent_name)
                self.store.set_agent_status(agent_name, "error", str(exc))
                self.store.add_event(level="error", kind=agent_name, message=str(exc))
                summary = "error"

            elapsed = time.time() - started
            remaining = self._next_sleep_seconds(agent_name, summary, interval_seconds, elapsed)
            self.stop_event.wait(remaining)

    def _next_sleep_seconds(
        self,
        agent_name: str,
        summary: str,
        interval_seconds: int,
        elapsed: float,
    ) -> int:
        if agent_name == "researcher":
            return max(interval_seconds - int(elapsed), 1)

        if summary in {"no queued ideas", "no promising experiments"}:
            return min(15, interval_seconds)

        return max(interval_seconds - int(elapsed), 1)

    def run_researcher_cycle(self) -> str:
        items = self.collector.collect()
        inserted = self.store.add_source_items(items)
        recent_sources = self.store.recent_sources(limit=8)
        recent_experiments = self.store.list_recent_experiments(limit=8)
        provider = create_llm_provider(
            self.config,
            self.config.get("agents", {}).get("researcher", {}).get("llm_profile"),
        )
        batch_size = int(
            self.config.get("agents", {}).get("researcher", {}).get("idea_batch_size", 4)
        )
        ideas = self._generate_research_ideas(provider, recent_sources, recent_experiments, batch_size)
        inserted_ideas = self.store.add_ideas(ideas)
        self.store.add_event(
            level="info",
            kind="researcher",
            message=f"researcher created {inserted_ideas} ideas from {len(recent_sources)} recent sources",
            payload={"new_sources": inserted, "ideas": inserted_ideas},
        )
        return f"new_sources={inserted}, queued_ideas={inserted_ideas}"

    def _generate_research_ideas(
        self,
        provider: BaseLLMProvider,
        recent_sources: List[Dict[str, Any]],
        recent_experiments: List[Dict[str, Any]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        sources_text = json.dumps(recent_sources[:8], ensure_ascii=False, indent=2)
        experiments_text = json.dumps(recent_experiments[:6], ensure_ascii=False, indent=2)
        system_prompt = (
            "You are a quantitative researcher agent. Produce compact JSON only. "
            "Return a list with title, summary, rationale, priority, source_kind, source_title, source_url."
        )
        user_prompt = (
            f"Generate {batch_size} actionable WorldQuant alpha ideas from the following fresh sources "
            f"and recent experiment feedback.\nSources:\n{sources_text}\nExperiments:\n{experiments_text}"
        )

        try:
            raw = provider.generate(system_prompt, user_prompt)
            ideas = json.loads(extract_json(raw))
            return [self._normalize_idea(idea) for idea in ideas[:batch_size]]
        except Exception:
            return self._fallback_research_ideas(recent_sources, recent_experiments, batch_size)

    def _fallback_research_ideas(
        self,
        recent_sources: List[Dict[str, Any]],
        recent_experiments: List[Dict[str, Any]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        ideas = []
        failure_hint = "Reduce turnover and favor templates with stronger recent Sharpe stability."
        if recent_experiments:
            promising = [item for item in recent_experiments if item.get("status") in {"promising", "submitted"}]
            if promising:
                failure_hint = "Extend recently promising motifs into diversified variants with different windows and neutralization."
        for index, source in enumerate(recent_sources[:batch_size]):
            ideas.append(
                self._normalize_idea(
                    {
                        "title": f"{source['source_kind']} signal idea {index + 1}: {source['title'][:60]}",
                        "summary": f"Turn {source['title']} into a cross-sectional alpha hypothesis anchored to liquidity, reversion, or momentum.",
                        "rationale": failure_hint,
                        "priority": 10 + index,
                        "source_kind": source.get("source_kind"),
                        "source_title": source.get("title"),
                        "source_url": source.get("url"),
                    }
                )
            )
        if not ideas:
            ideas.append(
                self._normalize_idea(
                    {
                        "title": "Offline learning-driven refinement",
                        "summary": "Generate regular alpha variants from the best historical templates and push them into the engineering queue.",
                        "rationale": failure_hint,
                        "priority": 25,
                        "source_kind": "history",
                        "source_title": "local alpha history",
                        "source_url": "",
                    }
                )
            )
        return ideas

    def _normalize_idea(self, idea: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": str(idea.get("title", "Untitled idea")).strip(),
            "summary": str(idea.get("summary", "")).strip() or "No summary",
            "rationale": str(idea.get("rationale", "")).strip(),
            "priority": int(idea.get("priority", 50)),
            "source_kind": idea.get("source_kind"),
            "source_title": idea.get("source_title"),
            "source_url": idea.get("source_url"),
            "status": "queued",
        }

    def run_engineer_cycle(self) -> str:
        limit = int(self.config["app"].get("default_claim_limit", 3))
        claimed_ideas = self.store.claim_ideas(limit=limit)
        if not claimed_ideas:
            return "no queued ideas"

        provider = create_llm_provider(
            self.config,
            self.config.get("agents", {}).get("engineer", {}).get("llm_profile"),
        )
        alpha_batch_size = int(self.config.get("agents", {}).get("engineer", {}).get("alpha_batch_size", 4))
        client = None
        try:
            client = self._worldquant_client()
        except Exception as exc:
            self.store.add_event(level="warning", kind="engineer", message=f"worldquant unavailable: {exc}")

        criteria = self._criteria()
        results = []
        for idea in claimed_ideas:
            alpha_candidates = self._generate_engineering_candidates(provider, idea, alpha_batch_size)
            if client is None:
                experiment_id = self.store.create_experiment(
                    {
                        "idea_id": idea["id"],
                        "alpha_name": alpha_candidates[0]["name"],
                        "alpha_expression": alpha_candidates[0]["expression"],
                        "implementation_notes": "WorldQuant credentials unavailable during engineer cycle",
                        "status": "blocked",
                    }
                )
                self.store.update_idea(idea["id"], status="blocked", agent_notes="Missing WorldQuant credentials")
                self.store.add_event(
                    level="warning",
                    kind="engineer",
                    message=f"idea {idea['id']} blocked because WorldQuant credentials are missing",
                    payload={"idea_id": idea["id"], "idea_title": idea["title"]},
                )
                results.append(experiment_id)
                continue

            submitter = AlphaSubmitter(
                client,
                criteria=criteria,
                results_dir=str(self.state_dir),
                enable_learning=True,
            )
            region = Region(self.config["integrations"]["worldquant"].get("region", "USA"))
            universe = Unviverse(self.config["integrations"]["worldquant"].get("universe", "TOP3000"))
            records = submitter.simulate_and_submit(
                alphas=alpha_candidates,
                region=region,
                universe=universe,
                auto_submit=False,
            )
            idea_status = "tested"
            for alpha_candidate, record in zip(alpha_candidates, records):
                meets_threshold = criteria.check(record.simulate_result)
                experiment_id = self.store.create_experiment(
                    {
                        "idea_id": idea["id"],
                        "alpha_name": alpha_candidate.get("name") or record.alpha_type or record.expression[:48],
                        "alpha_expression": record.expression,
                        "implementation_notes": f"category={record.category}; source_idea={idea['title']}",
                        "status": "promising" if meets_threshold else "rejected",
                        "sharpe": record.simulate_result.sharpe,
                        "fitness": record.simulate_result.fitness,
                        "turnover": record.simulate_result.turnover,
                        "drawdown": record.simulate_result.drawdown,
                        "returns": record.simulate_result.returns,
                        "wq_alpha_id": record.alpha_id,
                    }
                )
                results.append(experiment_id)
                if meets_threshold:
                    idea_status = "reviewing"
            self.store.update_idea(
                idea["id"],
                status=idea_status,
                agent_notes=f"generated {len(records)} alpha candidates",
            )
            self.store.add_event(
                level="info",
                kind="engineer",
                message=f"engineered {len(records)} candidates for idea {idea['id']}",
                payload={"idea_id": idea["id"], "idea_title": idea["title"], "idea_status": idea_status},
            )

        self.store.add_event(
            level="info",
            kind="engineer",
            message=f"engineer processed {len(claimed_ideas)} ideas and created {len(results)} experiments",
        )
        return f"claimed_ideas={len(claimed_ideas)}, experiments={len(results)}"

    def _generate_engineering_candidates(
        self,
        provider: BaseLLMProvider,
        idea: Dict[str, Any],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        smart_generator = SmartGenerator(AlphaAnalyzer(self.learning_db))
        template_weights = smart_generator.get_template_weights("regular")
        top_templates = sorted(template_weights.items(), key=lambda item: item[1], reverse=True)[:5]

        system_prompt = (
            "You are a quant implementation agent. Return JSON only. "
            "Each item needs name, category, expression, type."
        )
        user_prompt = (
            f"Implement up to {batch_size} WorldQuant alpha expressions for this idea:\n"
            f"{json.dumps(idea, ensure_ascii=False, indent=2)}\n"
            f"Recent strong template hints:\n{json.dumps(top_templates, ensure_ascii=False)}"
        )
        try:
            raw = provider.generate(system_prompt, user_prompt)
            payload = json.loads(extract_json(raw))
            candidates = [self._normalize_alpha_candidate(item) for item in payload[:batch_size]]
            if candidates:
                return candidates
        except Exception:
            pass

        generated = self.alpha_generator.generate_regular_alphas(batch_size, diversify=True)
        return [self._normalize_alpha_candidate(item) for item in generated]

    def _normalize_alpha_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": item.get("name", "agent_alpha"),
            "category": item.get("category", "research"),
            "expression": item.get("expression", "rank(close - vwap)"),
            "type": item.get("type", "regular"),
            "params": item.get("params", {}),
        }

    def run_reviewer_cycle(self) -> str:
        limit = int(self.config["app"].get("default_claim_limit", 3))
        experiments = self.store.claim_promising_experiments(limit=limit)
        if not experiments:
            return "no promising experiments"

        client = None
        try:
            client = self._worldquant_client()
        except Exception as exc:
            self.store.add_event(level="warning", kind="reviewer", message=f"worldquant unavailable: {exc}")

        auto_submit = bool(self.config.get("integrations", {}).get("worldquant", {}).get("auto_submit", True))
        accepted = 0
        submitted = 0
        for experiment in experiments:
            final_status = "accepted"
            summary = (
                f"*Accepted alpha*\n"
                f"Idea: {experiment['idea_title']}\n"
                f"Sharpe: {experiment.get('sharpe')}\n"
                f"Fitness: {experiment.get('fitness')}\n"
                f"Turnover: {experiment.get('turnover')}\n"
                f"Alpha ID: `{experiment.get('wq_alpha_id') or 'n/a'}`"
            )
            if client and auto_submit and experiment.get("wq_alpha_id"):
                success = client.submit_alpha(experiment["wq_alpha_id"])
                self.store.update_experiment(
                    experiment["id"],
                    status="submitted" if success else "accepted",
                    submitted=1 if success else 0,
                    submission_result="submitted_to_worldquant" if success else "accepted_but_submit_failed",
                )
                if success:
                    submitted += 1
                    final_status = "submitted"
                    summary += "\nStatus: submitted to WorldQuant"
                else:
                    summary += "\nStatus: accepted, submit failed"
            else:
                self.store.update_experiment(
                    experiment["id"],
                    status="accepted",
                    submission_result="accepted_without_submit",
                )
                summary += "\nStatus: accepted without submit"
            self.store.update_idea(experiment["idea_id"], status="accepted", agent_notes="reviewer accepted candidate")
            self.notifier.send(summary)
            self.store.add_event(
                level="info",
                kind="reviewer",
                message=f"reviewed experiment {experiment['id']} with status {final_status}",
                payload={"experiment_id": experiment["id"], "idea_id": experiment["idea_id"], "alpha_name": experiment.get("alpha_name")},
            )
            accepted += 1

        self.store.add_event(
            level="info",
            kind="reviewer",
            message=f"reviewer accepted {accepted} experiments, submitted {submitted}",
        )
        return f"accepted={accepted}, submitted={submitted}"


def extract_json(text: str) -> str:
    if not text:
        return "[]"
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    raise ValueError("No JSON payload found")


def init_runtime_config(config_path: Path, force: bool = False) -> Path:
    if config_path.exists() and not force:
        raise FileExistsError(f"config already exists: {config_path}")
    ensure_directory(config_path.parent)
    template = DEFAULT_CONFIG_TEMPLATE
    state_dir = f"./{config_path.parent.name}" if config_path.parent.name else "./.wqa"
    template = template.replace("state_dir: ./.wqa", f"state_dir: {state_dir}", 1)
    config_path.write_text(template, encoding="utf-8")
    return config_path


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_runtime(config_path: Path) -> Dict[str, Any]:
    runtime = AgentRuntime(config_path)
    if runtime.pid_file.exists():
        try:
            current_pid = int(runtime.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            current_pid = 0
        if current_pid and is_process_alive(current_pid):
            return {
                "status": "already_running",
                "pid": current_pid,
                "dashboard_url": f"http://{runtime.config['app'].get('dashboard_host', '127.0.0.1')}:{runtime.config['app'].get('dashboard_port', 8765)}",
            }

    stdout_path = runtime.log_dir / "wqa.out.log"
    stderr_path = runtime.log_dir / "wqa.err.log"
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "wq_brain.agent_cli", "--config", str(config_path), "run-daemon"],
            cwd=str(Path.cwd()),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    return {
        "status": "started",
        "pid": process.pid,
        "dashboard_url": f"http://{runtime.config['app'].get('dashboard_host', '127.0.0.1')}:{runtime.config['app'].get('dashboard_port', 8765)}",
    }


def stop_runtime(config_path: Path) -> Dict[str, Any]:
    runtime = AgentRuntime(config_path)
    if not runtime.pid_file.exists():
        return {"status": "not_running"}
    pid = int(runtime.pid_file.read_text(encoding="utf-8").strip())
    if not is_process_alive(pid):
        runtime.remove_runtime_metadata()
        return {"status": "stale_pid", "pid": pid}
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not is_process_alive(pid):
            runtime.remove_runtime_metadata()
            return {"status": "stopped", "pid": pid}
        time.sleep(0.5)
    return {"status": "stop_requested", "pid": pid}


def runtime_status(config_path: Path) -> Dict[str, Any]:
    runtime = AgentRuntime(config_path)
    pid = None
    if runtime.pid_file.exists():
        try:
            pid = int(runtime.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = None
    summary = runtime.store.summary()
    return {
        "config_path": str(config_path),
        "state_dir": str(runtime.state_dir),
        "log_dir": str(runtime.log_dir),
        "running": bool(pid and is_process_alive(pid)),
        "pid": pid,
        "dashboard_url": f"http://{runtime.config['app'].get('dashboard_host', '127.0.0.1')}:{runtime.config['app'].get('dashboard_port', 8765)}",
        "summary": summary,
        "feedback": runtime.store.list_feedback(limit=5),
        "recent_events": runtime.store.list_recent_events(limit=5),
    }
