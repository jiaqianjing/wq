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


SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "password",
    "secret",
    "token",
    "chat_id",
)


def _mask_sensitive_value(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) <= 6:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def redact_sensitive_data(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
                redacted[key] = _mask_sensitive_value(item)
                continue
            redacted[key] = redact_sensitive_data(item, parent_key=key)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item, parent_key=parent_key) for item in value]
    return value


def read_config_snapshot(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        return {"error": f"config file not found: {config_path}"}
    return redact_sensitive_data(load_yaml_config(config_path))


def read_log_tail(log_path: Path, lines: int = 40) -> str:
    if not log_path.exists():
        return "No log file yet"
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = content[-lines:]
    return "\n".join(tail) if tail else "No log lines"


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
        reflections = self._recent_reflections_from_db(limit=8)
        with self._cache_lock:
            self._cache = {
                "summary": summary,
                "ideas": ideas,
                "experiments": experiments,
                "events": events,
                "feedback": feedback,
                "reflections": reflections,
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

    def list_recent_reflections(self, limit: int = 8) -> List[Dict[str, Any]]:
        cached = self._cached("reflections")
        if cached is not None:
            return cached[:limit]
        return self._recent_reflections_from_db(limit)

    def _recent_reflections_from_db(self, limit: int = 8) -> List[Dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT id, level, kind, message, payload_json, created_at
            FROM events
            WHERE kind = 'researcher_reflection'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        parsed: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload_json") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            parsed.append(item)
        return parsed

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
      --bg: #09111f;
      --bg-strong: #121d31;
      --panel: rgba(14, 24, 40, 0.86);
      --panel-strong: rgba(19, 31, 52, 0.96);
      --ink: #eef4ff;
      --muted: #93a4bf;
      --line: rgba(173, 194, 255, 0.12);
      --accent: #52d3c8;
      --accent-2: #ffb44d;
      --accent-soft: rgba(82, 211, 200, 0.14);
      --warn-soft: rgba(255, 180, 77, 0.12);
      --good: #166534;
      --bad: #b42318;
      --shadow: 0 18px 44px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(82, 211, 200, 0.18), transparent 24%),
        radial-gradient(circle at 90% 0%, rgba(255, 180, 77, 0.18), transparent 22%),
        linear-gradient(180deg, #07101b, var(--bg));
      min-height: 100vh;
    }
    main {
      width: min(1380px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 36px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .hero-panel,
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }
    .hero-panel {
      padding: 24px;
      min-height: 220px;
    }
    .hero-panel.accent {
      background:
        linear-gradient(135deg, rgba(82, 211, 200, 0.12), rgba(15, 20, 32, 0.35)),
        var(--panel-strong);
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(173, 194, 255, 0.08);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    h1 {
      margin: 14px 0 12px;
      font-size: clamp(38px, 5vw, 72px);
      line-height: 0.94;
      letter-spacing: -0.05em;
      font-family: "Space Grotesk", "IBM Plex Sans", sans-serif;
    }
    .lede {
      margin: 0;
      max-width: 820px;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.6;
    }
    .hero-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 20px;
    }
    .hero-stat {
      padding: 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(173, 194, 255, 0.1);
    }
    .hero-stat .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }
    .hero-stat .value {
      margin-top: 10px;
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.03em;
    }
    .hero-stat .subvalue {
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    .metric-card {
      padding: 18px;
      border-radius: 22px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }
    .metric-value {
      margin-top: 12px;
      font-size: clamp(28px, 3vw, 40px);
      font-weight: 800;
      letter-spacing: -0.04em;
    }
    .metric-foot {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(380px, 1.1fr);
      gap: 18px;
    }
    .stack {
      display: grid;
      gap: 18px;
    }
    .card {
      padding: 18px;
      overflow: hidden;
    }
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .title-wrap h2 {
      margin: 0;
      font-size: 22px;
      letter-spacing: -0.03em;
    }
    .title-wrap p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 32px;
      padding: 7px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
    }
    .badge.warn {
      background: var(--warn-soft);
      color: var(--accent-2);
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .item {
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(173, 194, 255, 0.08);
    }
    .item-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .item-title {
      font-size: 15px;
      font-weight: 700;
      line-height: 1.4;
    }
    .item-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }
    .item-summary {
      margin-top: 6px;
      color: var(--muted);
      line-height: 1.55;
      font-size: 14px;
    }
    .idea-detail-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
      margin-top: 12px;
    }
    .idea-detail {
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(173, 194, 255, 0.05);
      border: 1px solid rgba(173, 194, 255, 0.08);
    }
    .idea-detail strong {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .idea-detail p {
      margin: 0;
      line-height: 1.55;
      font-size: 14px;
      color: var(--ink);
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(173, 194, 255, 0.08);
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .status-chip {
      background: var(--accent-soft);
      color: var(--accent);
    }
    .status-chip.rejected,
    .status-chip.blocked,
    .status-chip.error {
      background: rgba(180, 35, 24, 0.12);
      color: var(--bad);
    }
    .status-chip.promising,
    .status-chip.submitted,
    .status-chip.accepted {
      background: rgba(22, 101, 52, 0.12);
      color: var(--good);
    }
    .muted {
      color: var(--muted);
    }
    .mono {
      font-family: "SFMono-Regular", "Menlo", monospace;
      font-size: 12px;
    }
    .split-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .pane {
      min-height: 0;
    }
    .scroll {
      max-height: 440px;
      overflow: auto;
      padding-right: 4px;
    }
    .code-block {
      margin: 0;
      padding: 16px;
      border-radius: 18px;
      background: #07111f;
      color: #dbe7ff;
      max-height: 440px;
      overflow: auto;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .pager {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px solid rgba(173, 194, 255, 0.08);
    }
    .pager-controls {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    button {
      appearance: none;
      border: 0;
      border-radius: 999px;
      padding: 9px 14px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      color: var(--ink);
      background: rgba(173, 194, 255, 0.1);
      cursor: pointer;
    }
    button:hover { background: rgba(173, 194, 255, 0.16); }
    button:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }
    .key-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
    }
    .key-row:last-child { border-bottom: 0; }
    .empty {
      padding: 26px 12px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed rgba(173, 194, 255, 0.16);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.02);
    }
    .chart-grid {
      display: grid;
      grid-template-columns: 1.15fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }
    .chart-shell {
      padding-top: 8px;
    }
    .svg-chart {
      width: 100%;
      height: 220px;
      display: block;
    }
    .chart-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .legend-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(173, 194, 255, 0.08);
      color: var(--muted);
      font-size: 12px;
    }
    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }
    .reflection-list {
      display: grid;
      gap: 12px;
    }
    .reflection-card {
      padding: 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(173, 194, 255, 0.08);
    }
    .reflection-card h3 {
      margin: 0 0 8px;
      font-size: 16px;
      line-height: 1.4;
    }
    .reflection-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .reflection-block {
      padding: 12px;
      border-radius: 14px;
      background: rgba(173, 194, 255, 0.05);
    }
    .reflection-block strong {
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .bullet-list {
      margin: 0;
      padding-left: 18px;
    }
    .bullet-list li {
      margin-bottom: 6px;
    }
    .trace-card {
      min-height: 620px;
    }
    .tab-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 18px;
    }
    .tab-button {
      padding: 10px 16px;
      border-radius: 999px;
      border: 1px solid rgba(173, 194, 255, 0.12);
      background: rgba(255, 255, 255, 0.03);
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .tab-button.active {
      background: rgba(82, 211, 200, 0.14);
      color: var(--ink);
      border-color: rgba(82, 211, 200, 0.32);
    }
    .tab-panel {
      display: none;
    }
    .tab-panel.active {
      display: block;
    }
    @media (max-width: 1100px) {
      .hero,
      .layout,
      .split-grid,
      .grid-2,
      .metrics,
      .chart-grid,
      .reflection-grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 720px) {
      main { width: min(100vw - 20px, 1380px); }
      .hero-panel, .card, .metric-card { border-radius: 20px; }
      .hero-stats { grid-template-columns: 1fr; }
      .pager { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>
<main>
  <section class="hero">
    <div class="hero-panel accent">
      <div class="eyebrow">WorldQuant Agent Lab</div>
      <h1>See The Queue, Not Just The Chaos.</h1>
      <p class="lede">WQA runs a small research pipeline in the background. This board is designed to answer three questions fast: what the agents are doing now, what just entered the queue, and whether recent experiments are getting better.</p>
      <div class="hero-stats" id="heroStats">
        <div class="hero-stat"><div class="label">Ideas in Flight</div><div class="value">...</div><div class="subvalue">Waiting for live runtime data</div></div>
        <div class="hero-stat"><div class="label">Best Sharpe</div><div class="value">...</div><div class="subvalue">Waiting for live runtime data</div></div>
      </div>
    </div>
    <div class="hero-panel">
      <div class="card-header">
        <div class="title-wrap">
          <h2>Agent Pulse</h2>
          <p>Latest heartbeat and current work summary.</p>
        </div>
        <span class="badge" id="agentCount">0</span>
      </div>
      <div class="list scroll" id="agents">Loading...</div>
    </div>
  </section>

  <nav class="tab-bar">
    <button class="tab-button active" data-tab="overview">Overview</button>
    <button class="tab-button" data-tab="research">Research</button>
    <button class="tab-button" data-tab="execution">Execution</button>
    <button class="tab-button" data-tab="ops">Ops</button>
  </nav>

  <section class="tab-panel active" data-panel="overview">
    <section class="metrics">
      <div class="metric-card">
        <div class="metric-label">Sources</div>
        <div class="metric-value" id="metricSources">0</div>
        <div class="metric-foot" id="metricSourcesFoot">Fresh external inputs collected by researcher.</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Ideas</div>
        <div class="metric-value" id="metricIdeas">0</div>
        <div class="metric-foot" id="metricIdeasFoot">Queued and engineering candidates.</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Experiments</div>
        <div class="metric-value" id="metricExperiments">0</div>
        <div class="metric-foot" id="metricExperimentsFoot">Backtests generated by engineer.</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Events</div>
        <div class="metric-value" id="metricEvents">0</div>
        <div class="metric-foot" id="metricEventsFoot">Recent runtime actions and transitions.</div>
      </div>
    </section>

    <section class="chart-grid">
      <section class="card">
        <div class="card-header">
          <div class="title-wrap">
            <h2>Experiment Trend</h2>
            <p>Recent Sharpe path across the latest backtests.</p>
          </div>
        </div>
        <div class="chart-shell">
          <div id="trendChart">Loading...</div>
        </div>
      </section>
      <section class="card">
        <div class="card-header">
          <div class="title-wrap">
            <h2>Status Mix</h2>
            <p>Current queue and experiment outcome distribution.</p>
          </div>
        </div>
        <div class="chart-shell">
          <div id="mixChart">Loading...</div>
        </div>
      </section>
    </section>

    <section class="grid-2">
      <section class="card">
        <div class="card-header">
          <div class="title-wrap">
            <h2>Best Result</h2>
            <p>Top experiment snapshot across the current runtime database.</p>
          </div>
        </div>
        <div id="best">Loading...</div>
      </section>
      <section class="card">
        <div class="card-header">
          <div class="title-wrap">
            <h2>Researcher Trace</h2>
            <p>How prior experiment outcomes shape the next direction.</p>
          </div>
        </div>
        <div class="pane trace-card" id="reflections">Loading...</div>
      </section>
    </section>
  </section>

  <section class="tab-panel" data-panel="research">
    <section class="card">
      <div class="card-header">
        <div class="title-wrap">
          <h2>Idea Queue</h2>
          <p>Research output waiting to be engineered or already in progress.</p>
        </div>
        <span class="badge" id="ideaCount">0</span>
      </div>
      <div class="pane" id="ideas">Loading...</div>
    </section>
  </section>

  <section class="tab-panel" data-panel="execution">
    <section class="layout">
      <div class="stack">
        <section class="card">
          <div class="card-header">
            <div class="title-wrap">
              <h2>Experiments</h2>
              <p>Recent alpha simulations with current outcome and key metrics.</p>
            </div>
            <span class="badge warn" id="experimentCount">0</span>
          </div>
          <div class="pane" id="experiments">Loading...</div>
        </section>
      </div>
      <div class="stack">
        <section class="card">
          <div class="card-header">
            <div class="title-wrap">
              <h2>Feedback Loop</h2>
              <p>Compact view of what is working, failing, and repeating.</p>
            </div>
            <span class="badge" id="feedbackCount">0</span>
          </div>
          <div class="pane" id="feedback">Loading...</div>
        </section>
      </div>
    </section>
  </section>

  <section class="tab-panel" data-panel="ops">
    <section class="layout">
      <div class="stack">
        <section class="card">
          <div class="card-header">
            <div class="title-wrap">
              <h2>Event Log</h2>
              <p>Recent state transitions and automation activity.</p>
            </div>
            <span class="badge" id="eventCount">0</span>
          </div>
          <div class="pane" id="events">Loading...</div>
        </section>
      </div>
      <div class="stack">
        <section class="card">
          <div class="card-header">
            <div class="title-wrap">
              <h2>Config Snapshot</h2>
              <p>Live runtime configuration with sensitive fields masked.</p>
            </div>
          </div>
          <pre id="configView" class="code-block">Loading...</pre>
        </section>
        <section class="card">
          <div class="card-header">
            <div class="title-wrap">
              <h2>Recent Log Tail</h2>
              <p>Latest daemon stderr lines for quick diagnosis.</p>
            </div>
          </div>
          <pre id="logView" class="code-block">Loading...</pre>
        </section>
      </div>
    </section>
  </section>
</main>
<script>
const PAGE_SIZES = {
  ideas: 5,
  experiments: 6,
  feedback: 6,
  events: 6,
  reflections: 3,
};

const pageState = {
  ideas: 0,
  experiments: 0,
  feedback: 0,
  events: 0,
  reflections: 0,
};

async function getJSON(path) {
  const response = await fetch(path);
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function clampPage(key, totalItems) {
  const totalPages = Math.max(Math.ceil(totalItems / PAGE_SIZES[key]), 1);
  pageState[key] = Math.min(Math.max(pageState[key], 0), totalPages - 1);
  return totalPages;
}

function renderPager(key, totalItems) {
  const totalPages = clampPage(key, totalItems);
  if (totalPages <= 1) {
    return "";
  }
  const currentPage = pageState[key];
  return `
    <div class="pager">
      <div class="muted">${currentPage + 1}/${totalPages} · ${totalItems} items</div>
      <div class="pager-controls">
        <button data-page-target="${key}" data-page-delta="-1" ${currentPage === 0 ? "disabled" : ""}>Previous</button>
        <button data-page-target="${key}" data-page-delta="1" ${currentPage >= totalPages - 1 ? "disabled" : ""}>Next</button>
      </div>
    </div>
  `;
}

function pagedItems(key, items) {
  const list = items || [];
  clampPage(key, list.length);
  const size = PAGE_SIZES[key];
  const start = pageState[key] * size;
  return list.slice(start, start + size);
}

function statusChip(status) {
  const value = escapeHtml(status || "unknown");
  return `<span class="chip status-chip ${value.toLowerCase()}">${value}</span>`;
}

function renderTrendChart(experiments) {
  const values = (experiments || [])
    .slice()
    .reverse()
    .map((item, index) => ({
      x: index,
      label: item.alpha_name || `exp-${index + 1}`,
      y: Number(item.sharpe || 0),
    }));
  if (!values.length) {
    document.querySelector("#trendChart").innerHTML = `<div class="empty">No experiment trend yet.</div>`;
    return;
  }
  const width = 620;
  const height = 220;
  const padX = 32;
  const padY = 24;
  const ys = values.map(item => item.y);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 1);
  const yRange = Math.max(maxY - minY, 1);
  const xStep = values.length === 1 ? 0 : (width - padX * 2) / (values.length - 1);
  const points = values.map((item, index) => {
    const x = padX + index * xStep;
    const y = height - padY - ((item.y - minY) / yRange) * (height - padY * 2);
    return { ...item, x, y };
  });
  const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  const zeroY = height - padY - ((0 - minY) / yRange) * (height - padY * 2);
  document.querySelector("#trendChart").innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <defs>
        <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="rgba(82,211,200,0.35)" />
          <stop offset="100%" stop-color="rgba(82,211,200,0.03)" />
        </linearGradient>
      </defs>
      <line x1="${padX}" y1="${zeroY.toFixed(1)}" x2="${width - padX}" y2="${zeroY.toFixed(1)}" stroke="rgba(173,194,255,0.18)" stroke-dasharray="4 5" />
      <path d="${path}" fill="none" stroke="#52d3c8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
      ${points.map(point => `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="4.5" fill="#ffb44d" />`).join("")}
    </svg>
    <div class="chart-legend">
      <span class="legend-chip"><span class="legend-dot" style="background:#52d3c8"></span>Sharpe path</span>
      <span class="legend-chip"><span class="legend-dot" style="background:#ffb44d"></span>Latest points</span>
    </div>
  `;
}

function renderMixChart(summary) {
  const ideaStatus = summary.idea_status || {};
  const experimentStatus = summary.experiment_status || {};
  const bars = [
    { label: "Queued", value: ideaStatus.queued || 0, color: "#52d3c8" },
    { label: "Engineering", value: ideaStatus.engineering || 0, color: "#7dd3fc" },
    { label: "Promising", value: experimentStatus.promising || 0, color: "#9ae6b4" },
    { label: "Rejected", value: experimentStatus.rejected || 0, color: "#ff8a65" },
    { label: "Accepted", value: experimentStatus.accepted || 0, color: "#ffd166" },
  ];
  const maxValue = Math.max(...bars.map(item => item.value), 1);
  const width = 620;
  const height = 220;
  const padX = 36;
  const padY = 26;
  const plotWidth = width - padX * 2;
  const plotHeight = height - padY * 2;
  const gap = 18;
  const barWidth = (plotWidth - gap * (bars.length - 1)) / bars.length;
  document.querySelector("#mixChart").innerHTML = `
    <svg class="svg-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      ${bars.map((bar, index) => {
        const h = (bar.value / maxValue) * plotHeight;
        const x = padX + index * (barWidth + gap);
        const y = height - padY - h;
        return `
          <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${h.toFixed(1)}" rx="10" fill="${bar.color}" opacity="0.9" />
          <text x="${(x + barWidth / 2).toFixed(1)}" y="${(y - 8).toFixed(1)}" text-anchor="middle" fill="#93a4bf" font-size="12">${bar.value}</text>
          <text x="${(x + barWidth / 2).toFixed(1)}" y="${(height - 8).toFixed(1)}" text-anchor="middle" fill="#93a4bf" font-size="11">${bar.label}</text>
        `;
      }).join("")}
    </svg>
    <div class="chart-legend">
      ${bars.map(bar => `<span class="legend-chip"><span class="legend-dot" style="background:${bar.color}"></span>${bar.label}</span>`).join("")}
    </div>
  `;
}

function renderHero(summary) {
  const ideaStatus = summary.idea_status || {};
  const queued = ideaStatus.queued || 0;
  const engineering = ideaStatus.engineering || 0;
  const best = summary.best_experiment || {};
  document.querySelector("#heroStats").innerHTML = `
    <div class="hero-stat">
      <div class="label">Ideas In Flight</div>
      <div class="value">${queued + engineering}</div>
      <div class="subvalue">${queued} queued · ${engineering} engineering</div>
    </div>
    <div class="hero-stat">
      <div class="label">Best Sharpe</div>
      <div class="value">${best.sharpe ?? "-"}</div>
      <div class="subvalue">${escapeHtml(best.idea_title || "No completed benchmark yet")}</div>
    </div>
    <div class="hero-stat">
      <div class="label">Latest Accepted</div>
      <div class="value">${summary.latest_accept ? "1" : "0"}</div>
      <div class="subvalue">${escapeHtml((summary.latest_accept || {}).idea_title || "No accepted alpha yet")}</div>
    </div>
    <div class="hero-stat">
      <div class="label">Runtime Shape</div>
      <div class="value">${summary.counts?.events || 0}</div>
      <div class="subvalue">Events recorded across the current daemon session.</div>
    </div>
  `;
}

function renderMetrics(summary) {
  const counts = summary.counts || {};
  const ideaStatus = summary.idea_status || {};
  const experimentStatus = summary.experiment_status || {};
  document.querySelector("#metricSources").textContent = counts.sources || 0;
  document.querySelector("#metricIdeas").textContent = counts.ideas || 0;
  document.querySelector("#metricExperiments").textContent = counts.experiments || 0;
  document.querySelector("#metricEvents").textContent = counts.events || 0;
  document.querySelector("#metricIdeasFoot").textContent = `${ideaStatus.queued || 0} queued, ${ideaStatus.engineering || 0} engineering, ${ideaStatus.tested || 0} tested`;
  document.querySelector("#metricExperimentsFoot").textContent = `${experimentStatus.promising || 0} promising, ${experimentStatus.rejected || 0} rejected, ${experimentStatus.accepted || 0} accepted`;
}

function renderAgents(items) {
  const html = (items || []).map(item => `
    <div class="item">
      <div class="item-top">
        <div>
          <div class="item-title">${escapeHtml(item.agent_name)}</div>
          <div class="muted">${escapeHtml(item.summary || "No summary")}</div>
        </div>
        ${statusChip(item.state)}
      </div>
      <div class="item-meta">
        <span class="chip mono">${escapeHtml(item.last_heartbeat || "")}</span>
      </div>
    </div>
  `).join("");
  document.querySelector("#agents").innerHTML = html || `<div class="empty">No agent heartbeat yet.</div>`;
  document.querySelector("#agentCount").textContent = (items || []).length;
}

function renderBest(item) {
  if (!item) {
    document.querySelector("#best").innerHTML = `<div class="empty">No completed experiment yet.</div>`;
    return;
  }
  document.querySelector("#best").innerHTML = `
    <div class="key-row"><span class="muted">Idea</span><strong>${escapeHtml(item.idea_title || "-")}</strong></div>
    <div class="key-row"><span class="muted">Alpha</span><strong>${escapeHtml(item.alpha_name || "-")}</strong></div>
    <div class="key-row"><span class="muted">Sharpe</span><strong>${item.sharpe ?? "-"}</strong></div>
    <div class="key-row"><span class="muted">Fitness</span><strong>${item.fitness ?? "-"}</strong></div>
    <div class="key-row"><span class="muted">Turnover</span><strong>${item.turnover ?? "-"}</strong></div>
    <div class="key-row"><span class="muted">Status</span>${statusChip(item.status)}</div>
  `;
}

function renderIdeas(items) {
  const list = items || [];
  const visible = pagedItems("ideas", list);
  const content = visible.map(item => `
    <div class="item">
      <div class="item-top">
        <div>
          <div class="item-title">${escapeHtml(item.title)}</div>
          <div class="item-summary">${escapeHtml(item.summary)}</div>
        </div>
        ${statusChip(item.status)}
      </div>
      <div class="item-meta">
        <span class="chip">priority ${escapeHtml(item.priority)}</span>
        <span class="chip">${escapeHtml(item.source_kind || "unknown source")}</span>
        <span class="chip">${escapeHtml(item.source_title || "no source title")}</span>
        <span class="chip mono">${escapeHtml((item.created_at || "").replace("T", " ").replace("+00:00", " UTC"))}</span>
      </div>
      <div class="idea-detail-grid">
        <div class="idea-detail">
          <strong>Research Direction</strong>
          <p>${escapeHtml(item.rationale || "No rationale recorded.")}</p>
        </div>
      </div>
    </div>
  `).join("");
  document.querySelector("#ideas").innerHTML = list.length
    ? `<div class="list scroll">${content}</div>${renderPager("ideas", list.length)}`
    : `<div class="empty">No ideas in the queue right now.</div>`;
  document.querySelector("#ideaCount").textContent = list.length;
}

function renderReflections(items) {
  const list = items || [];
  const visible = pagedItems("reflections", list);
  const content = visible.map(item => {
    const payload = item.payload || {};
    const observations = payload.observations || {};
    const failures = payload.failure_patterns || [];
    const directions = payload.improvement_directions || [];
    const sources = payload.source_focus || [];
    const queuedTitles = payload.queued_titles || [];
    const experimentRefs = payload.experiment_refs || [];
    const discardedMotifs = payload.discarded_motifs || [];
    const adjustmentMap = payload.adjustment_map || [];
    const lineageEntries = payload.queued_idea_lineage || [];
    return `
      <div class="reflection-card">
        <div class="item-top">
          <h3>${escapeHtml(item.message || "Research reflection")}</h3>
          <span class="chip mono">${escapeHtml(item.created_at || "")}</span>
        </div>
        <div class="item-meta">
          <span class="chip">Avg Sharpe ${escapeHtml(observations.average_sharpe ?? "-")}</span>
          <span class="chip">Avg Turnover ${escapeHtml(observations.average_turnover ?? "-")}</span>
          <span class="chip">Queued ${escapeHtml(observations.queued_idea_count ?? "-")}</span>
        </div>
        <div class="reflection-grid">
          <div class="reflection-block">
            <strong>Failure Patterns</strong>
            <ul class="bullet-list">${failures.map(item => `<li>${escapeHtml(item)}</li>`).join("") || "<li>No failure pattern recorded.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Improvement Directions</strong>
            <ul class="bullet-list">${directions.map(item => `<li>${escapeHtml(item)}</li>`).join("") || "<li>No next-step guidance recorded.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Source Focus</strong>
            <ul class="bullet-list">${sources.map(item => `<li>${escapeHtml(item)}</li>`).join("") || "<li>No source focus recorded.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Status Breakdown</strong>
            <ul class="bullet-list">${Object.entries(observations.status_breakdown || {}).map(([key, value]) => `<li>${escapeHtml(key)}: ${escapeHtml(value)}</li>`).join("") || "<li>No experiment status data.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Referenced Experiments</strong>
            <ul class="bullet-list">${experimentRefs.map(ref => `<li>${escapeHtml(ref.alpha_name || "unknown alpha")} · ${escapeHtml(ref.status)} · Sharpe ${escapeHtml(ref.sharpe ?? "-")}</li>`).join("") || "<li>No referenced experiments.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Discarded Motifs</strong>
            <ul class="bullet-list">${discardedMotifs.map(entry => `<li>${escapeHtml(entry.motif)}: ${escapeHtml(entry.reason)}</li>`).join("") || "<li>No motifs explicitly discarded.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Observation To Response</strong>
            <ul class="bullet-list">${adjustmentMap.map(entry => `<li>${escapeHtml(entry.observation)} -> ${escapeHtml(entry.response)}</li>`).join("") || "<li>No adjustment map recorded.</li>"}</ul>
          </div>
          <div class="reflection-block">
            <strong>Queued Follow-ups</strong>
            <ul class="bullet-list">${queuedTitles.map(entry => `<li>${escapeHtml(entry)}</li>`).join("") || "<li>No follow-up ideas recorded.</li>"}</ul>
          </div>
        </div>
        <div class="reflection-block" style="margin-top:12px;">
          <strong>Idea Lineage</strong>
          <ul class="bullet-list">${lineageEntries.map(entry => `<li><strong>${escapeHtml(entry.idea_title)}</strong> <- ${escapeHtml(entry.parent_alpha || "no parent alpha")} (${escapeHtml(entry.parent_status || "n/a")}, Sharpe ${escapeHtml(entry.parent_sharpe ?? "-")}) -> ${escapeHtml(entry.adjustment || "no adjustment recorded")} | source: ${escapeHtml(entry.source_title || "n/a")}</li>`).join("") || "<li>No lineage recorded.</li>"}</ul>
        </div>
      </div>
    `;
  }).join("");
  document.querySelector("#reflections").innerHTML = list.length
    ? `<div class="reflection-list">${content}</div>${renderPager("reflections", list.length)}`
    : `<div class="empty">Researcher has not logged a reflection yet.</div>`;
}

function renderExperiments(items) {
  const list = items || [];
  const visible = pagedItems("experiments", list);
  const content = visible.map(item => `
    <div class="item">
      <div class="item-top">
        <div>
          <div class="item-title">${escapeHtml(item.idea_title || "Untitled idea")}</div>
          <div class="muted mono">${escapeHtml(item.alpha_name || "")}</div>
        </div>
        ${statusChip(item.status)}
      </div>
      <div class="item-meta">
        <span class="chip">Sharpe ${escapeHtml(item.sharpe ?? "-")}</span>
        <span class="chip">Fitness ${escapeHtml(item.fitness ?? "-")}</span>
        <span class="chip">Turnover ${escapeHtml(item.turnover ?? "-")}</span>
      </div>
    </div>
  `).join("");
  document.querySelector("#experiments").innerHTML = list.length
    ? `<div class="list scroll">${content}</div>${renderPager("experiments", list.length)}`
    : `<div class="empty">No experiments yet.</div>`;
  document.querySelector("#experimentCount").textContent = list.length;
}

function renderFeedback(items) {
  const list = items || [];
  const visible = pagedItems("feedback", list);
  const content = visible.map(item => `
    <div class="item">
      <div class="item-top">
        <div>
          <div class="item-title">${escapeHtml(item.idea_title || "")}</div>
          <div class="muted mono">${escapeHtml(item.alpha_name || "")}</div>
        </div>
        ${statusChip(item.status)}
      </div>
      <div class="item-meta">
        <span class="chip">Sharpe ${escapeHtml(item.sharpe ?? "-")}</span>
        <span class="chip">Fitness ${escapeHtml(item.fitness ?? "-")}</span>
        <span class="chip">Turnover ${escapeHtml(item.turnover ?? "-")}</span>
        <span class="chip">Returns ${escapeHtml(item.returns ?? "-")}</span>
      </div>
    </div>
  `).join("");
  document.querySelector("#feedback").innerHTML = list.length
    ? `<div class="list scroll">${content}</div>${renderPager("feedback", list.length)}`
    : `<div class="empty">No feedback records yet.</div>`;
  document.querySelector("#feedbackCount").textContent = list.length;
}

function renderEvents(items) {
  const list = items || [];
  const visible = pagedItems("events", list);
  const content = visible.map(item => `
    <div class="item">
      <div class="item-top">
        <div>
          <div class="item-title">${escapeHtml(item.kind)}</div>
          <div class="muted">${escapeHtml(item.message)}</div>
        </div>
        <span class="chip mono">${escapeHtml(item.created_at || "")}</span>
      </div>
    </div>
  `).join("");
  document.querySelector("#events").innerHTML = list.length
    ? `<div class="list scroll">${content}</div>${renderPager("events", list.length)}`
    : `<div class="empty">No events yet.</div>`;
  document.querySelector("#eventCount").textContent = list.length;
}

function renderConfig(snapshot) {
  document.querySelector("#configView").textContent = JSON.stringify(snapshot, null, 2);
}

function renderLogs(payload) {
  document.querySelector("#logView").textContent = (payload || {}).tail || "No log lines";
}

function setActiveTab(name) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === name);
  });
}

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest("[data-tab]");
  if (tabButton) {
    setActiveTab(tabButton.dataset.tab);
    return;
  }
  const button = event.target.closest("[data-page-target]");
  if (!button) {
    return;
  }
  const key = button.dataset.pageTarget;
  const delta = Number(button.dataset.pageDelta || 0);
  pageState[key] = Math.max(0, pageState[key] + delta);
  refresh();
});

async function refresh() {
  const [summary, ideas, experiments, feedback, events, reflections, config, logs] = await Promise.all([
    getJSON("/api/summary"),
    getJSON("/api/ideas"),
    getJSON("/api/experiments"),
    getJSON("/api/feedback"),
    getJSON("/api/events"),
    getJSON("/api/reflections"),
    getJSON("/api/config"),
    getJSON("/api/logs"),
  ]);
  renderHero(summary);
  renderMetrics(summary);
  renderTrendChart(experiments);
  renderMixChart(summary);
  renderAgents(summary.agents || []);
  renderBest(summary.best_experiment);
  renderIdeas(ideas);
  renderExperiments(experiments);
  renderFeedback(feedback);
  renderEvents(events);
  renderReflections(reflections);
  renderConfig(config);
  renderLogs(logs);
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
            config_path=self.config_path,
            log_path=self.log_dir / "wqa.err.log",
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
        self.store.set_agent_status("researcher", "running", "collecting sources")
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
        self.store.set_agent_status("researcher", "running", f"generating ideas from {len(recent_sources)} sources")
        ideas = self._generate_research_ideas(provider, recent_sources, recent_experiments, batch_size)
        reflection = self._build_research_reflection(recent_sources, recent_experiments, ideas)
        inserted_ideas = self.store.add_ideas(ideas)
        for idea in ideas[:inserted_ideas]:
            self.store.add_event(
                level="info",
                kind="idea_queue",
                message=f"queued idea: {idea['title']}",
                payload={"title": idea["title"], "source_title": idea.get("source_title"), "source_kind": idea.get("source_kind")},
            )
        self.store.add_event(
            level="info",
            kind="researcher",
            message=f"researcher created {inserted_ideas} ideas from {len(recent_sources)} recent sources",
            payload={"new_sources": inserted, "ideas": inserted_ideas},
        )
        self.store.add_event(
            level="info",
            kind="researcher_reflection",
            message=reflection["headline"],
            payload=reflection,
        )
        return f"new_sources={inserted}, queued_ideas={inserted_ideas}"

    def _build_research_reflection(
        self,
        recent_sources: List[Dict[str, Any]],
        recent_experiments: List[Dict[str, Any]],
        queued_ideas: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        statuses: Dict[str, int] = {}
        sharpes: List[float] = []
        turnovers: List[float] = []
        for experiment in recent_experiments:
            status = str(experiment.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            sharpe = experiment.get("sharpe")
            turnover = experiment.get("turnover")
            if isinstance(sharpe, (int, float)):
                sharpes.append(float(sharpe))
            if isinstance(turnover, (int, float)):
                turnovers.append(float(turnover))

        avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
        avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0
        source_focus = [source.get("title", "")[:72] for source in recent_sources[:3] if source.get("title")]
        queued_titles = [idea.get("title", "")[:72] for idea in queued_ideas[:3] if idea.get("title")]
        experiment_refs = [
            {
                "idea_title": str(experiment.get("idea_title", ""))[:72],
                "alpha_name": str(experiment.get("alpha_name", ""))[:48],
                "status": str(experiment.get("status", "unknown")),
                "sharpe": experiment.get("sharpe"),
                "turnover": experiment.get("turnover"),
            }
            for experiment in recent_experiments[:4]
        ]
        rejected_alpha_names = [
            str(experiment.get("alpha_name", "")).strip()
            for experiment in recent_experiments
            if experiment.get("status") == "rejected" and experiment.get("alpha_name")
        ]
        unique_rejected_alpha_names = list(dict.fromkeys(rejected_alpha_names))[:4]

        failures = []
        if statuses.get("rejected", 0):
            failures.append(f"{statuses['rejected']} recent experiments were rejected")
        if avg_turnover > 0.7:
            failures.append("turnover is running too high relative to the current threshold")
        if avg_sharpe <= 0.5 and sharpes:
            failures.append("recent Sharpe profile is weak and needs cleaner signal structure")
        if not failures:
            failures.append("recent batch is small, so the next cycle should probe diversified motifs")

        directions = []
        if avg_turnover > 0.7:
            directions.append("favor slower decay windows and stronger neutralization to compress turnover")
        if avg_sharpe <= 0.5:
            directions.append("bias the next ideas toward simpler cross-sectional expressions with clearer economic anchors")
        if source_focus:
            directions.append(f"lean on the newest source set led by: {source_focus[0]}")
        if not directions:
            directions.append("extend the strongest recent motif into nearby parameter neighborhoods")

        adjustment_map = []
        if statuses.get("rejected", 0):
            adjustment_map.append(
                {
                    "observation": "Most recent candidates were rejected before reaching reviewer.",
                    "response": "Narrow the next idea set toward simpler expressions with clearer economic justification.",
                }
            )
        if avg_turnover > 0.7:
            adjustment_map.append(
                {
                    "observation": "Turnover pressure is too high.",
                    "response": "Prefer slower windows, stronger neutralization, and lower-churn motifs.",
                }
            )
        if avg_sharpe <= 0.5 and sharpes:
            adjustment_map.append(
                {
                    "observation": "Average Sharpe in the recent sample is weak.",
                    "response": "Shift the next batch toward higher-signal, lower-complexity ideas anchored to the newest sources.",
                }
            )
        if not adjustment_map:
            adjustment_map.append(
                {
                    "observation": "Recent sample is too small for a strong conclusion.",
                    "response": "Probe adjacent parameter neighborhoods instead of making a hard directional pivot.",
                }
            )

        discarded_motifs = []
        for name in unique_rejected_alpha_names:
            discarded_motifs.append(
                {
                    "motif": name,
                    "reason": "recent runs did not clear the acceptance bar",
                }
            )
        if avg_turnover > 0.7:
            discarded_motifs.append(
                {
                    "motif": "high-turnover variants",
                    "reason": "turnover is already above the preferred operating range",
                }
            )

        lineage_entries = []
        for index, idea in enumerate(queued_ideas[:4]):
            parent_experiment = experiment_refs[index % len(experiment_refs)] if experiment_refs else {}
            applied_adjustment = adjustment_map[index % len(adjustment_map)] if adjustment_map else {}
            lineage_entries.append(
                {
                    "idea_title": str(idea.get("title", ""))[:80],
                    "idea_summary": str(idea.get("summary", ""))[:140],
                    "source_title": str(idea.get("source_title", "") or idea.get("source_kind", ""))[:80],
                    "rationale": str(idea.get("rationale", ""))[:160],
                    "parent_alpha": parent_experiment.get("alpha_name"),
                    "parent_status": parent_experiment.get("status"),
                    "parent_sharpe": parent_experiment.get("sharpe"),
                    "adjustment": applied_adjustment.get("response"),
                }
            )

        headline = (
            f"Researcher reviewed {len(recent_experiments)} recent experiments and queued "
            f"{len(queued_ideas)} follow-up ideas."
        )
        return {
            "headline": headline,
            "observations": {
                "recent_experiment_count": len(recent_experiments),
                "recent_source_count": len(recent_sources),
                "queued_idea_count": len(queued_ideas),
                "average_sharpe": round(avg_sharpe, 3),
                "average_turnover": round(avg_turnover, 3),
                "status_breakdown": statuses,
            },
            "failure_patterns": failures,
            "improvement_directions": directions,
            "source_focus": source_focus,
            "queued_titles": queued_titles,
            "experiment_refs": experiment_refs,
            "discarded_motifs": discarded_motifs,
            "adjustment_map": adjustment_map,
            "queued_idea_lineage": lineage_entries,
        }

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
        for index, idea in enumerate(claimed_ideas, start=1):
            self.store.set_agent_status(
                "engineer",
                "running",
                f"idea {index}/{len(claimed_ideas)}: {idea['title'][:64]}",
            )
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
            self.store.set_agent_status(
                "engineer",
                "running",
                f"simulating {len(alpha_candidates)} alphas for idea {index}/{len(claimed_ideas)}",
            )
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
        for index, experiment in enumerate(experiments, start=1):
            self.store.set_agent_status(
                "reviewer",
                "running",
                f"reviewing experiment {index}/{len(experiments)}: {experiment.get('alpha_name') or experiment['idea_title'][:48]}",
            )
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
