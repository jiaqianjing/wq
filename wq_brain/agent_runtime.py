"""
Multi-agent runtime for quant idea research, implementation, and review.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .alpha_generator import AlphaGenerator
from .alpha_submitter import AlphaSubmitter, SubmissionCriteria
from .client import Region, SubmissionResult, Universe, WorldQuantBrainClient
from .learning import AlphaAnalyzer, AlphaDatabase, SmartGenerator
from .refinement_policy import (
    refinement_problem,
    refinement_strategy_lines,
)
from .source_collector import SourceCollector, SourceItem

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

VALID_IDEA_STATUSES = (
    "queued",
    "engineering",
    "tested",
    "reviewing",
    "accepted",
    "blocked",
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


def compact_submission_reason(reason: Any) -> str:
    text = " ".join(str(reason or "submit failed").split())
    return text[:200]


def is_worldquant_submission_block(result: SubmissionResult) -> bool:
    check_result = result.check_result
    if check_result is None:
        return False
    return (not check_result.ok) or bool(check_result.failed_checks) or bool(check_result.pending_checks)


DEFAULT_CONFIG_TEMPLATE = """# wqa: WorldQuant Agent Lab
# 运行前建议：
# 1. 填入 WorldQuant Brain 账号
# 2. 至少选择一个 LLM profile（gemini、kimi、siliconflow 或 anthropic）
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
  siliconflow:
    provider: siliconflow
    model_name: deepseek-ai/DeepSeek-V3
    api_key: ${SILICONFLOW_API_KEY}
    base_url: https://api.siliconflow.cn/v1
  anthropic:
    provider: anthropic
    model_name: claude-opus-4-20250514
    api_key: ${ANTHROPIC_API_KEY}
    base_url: https://api.anthropic.com

agents:
  researcher:
    enabled: true
    interval_seconds: 900
    llm_profile: gemini
    idea_batch_size: 4
    max_queued_ideas: 20
  engineer:
    enabled: true
    interval_seconds: 300
    llm_profile: kimi
    alpha_batch_size: 4
  reviewer:
    enabled: true
    interval_seconds: 180
    llm_profile: kimi
  analyst:
    enabled: true
    interval_seconds: 1800
    llm_profile: anthropic
    review_every_n_experiments: 20

integrations:
  worldquant:
    username: ${WQB_USERNAME}
    password: ${WQB_PASSWORD}
    disable_proxy: false
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
      timeout_seconds: 15
      user_agent: wqa-source-collector/1.0
      honor_retry_after: true
      rate_limit_cooldown_seconds: 1800
  reports: []
  market: []
"""


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
                llm_model TEXT,
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
                llm_model TEXT,
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
        # Migrate existing databases: add llm_model columns if missing
        existing_idea_cols = {row[1] for row in conn.execute("PRAGMA table_info(ideas)").fetchall()}
        if "llm_model" not in existing_idea_cols:
            conn.execute("ALTER TABLE ideas ADD COLUMN llm_model TEXT")
            conn.commit()
        existing_exp_cols = {row[1] for row in conn.execute("PRAGMA table_info(experiments)").fetchall()}
        if "llm_model" not in existing_exp_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN llm_model TEXT")
            conn.commit()
        purged_ideas = self._purge_legacy_ideas(conn)
        if purged_ideas:
            logger.info("purged %d ideas with unsupported legacy statuses", purged_ideas)
            conn.commit()
        conn.close()
        self._refresh_cache()

    def _purge_legacy_ideas(self, conn: sqlite3.Connection) -> int:
        placeholders = ", ".join("?" for _ in VALID_IDEA_STATUSES)
        rows = conn.execute(
            f"SELECT id FROM ideas WHERE status NOT IN ({placeholders})",
            VALID_IDEA_STATUSES,
        ).fetchall()
        if not rows:
            return 0
        idea_ids = tuple(int(row[0]) for row in rows)
        id_placeholders = ", ".join("?" for _ in idea_ids)
        conn.execute(
            f"DELETE FROM experiments WHERE idea_id IN ({id_placeholders})",
            idea_ids,
        )
        conn.execute(
            f"DELETE FROM ideas WHERE id IN ({id_placeholders})",
            idea_ids,
        )
        return len(idea_ids)

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

    @staticmethod
    def _safe_priority(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            mapping = {"low": 25, "medium": 50, "high": 75, "critical": 90}
            return mapping.get(str(value).strip().lower(), 50)

    def add_ideas(self, ideas: List[Dict[str, Any]]) -> int:
        conn = self._connect()
        cur = conn.cursor()
        inserted = 0
        now = utc_now()
        for idea in ideas:
            # Deduplicate only ideas that are still waiting in the queue.
            existing = cur.execute(
                """
                SELECT id FROM ideas
                WHERE title = ? AND summary = ? AND status = 'queued'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (idea["title"], idea.get("summary", "")),
            ).fetchone()
            if existing:
                continue
            cur.execute(
                """
                INSERT INTO ideas (
                    title, summary, rationale, source_kind, source_title, source_url,
                    status, priority, agent_notes, llm_model, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idea["title"],
                    idea["summary"],
                    idea.get("rationale", ""),
                    idea.get("source_kind"),
                    idea.get("source_title"),
                    idea.get("source_url"),
                    idea.get("status", "queued"),
                    self._safe_priority(idea.get("priority", 50)),
                    idea.get("agent_notes", ""),
                    idea.get("llm_model"),
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
        # Recover ideas stuck in 'engineering' for over 30 minutes
        stale_cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        cur.execute(
            "UPDATE ideas SET status = 'queued', updated_at = ? WHERE status = 'engineering' AND updated_at < ?",
            (utc_now(), stale_cutoff.isoformat()),
        )
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
                submitted, submission_result, llm_model, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                payload.get("llm_model"),
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
        # Recover experiments stuck in 'reviewing' for over 30 minutes
        stale_cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        cur.execute(
            "UPDATE experiments SET status = 'promising', updated_at = ? WHERE status = 'reviewing' AND updated_at < ?",
            (utc_now(), stale_cutoff.isoformat()),
        )
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

    def queued_idea_count(self) -> int:
        conn = self._connect()
        count = conn.execute("SELECT COUNT(*) FROM ideas WHERE status = 'queued'").fetchone()[0]
        conn.close()
        return count

    def experiment_count_since(self, since_event_kind: str) -> int:
        """Count experiments created after the latest event of the given kind."""
        conn = self._connect()
        last_event = conn.execute(
            "SELECT created_at FROM events WHERE kind = ? ORDER BY created_at DESC LIMIT 1",
            (since_event_kind,),
        ).fetchone()
        if last_event:
            count = conn.execute(
                "SELECT COUNT(*) FROM experiments WHERE created_at > ?",
                (last_event["created_at"],),
            ).fetchone()[0]
        else:
            count = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        conn.close()
        return count

    def latest_analyst_strategy(self) -> Optional[Dict[str, Any]]:
        """Return the most recent analyst strategy payload, or None."""
        conn = self._connect()
        row = conn.execute(
            "SELECT payload_json FROM events WHERE kind = 'analyst_strategy' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    def all_experiments(self) -> List[Dict[str, Any]]:
        """Return all experiments with idea titles for analyst deep review."""
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT e.*, i.title AS idea_title
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            ORDER BY e.created_at ASC
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def model_stats(self) -> List[Dict[str, Any]]:
        """Aggregate idea/experiment performance grouped by llm_model."""
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT
                COALESCE(i.llm_model, 'unknown') AS model,
                COUNT(DISTINCT i.id) AS idea_count,
                COUNT(e.id) AS experiment_count,
                SUM(CASE WHEN e.status IN ('promising','accepted','submitted') THEN 1 ELSE 0 END) AS promising_count,
                SUM(CASE WHEN e.status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                ROUND(AVG(e.sharpe), 3) AS avg_sharpe,
                ROUND(AVG(e.fitness), 3) AS avg_fitness,
                ROUND(AVG(e.turnover), 3) AS avg_turnover,
                ROUND(MAX(e.sharpe), 3) AS best_sharpe
            FROM ideas i
            LEFT JOIN experiments e ON e.idea_id = i.id
            GROUP BY COALESCE(i.llm_model, 'unknown')
            ORDER BY avg_sharpe DESC
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]


class BaseLLMProvider:
    provider_name = "unknown"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class DisabledLLMProvider(BaseLLMProvider):
    provider_name = "disabled"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("LLM provider is not configured")


ANTHROPIC_MODEL_ALIASES = {
    # Anthropic expects snapshot model IDs for Opus 4.
    "claude-opus-4": "claude-opus-4-20250514",
}

FASTEXPR_OPERATOR_ALIASES = {
    "ts_std": "ts_std_dev",
}


def normalize_anthropic_model_name(model_name: str) -> str:
    return ANTHROPIC_MODEL_ALIASES.get(model_name.strip(), model_name.strip())


def normalize_fastexpr_operators(expression: str) -> str:
    normalized = expression or ""
    for alias, canonical in FASTEXPR_OPERATOR_ALIASES.items():
        normalized = re.sub(rf"\b{re.escape(alias)}(?=\s*\()", canonical, normalized)
    return normalized


def format_http_error(response: requests.Response, service_name: str) -> str:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("error") or "").strip()
        else:
            detail = str(error).strip()
    if not detail:
        detail = response.text.strip()
    detail = " ".join(detail.split())
    if detail:
        return f"{service_name} API error HTTP {response.status_code} for {response.url}: {detail[:300]}"
    return f"{service_name} API error HTTP {response.status_code} for {response.url}"


class GeminiProvider(BaseLLMProvider):
    provider_name = "gemini"

    def __init__(self, model_name: str, api_key: str):
        self.model_name = model_name
        self.api_key = api_key

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model_name)}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                },
                timeout=120,
            )
            if response.status_code == 429 and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1) * 15  # 30s, 60s
                logger.warning("Gemini 429 rate limited, retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(part.get("text", "") for part in parts if part.get("text"))


class KimiProvider(BaseLLMProvider):
    provider_name = "kimi"

    def __init__(self, model_name: str, api_key: str, base_url: str):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if "k2" not in self.model_name:
            body["temperature"] = 0.4
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


class SiliconFlowProvider(BaseLLMProvider):
    provider_name = "siliconflow"

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
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"]


class AnthropicProvider(BaseLLMProvider):
    provider_name = "anthropic"

    def __init__(self, model_name: str, api_key: str, base_url: str):
        self.model_name = normalize_anthropic_model_name(model_name)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=180,
            )
            if response.status_code == 429 and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1) * 15
                logger.warning("Anthropic 429 rate limited, retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                raise RuntimeError(format_http_error(response, "Anthropic"))
            break
        payload = response.json()
        content = payload.get("content", [])
        return "\n".join(block.get("text", "") for block in content if block.get("type") == "text")


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
    if provider == "siliconflow":
        return SiliconFlowProvider(
            model_name=model_name,
            api_key=api_key,
            base_url=profile.get("base_url", "https://api.siliconflow.cn/v1"),
        )
    if provider == "anthropic":
        return AnthropicProvider(
            model_name=model_name,
            api_key=api_key,
            base_url=profile.get("base_url", "https://api.anthropic.com"),
        )
    return DisabledLLMProvider()


def describe_llm_profile(config: Dict[str, Any], profile_name: Optional[str], provider: BaseLLMProvider) -> Dict[str, str]:
    providers = config.get("providers", {})
    profile = providers.get(profile_name or "", {})
    return {
        "profile": profile_name or "disabled",
        "provider": getattr(provider, "provider_name", "unknown"),
        "model_name": str(getattr(provider, "model_name", "") or profile.get("model_name", "")),
        "base_url": str(profile.get("base_url", "") or getattr(provider, "base_url", "")),
    }


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


class AgentRuntime:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = load_yaml_config(config_path)
        self.state_dir = ensure_directory(Path(self.config["app"]["state_dir"]).resolve())
        self.db_dir = ensure_directory(self.state_dir / "db")
        self.log_dir = ensure_directory(self.state_dir / "logs")
        self.results_dir = ensure_directory(self.state_dir / "results")
        self._migrate_legacy_layout()
        self.runtime_db = self.db_dir / "runtime.db"
        self.pid_file = self.state_dir / "wqa.pid"
        self.meta_file = self.state_dir / "runtime.json"
        self.store = RuntimeStore(self.runtime_db)
        self.stop_event = threading.Event()
        from wq_brain.dashboard import DashboardServer
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
        self.learning_db = AlphaDatabase(str(self.db_dir / "alpha_history.db"))
        self._brain_knowledge = self._load_brain_knowledge()

    def _migrate_legacy_layout(self) -> None:
        """Move files from flat .wqa/ to the new subdirectory layout."""
        import shutil
        moves = [
            (self.state_dir / "runtime.db", self.db_dir / "runtime.db"),
            (self.state_dir / "alpha_history.db", self.db_dir / "alpha_history.db"),
            (self.state_dir / "submission_checks.jsonl", self.log_dir / "submission_checks.jsonl"),
        ]
        for src, dst in moves:
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))
                logger.info("migrated %s → %s", src.name, dst)
        # Move submission_progress_*.json and report_*.txt to results/
        for pattern in ("submission_progress_*.json", "report_*.txt"):
            for f in self.state_dir.glob(pattern):
                dst = self.results_dir / f.name
                if not dst.exists():
                    shutil.move(str(f), str(dst))
        # Move legacy state.db if present
        legacy_state = self.state_dir / "state.db"
        if legacy_state.exists() and not (self.db_dir / "runtime.db").exists():
            shutil.move(str(legacy_state), str(self.db_dir / "runtime.db"))
            logger.info("migrated state.db → db/runtime.db")

    def _load_brain_knowledge(self) -> Dict[str, Any]:
        """Load curated BRAIN platform knowledge if available."""
        kb_path = self.state_dir / "brain_knowledge.yaml"
        if not kb_path.exists():
            return {}
        try:
            return load_yaml_config(kb_path)
        except Exception:
            return {}

    def _brain_knowledge_prompt(self, section: str) -> str:
        """Format a knowledge section for prompt injection."""
        data = self._brain_knowledge.get(section)
        if not data:
            return ""
        if isinstance(data, list):
            lines = []
            for item in data[:8]:
                if isinstance(item, dict):
                    lines.append(
                        f"  - {item.get('expression', item.get('name', ''))}"
                        + (f"  ({item.get('performance', '')})" if item.get('performance') else "")
                        + (f"\n    Insight: {item['insight']}" if item.get('insight') else "")
                    )
                else:
                    lines.append(f"  - {item}")
            return "\n".join(lines)
        if isinstance(data, dict):
            lines = []
            for key, values in list(data.items())[:6]:
                if isinstance(values, list):
                    for v in values[:3]:
                        lines.append(f"  - [{key}] {v}")
            return "\n".join(lines)
        return str(data)

    def _account_profile_prompt(self) -> str:
        """Format account profile for prompt injection."""
        ap = self._brain_knowledge.get("account_profile")
        if not ap:
            return ""
        lines = ["ACCOUNT PROFILE:"]
        lines.append(f"  Level: {ap.get('genius_level', 'unknown')}, Super permitted: {ap.get('super_permitted', False)}")
        lines.append(f"  Regions: {', '.join(ap.get('available_regions', []))}")
        delays = ap.get("available_delays", {})
        if delays:
            lines.append(f"  Delays: {', '.join(f'{r}: d{d}' for r, ds in delays.items() for d in ds)}")
        checks = ap.get("real_submission_checks", {})
        if checks:
            lines.append("  REAL WQ submission thresholds (from platform):")
            for name, limit in checks.items():
                lines.append(f"    {name}: {limit}")
        return "\n".join(lines)

    def _worldquant_client(self) -> Optional[WorldQuantBrainClient]:
        wq_config = self.config.get("integrations", {}).get("worldquant", {})
        username = wq_config.get("username", "")
        password = wq_config.get("password", "")
        if not username or not password:
            return None
        client = WorldQuantBrainClient(
            username,
            password,
            disable_proxy=bool(wq_config.get("disable_proxy", False)),
        )
        if not client.authenticate():
            raise RuntimeError("WorldQuant authentication failed")
        client.submission_log_path = str(self.log_dir / "submission_checks.jsonl")
        return client

    def _criteria(self) -> SubmissionCriteria:
        real = self._brain_knowledge.get("account_profile", {}).get("real_submission_checks", {})
        return SubmissionCriteria(
            min_sharpe=float(real.get("LOW_SHARPE", 1.25)),
            min_fitness=float(real.get("LOW_FITNESS", 0.7)),
            max_turnover=float(real.get("HIGH_TURNOVER", 0.7)),
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
        for agent_name in ("researcher", "engineer", "reviewer", "analyst"):
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
                elif agent_name == "analyst":
                    summary = self.run_analyst_cycle()
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
            # If queue is full, back off to full interval
            if summary.startswith("queue full"):
                return interval_seconds
            # If queue is low, produce faster (1/3 of normal interval)
            max_queued = int(
                self.config.get("agents", {}).get("researcher", {}).get("max_queued_ideas", 20)
            )
            current_queued = self.store.queued_idea_count()
            if current_queued < max_queued // 2:
                return max(interval_seconds // 3 - int(elapsed), 1)
            return max(interval_seconds - int(elapsed), 1)

        if summary in {"no queued ideas", "no promising experiments"} or summary.startswith("waiting"):
            # Back off to full interval instead of spinning
            return interval_seconds

        return max(interval_seconds - int(elapsed), 1)

    def run_researcher_cycle(self) -> str:
        max_queued = int(
            self.config.get("agents", {}).get("researcher", {}).get("max_queued_ideas", 20)
        )
        current_queued = self.store.queued_idea_count()
        if current_queued >= max_queued:
            return f"queue full ({current_queued}/{max_queued}), skipping"
        self.store.set_agent_status("researcher", "running", "collecting sources")
        items = self.collector.collect()
        inserted = self.store.add_source_items(items)
        recent_sources = self.store.recent_sources(limit=8)
        recent_experiments = self.store.list_recent_experiments(limit=8)
        profile_name = self.config.get("agents", {}).get("researcher", {}).get("llm_profile")
        provider = create_llm_provider(self.config, profile_name)
        provider_info = describe_llm_profile(self.config, profile_name, provider)
        batch_size = int(
            self.config.get("agents", {}).get("researcher", {}).get("idea_batch_size", 4)
        )
        self.store.set_agent_status("researcher", "running", f"generating ideas from {len(recent_sources)} sources")
        self.store.add_event(
            level="info",
            kind="researcher",
            message=(
                "researcher using llm profile "
                f"{provider_info['profile']} ({provider_info['provider']}) model={provider_info['model_name']}"
            ),
            payload=provider_info,
        )
        model_label = f"{provider_info.get('provider', '')}:{provider_info.get('model_name', '')}".strip(":")
        ideas = self._generate_research_ideas(provider, recent_sources, recent_experiments, batch_size, model_label)
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
        model_label: str = "",
    ) -> List[Dict[str, Any]]:
        sources_text = json.dumps(recent_sources[:8], ensure_ascii=False, indent=2)
        criteria = self._criteria()

        # Compact experiment digest: only fields the LLM needs
        exp_digest = [
            {
                "name": e.get("alpha_name"),
                "expr": e.get("alpha_expression"),
                "sharpe": e.get("sharpe"),
                "fitness": e.get("fitness"),
                "turnover": e.get("turnover"),
                "status": e.get("status"),
            }
            for e in recent_experiments[:8]
        ]

        # Inject the last reflection so the learning loop actually closes
        last_reflection = ""
        reflections = self.store.list_recent_reflections(limit=1)
        if reflections:
            payload = reflections[0].get("payload") or {}
            parts = []
            if payload.get("failure_patterns"):
                parts.append(f"Failure patterns: {'; '.join(payload['failure_patterns'])}")
            if payload.get("improvement_directions"):
                parts.append(f"Directions: {'; '.join(payload['improvement_directions'])}")
            if payload.get("discarded_motifs"):
                names = [m["motif"] for m in payload["discarded_motifs"]]
                parts.append(f"Discarded motifs (DO NOT reuse): {', '.join(names)}")
            if parts:
                last_reflection = "\n\nLAST CYCLE REFLECTION:\n" + "\n".join(parts)

        # Inject analyst strategy (long-term strategic direction)
        analyst_section = ""
        strategy = self.store.latest_analyst_strategy()
        if strategy:
            parts_s = []
            if strategy.get("strategic_directions"):
                parts_s.append("Strategic directions: " + "; ".join(strategy["strategic_directions"][:4]))
            if strategy.get("winning_patterns"):
                parts_s.append("Winning patterns: " + "; ".join(strategy["winning_patterns"][:4]))
            if strategy.get("losing_patterns"):
                parts_s.append("Avoid: " + "; ".join(strategy["losing_patterns"][:4]))
            if strategy.get("coverage_gaps"):
                parts_s.append("Underexplored areas: " + "; ".join(strategy["coverage_gaps"][:3]))
            if strategy.get("priority_categories"):
                parts_s.append(f"Priority categories: {', '.join(strategy['priority_categories'][:5])}")
            if strategy.get("operator_preferences"):
                parts_s.append(f"Preferred operators: {', '.join(strategy['operator_preferences'][:5])}")
            if strategy.get("avoid_operators"):
                parts_s.append(f"Avoid operators: {', '.join(strategy['avoid_operators'][:5])}")
            if parts_s:
                analyst_section = "\n\nANALYST STRATEGY (follow these long-term directions):\n" + "\n".join(parts_s)

        # Inject platform knowledge
        kb_proven = self._brain_knowledge_prompt("proven_alphas")
        kb_tips = self._brain_knowledge_prompt("platform_tips")
        kb_account = self._account_profile_prompt()
        kb_corr = self._brain_knowledge_prompt("production_correlation")
        kb_section = ""
        parts_kb = []
        if kb_proven:
            parts_kb.append(f"PROVEN ALPHA PATTERNS (reference these):\n{kb_proven}")
        if kb_tips:
            parts_kb.append(f"PLATFORM TIPS:\n{kb_tips}")
        if kb_corr:
            parts_kb.append(f"PRODUCTION CORRELATION STRATEGIES (critical):\n{kb_corr}")
        if kb_account:
            parts_kb.append(kb_account)
        if parts_kb:
            kb_section = "\n\n" + "\n\n".join(parts_kb)

        system_prompt = (
            "You are a quantitative researcher for WorldQuant BRAIN platform.\n"
            "Your job: generate ideas that an engineer can turn into BRAIN FASTEXPR alpha expressions.\n\n"
            "BRAIN acceptance criteria:\n"
            f"  Sharpe >= {criteria.min_sharpe}, Fitness >= {criteria.min_fitness}, "
            f"Turnover <= {criteria.max_turnover}\n"
            "  Fitness = Sharpe × sqrt(days_traded / total_days). To maximize fitness, the alpha must trade on MOST days.\n\n"
            "Available operators: rank, ts_corr, ts_delta, ts_mean, ts_std_dev, ts_sum, ts_max, ts_min, "
            "ts_returns, ts_delay, ts_rank, ts_argmax, ts_argmin, ts_product, "
            "group_rank, group_neutralize, trade_when, ts_regression, vector_neut, "
            "ts_decay_exp_window, signed_power, ts_quantile, humpdecay, abs, log, sign, power.\n"
            "Available fields: open, high, low, close, volume, vwap, adv20, returns, cap, sharesout, "
            "equity, enterprise_value, ebitda, operating_income, retained_earnings.\n"
            "Model data fields: mdf_oey, mdf_gry, mdf_nps, mdf_pbk.\n"
            "News/sentiment: nws12_afterhsz_sl, snt_buzz_ret, scl12_alltype_buzzvec.\n"
            "Options: implied_volatility_call_720, pcr_vol_10.\n"
            "Relative: rel_ret_comp.\n\n"
            "Rules:\n"
            "- Each idea MUST include a concrete expression sketch using the operators above.\n"
            "- Prefer 3+ nested operators; single-operator expressions always fail.\n"
            "- Always wrap with group_neutralize(subindustry, ...) for best performance.\n"
            "- Use trade_when with MODERATE volatility threshold (0.45-0.55, NOT 0.65) to keep enough trading days for high fitness.\n"
            "- Combine price-volume with fundamental data for strongest signals.\n"
            "- Explain the economic hypothesis: WHY should this signal predict returns?\n\n"
            "DIVERSITY REQUIREMENTS (critical):\n"
            "- Each idea MUST use a DIFFERENT primary signal mechanism (momentum, mean-reversion, fundamental, sentiment, cross-asset).\n"
            "- NEVER generate two ideas sharing the same core operator structure.\n"
            "- Explore underused operators: humpdecay, ts_quantile, signed_power, vector_neut, ts_regression(y,x,w,lag,rettype=3).\n"
            "- Try model data (mdf_oey, mdf_gry), news/sentiment, options, and relative returns data — these are underexplored.\n"
            "- The platform REJECTS alphas correlated with existing production alphas. Use diverse data fields and structural patterns.\n\n"
            "Return compact JSON only: a list with title, summary, expression_sketch, rationale, "
            "priority, source_kind, source_title, source_url."
            f"{kb_section}"
        )
        user_prompt = (
            f"Generate {batch_size} actionable WorldQuant alpha ideas.\n\n"
            f"Fresh sources:\n{sources_text}\n\n"
            f"Recent experiments (learn from these):\n{json.dumps(exp_digest, ensure_ascii=False, indent=2)}"
            f"{last_reflection}"
            f"{analyst_section}"
        )

        try:
            raw = provider.generate(system_prompt, user_prompt)
            ideas = json.loads(extract_json(raw))
            return [self._normalize_idea(idea, model_label) for idea in ideas[:batch_size]]
        except Exception as exc:
            logger.warning("researcher LLM failed, using fallback: %s", exc)
            self.store.add_event(
                level="error",
                kind="researcher",
                message=f"LLM call failed: {exc}",
                payload={"error": str(exc), "provider": self.config.get("agents", {}).get("researcher", {}).get("llm_profile", "unknown")},
            )
            return self._fallback_research_ideas(recent_sources, recent_experiments, batch_size)

    def _fallback_research_ideas(
        self,
        recent_sources: List[Dict[str, Any]],
        recent_experiments: List[Dict[str, Any]],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        ideas = []
        # Find the best recent expression to build on
        best = sorted(
            [e for e in recent_experiments if (e.get("sharpe") or 0) > 0],
            key=lambda e: e.get("sharpe", 0), reverse=True,
        )
        best_expr = best[0].get("alpha_expression", "") if best else ""
        _FALLBACK_SKETCHES = [
            "group_neutralize(trade_when(ts_rank(ts_std_dev(returns, 22), 252) > 0.50, "
            "rank(ts_decay_exp_window(rank(-ts_corr(open, volume, 20)) * ts_rank(mdf_oey, 252), 30)) "
            "+ rank(ts_mean(-returns, 5)), -1), subindustry)",
            "group_neutralize(trade_when(ts_rank(ts_std_dev(returns, 22), 252) > 0.45, "
            "signed_power(ts_regression(returns, ts_delay(returns, 1), 120, 0, 3), 0.5) "
            "* ts_rank(operating_income / (enterprise_value + 0.001), 180), -1), subindustry)",
            "group_neutralize(vector_neut(ts_decay_exp_window(rank(-ts_corr(vwap, volume, 15)) "
            "* ts_rank(mdf_gry, 120), 40), returns), subindustry)",
            "group_neutralize(trade_when(ts_rank(ts_std_dev(returns, 22), 252) > 0.50, "
            "rank(ts_quantile(returns, 60)) * rank(snt_buzz_ret), -1), subindustry)",
        ]
        hint = (
            f"Build on the strongest recent signal: {best_expr}. "
            "Use group_neutralize(subindustry, ...) and combine with diverse data fields."
            if best_expr else
            "Use group_neutralize(subindustry, trade_when(...)) patterns with 3+ nested operators."
        )
        for index, source in enumerate(recent_sources[:batch_size]):
            sketch = _FALLBACK_SKETCHES[index % len(_FALLBACK_SKETCHES)]
            ideas.append(
                self._normalize_idea(
                    {
                        "title": f"{source['source_kind']} signal idea {index + 1}: {source['title'][:60]}",
                        "summary": (
                            f"Derive a cross-sectional alpha from '{source['title'][:50]}'. "
                            f"Expression sketch: {sketch}"
                        ),
                        "rationale": hint,
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
                        "title": "Refinement of best historical alpha",
                        "summary": (
                            f"Take the best recent expression and create structurally different variants. "
                            f"Base: {best_expr or _FALLBACK_SKETCHES[0]}. "
                            "Try swapping data fields (mdf_oey, mdf_gry, snt_buzz_ret) and operators "
                            "(ts_regression rettype=3, vector_neut, signed_power)."
                        ),
                        "rationale": hint,
                        "priority": 5,
                        "source_kind": "history",
                        "source_title": "local alpha history",
                        "source_url": "",
                    }
                )
            )
        return ideas

    @staticmethod
    def _safe_priority(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            mapping = {"low": 25, "medium": 50, "high": 75, "critical": 90}
            return mapping.get(str(value).strip().lower(), 50)

    def _normalize_idea(self, idea: Dict[str, Any], llm_model: str = "") -> Dict[str, Any]:
        return {
            "title": str(idea.get("title", "Untitled idea")).strip(),
            "summary": str(idea.get("summary", "")).strip() or "No summary",
            "rationale": str(idea.get("rationale", "")).strip(),
            "priority": self._safe_priority(idea.get("priority", 50)),
            "source_kind": idea.get("source_kind"),
            "source_title": idea.get("source_title"),
            "source_url": idea.get("source_url"),
            "llm_model": llm_model,
            "status": "queued",
        }

    def run_engineer_cycle(self) -> str:
        limit = int(self.config["app"].get("default_claim_limit", 3))
        claimed_ideas = self.store.claim_ideas(limit=limit)

        profile_name = self.config.get("agents", {}).get("engineer", {}).get("llm_profile")
        provider = create_llm_provider(self.config, profile_name)

        client = None
        try:
            client = self._worldquant_client()
        except Exception as exc:
            self.store.add_event(level="warning", kind="engineer", message=f"worldquant unavailable: {exc}")

        if not claimed_ideas:
            # No new ideas, but still try to refine near-misses
            refined = self._refine_near_misses(provider, client)
            if not refined:
                return "no queued ideas"
            return f"claimed_ideas=0, experiments=0, refined={refined}"

        provider_info = describe_llm_profile(self.config, profile_name, provider)
        self.store.add_event(
            level="info",
            kind="engineer",
            message=(
                "engineer using llm profile "
                f"{provider_info['profile']} ({provider_info['provider']}) model={provider_info['model_name']}"
            ),
            payload=provider_info,
        )
        alpha_batch_size = int(self.config.get("agents", {}).get("engineer", {}).get("alpha_batch_size", 4))
        eng_model_label = f"{provider_info.get('provider', '')}:{provider_info.get('model_name', '')}".strip(":")

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
                results_dir=str(self.results_dir),
                enable_learning=True,
            )
            region = Region(self.config["integrations"]["worldquant"].get("region", "USA"))
            universe = Universe(self.config["integrations"]["worldquant"].get("universe", "TOP3000"))
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
                # Skip timed-out or errored simulations — don't record as rejected
                sim_status = getattr(record.simulate_result, "status", "")
                if sim_status in ("TIMEOUT", "ERROR", "FAILED") and record.simulate_result.sharpe == 0:
                    self.store.add_event(
                        level="warning",
                        kind="engineer",
                        message=f"simulation {sim_status} for {alpha_candidate.get('name', '?')}, skipping",
                    )
                    continue
                meets_threshold = criteria.check(record.simulate_result)
                experiment_id = self.store.create_experiment(
                    {
                        "idea_id": idea["id"],
                        "alpha_name": alpha_candidate.get("name") or record.alpha_type or record.expression[:48],
                        "alpha_expression": record.expression,
                        "implementation_notes": f"category={record.category}; source_idea={idea['title']}",
                        "llm_model": eng_model_label,
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

        # Phase 2: refine near-miss experiments (high sharpe but failed on turnover/fitness)
        refined = self._refine_near_misses(provider, client)

        self.store.add_event(
            level="info",
            kind="engineer",
            message=f"engineer processed {len(claimed_ideas)} ideas, created {len(results)} experiments, refined {refined} near-misses",
        )
        return f"claimed_ideas={len(claimed_ideas)}, experiments={len(results)}, refined={refined}"

    def _generate_engineering_candidates(
        self,
        provider: BaseLLMProvider,
        idea: Dict[str, Any],
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        smart_generator = SmartGenerator(AlphaAnalyzer(self.learning_db))
        template_weights = smart_generator.get_template_weights("regular")
        top_templates = sorted(template_weights.items(), key=lambda item: item[1], reverse=True)[:5]

        # Gather recently failed expressions so the LLM avoids repeating them
        recent_experiments = self.store.list_recent_experiments(limit=20)
        failed_expressions = [
            normalize_fastexpr_operators(e["alpha_expression"])
            for e in recent_experiments
            if e.get("status") == "rejected" and e.get("alpha_expression")
        ][:10]

        # Collect best historical results as few-shot examples
        best_examples = sorted(
            [e for e in recent_experiments if (e.get("sharpe") or 0) > 0.5],
            key=lambda e: e.get("sharpe", 0),
            reverse=True,
        )[:3]
        best_text = ""
        if best_examples:
            lines = []
            for e in best_examples:
                lines.append(
                    f"  - {normalize_fastexpr_operators(e.get('alpha_expression', ''))} → sharpe={e.get('sharpe')}, "
                    f"fitness={e.get('fitness')}, turnover={e.get('turnover')}"
                )
            best_text = "\nBest historical expressions (learn from these):\n" + "\n".join(lines)

        criteria = self._criteria()

        # Inject proven patterns from knowledge base
        kb_proven = self._brain_knowledge_prompt("proven_alphas")
        kb_operators = self._brain_knowledge_prompt("operators_by_category") or self._brain_knowledge_prompt("advanced_operators")
        kb_fields = self._brain_knowledge_prompt("top_data_fields")
        kb_account = self._account_profile_prompt()
        kb_corr = self._brain_knowledge_prompt("production_correlation")
        kb_section = ""
        if kb_proven:
            kb_section += f"\n\nPROVEN PATTERNS (use as inspiration):\n{kb_proven}"
        if kb_operators:
            kb_section += f"\n\nALL AVAILABLE OPERATORS:\n{kb_operators}"
        if kb_fields:
            kb_section += f"\n\nTOP DATA FIELDS BY POPULARITY:\n{kb_fields}"
        if kb_corr:
            kb_section += f"\n\nPRODUCTION CORRELATION STRATEGIES (critical for submission):\n{kb_corr}"
        if kb_account:
            kb_section += f"\n\n{kb_account}"

        system_prompt = (
            "You are a quant implementation agent for WorldQuant BRAIN. Return JSON only.\n"
            "Each item needs: name, category, expression, type.\n\n"
            "ACCEPTANCE CRITERIA (all must pass simultaneously):\n"
            f"  Sharpe >= {criteria.min_sharpe}, Fitness >= {criteria.min_fitness}, "
            f"Turnover <= {criteria.max_turnover}\n\n"
            "BRAIN FASTEXPR operators: rank, ts_corr, ts_delta, ts_mean, ts_std_dev, ts_sum, "
            "ts_max, ts_min, ts_returns, ts_delay, ts_rank, ts_argmax, ts_argmin, ts_product, "
            "group_rank, group_neutralize, trade_when, ts_regression, vector_neut, "
            "ts_decay_exp_window, signed_power, ts_quantile, humpdecay, "
            "abs, log, sign, max, min, power.\n"
            "Available fields: open, high, low, close, volume, vwap, adv20, returns, cap, sharesout, "
            "equity, enterprise_value, ebitda, operating_income, retained_earnings.\n"
            "Model data: mdf_oey, mdf_gry, mdf_nps, mdf_pbk.\n"
            "News/sentiment: nws12_afterhsz_sl, snt_buzz_ret.\n"
            "Options: implied_volatility_call_720, pcr_vol_10.\n"
            "Relative: rel_ret_comp.\n\n"
            "CRITICAL RULES:\n"
            "- ALWAYS wrap final expression with group_neutralize(subindustry, ...) for best performance.\n"
            "- Use trade_when(volatility_condition, alpha, -1) to reduce turnover, but keep threshold MODERATE (0.45-0.55).\n"
            "  Higher thresholds (0.65) kill fitness because Fitness = Sharpe × sqrt(days_traded/total_days).\n"
            "- Use 3+ nested operators; simple expressions always get rejected.\n"
            "- Combine price-volume AND fundamental fields for stronger signals.\n"
            "- Use ts_rank or rank for cross-sectional normalization.\n"
            "- Do NOT repeat any expression from the failed list below.\n"
            "- Each expression must be syntactically valid FASTEXPR.\n\n"
            "PRODUCTION CORRELATION AVOIDANCE (critical — best alphas were blocked by PROD_CORRELATION):\n"
            "- The platform rejects alphas correlated with existing production alphas.\n"
            "- Do NOT keep generating variants of: ts_decay_exp_window(rank(-ts_corr(price, volume, 20)) * ts_rank(fundamental/fundamental, N), M)\n"
            "- Use STRUCTURALLY DIFFERENT approaches across candidates:\n"
            "  * ts_regression(returns, x, window, 0, 3) for prediction-based signals\n"
            "  * vector_neut(alpha, momentum_risk_factor) for risk-neutralized signals\n"
            "  * humpdecay for turnover-controlled signals\n"
            "  * signed_power(x, 0.5) instead of rank() for non-linear transforms\n"
            "  * Model data (mdf_oey, mdf_gry) instead of equity/retained_earnings\n"
            "  * News/sentiment/options data for truly orthogonal signals\n"
            "- Each candidate MUST differ in primary data field AND primary operator structure."
            f"{best_text}"
            f"{kb_section}"
        )
        user_prompt = (
            f"Implement {batch_size} WorldQuant alpha expressions for this idea:\n"
            f"{json.dumps(idea, ensure_ascii=False, indent=2)}\n"
            f"Recent strong template hints:\n{json.dumps(top_templates, ensure_ascii=False)}\n"
            f"FAILED expressions (do NOT reuse):\n{json.dumps(failed_expressions, ensure_ascii=False)}"
        )
        try:
            raw = provider.generate(system_prompt, user_prompt)
            payload = json.loads(extract_json(raw))
            candidates = [self._normalize_alpha_candidate(item) for item in payload[:batch_size]]
            if candidates:
                return self._deduplicate_candidates(candidates)
        except Exception:
            pass

        generated = self.alpha_generator.generate_regular_alphas(batch_size, diversify=True)
        return self._deduplicate_candidates(
            [self._normalize_alpha_candidate(item) for item in generated]
        )

    def _normalize_alpha_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": item.get("name", "agent_alpha"),
            "category": item.get("category", "research"),
            "expression": normalize_fastexpr_operators(item.get("expression", "rank(close - vwap)")),
            "type": item.get("type", "regular"),
            "params": item.get("params", {}),
        }

    def _deduplicate_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove candidates whose expression was already tested in recent experiments."""
        conn = self.store._connect()
        tested = {
            row["alpha_expression"]
            for row in conn.execute(
                "SELECT DISTINCT alpha_expression FROM experiments"
            ).fetchall()
            if row["alpha_expression"]
        }
        conn.close()
        seen: set = set()
        result: List[Dict[str, Any]] = []
        for c in candidates:
            expr = c["expression"].strip()
            if expr not in tested and expr not in seen:
                seen.add(expr)
                result.append(c)
        return result if result else candidates[:1]  # keep at least one

    def _refine_near_misses(self, provider: BaseLLMProvider, client: Optional[Any]) -> int:
        """Find rejected or submission-blocked experiments and generate improved variants."""
        if client is None:
            return 0
        criteria = self._criteria()
        conn = self.store._connect()
        near_misses = conn.execute(
            """
            SELECT e.*, i.title AS idea_title
            FROM experiments e
            JOIN ideas i ON i.id = e.idea_id
            WHERE (
                (
                    e.status = 'rejected'
                    AND e.sharpe >= ?
                    AND (e.turnover > ? OR e.fitness < ?)
                )
                OR (
                    e.status = 'blocked'
                    AND (
                        e.submission_result LIKE 'blocked:FAIL=%'
                        OR e.submission_result LIKE 'blocked:PENDING=%'
                    )
                )
              )
              AND e.id NOT IN (
                  SELECT CAST(json_extract(payload_json, '$.parent_experiment_id') AS INTEGER)
                  FROM events
                  WHERE kind = 'engineer_refinement'
                    AND json_extract(payload_json, '$.parent_experiment_id') IS NOT NULL
              )
            ORDER BY CASE WHEN e.status = 'blocked' THEN 0 ELSE 1 END ASC, e.sharpe DESC
            LIMIT 2
            """,
            (criteria.min_sharpe * 0.6, criteria.max_turnover, criteria.min_fitness),
        ).fetchall()
        conn.close()

        if not near_misses:
            return 0

        submitter = AlphaSubmitter(
            client, criteria=criteria,
            results_dir=str(self.results_dir), enable_learning=True,
        )
        region = Region(self.config["integrations"]["worldquant"].get("region", "USA"))
        universe = Universe(self.config["integrations"]["worldquant"].get("universe", "TOP3000"))

        refined_count = 0
        for experiment in [dict(r) for r in near_misses]:
            self.store.set_agent_status(
                "engineer", "running",
                f"refining near-miss: {experiment.get('alpha_name', '?')} (sharpe={experiment.get('sharpe')})",
            )
            variants = self._generate_refinement_variants(provider, experiment, criteria)
            if not variants:
                continue

            records = submitter.simulate_and_submit(
                alphas=variants, region=region, universe=universe, auto_submit=False,
            )
            idea_status = "tested"
            for variant, record in zip(variants, records):
                sim_status = getattr(record.simulate_result, "status", "")
                if sim_status in ("TIMEOUT", "ERROR", "FAILED") and record.simulate_result.sharpe == 0:
                    continue
                meets = criteria.check(record.simulate_result)
                self.store.create_experiment({
                    "idea_id": experiment["idea_id"],
                    "alpha_name": f"refined_{variant.get('name', 'v')}",
                    "alpha_expression": record.expression,
                    "implementation_notes": f"refined from experiment {experiment['id']}",
                    "status": "promising" if meets else "rejected",
                    "sharpe": record.simulate_result.sharpe,
                    "fitness": record.simulate_result.fitness,
                    "turnover": record.simulate_result.turnover,
                    "drawdown": record.simulate_result.drawdown,
                    "returns": record.simulate_result.returns,
                    "wq_alpha_id": record.alpha_id,
                })
                refined_count += 1
                if meets:
                    idea_status = "reviewing"

            self.store.update_idea(
                experiment["idea_id"],
                status=idea_status,
                agent_notes=f"refined experiment {experiment['id']} into {len(records)} variants",
            )

            self.store.add_event(
                level="info", kind="engineer_refinement",
                message=f"refined experiment {experiment['id']}: generated {len(records)} variants",
                payload={
                    "parent_experiment_id": experiment["id"],
                    "parent_expression": experiment.get("alpha_expression"),
                    "parent_sharpe": experiment.get("sharpe"),
                    "parent_status": experiment.get("status"),
                    "parent_submission_result": experiment.get("submission_result"),
                    "variants": len(records),
                },
            )
        return refined_count

    def _generate_refinement_variants(
        self,
        provider: BaseLLMProvider,
        experiment: Dict[str, Any],
        criteria: SubmissionCriteria,
    ) -> List[Dict[str, Any]]:
        """Ask LLM to fix a near-miss alpha expression."""
        problem = refinement_problem(experiment, criteria)
        strategy_lines = refinement_strategy_lines(experiment, criteria)
        system_prompt = (
            "You are a quant engineer for WorldQuant BRAIN. "
            "Given a near-miss alpha expression, generate 2 improved variants.\n"
            "Return JSON only: a list with name, expression, category, type.\n\n"
            "ACCEPTANCE CRITERIA:\n"
            f"  Sharpe >= {criteria.min_sharpe}, Fitness >= {criteria.min_fitness}, "
            f"Turnover <= {criteria.max_turnover}\n"
            "  Fitness = Sharpe × sqrt(days_traded / total_days).\n\n"
            "FIX STRATEGIES:\n"
            + "\n".join(strategy_lines)
            + "\n\nPROD_CORRELATION FIX (if submission_result contains PROD_CORRELATION):\n"
            "- The original expression correlates too much with existing production alphas.\n"
            "- You MUST change the structural approach, not just tweak parameters.\n"
            "- Swap primary data fields (e.g., retained_earnings → mdf_oey, equity → mdf_gry).\n"
            "- Swap primary operators (e.g., ts_corr → ts_regression rettype=3, rank → signed_power).\n"
            "- Add vector_neut() to neutralize over a risk factor.\n"
            "- Use a completely different signal construction while preserving the economic idea.\n\n"
            "FITNESS FIX (if fitness is low):\n"
            "- Lower the trade_when volatility threshold (use 0.45-0.50 instead of 0.55-0.65).\n"
            "- Or remove trade_when entirely and control turnover via ts_decay_exp_window with longer window."
        )
        user_prompt = (
            f"Near-miss alpha to improve:\n"
            f"  Expression: {experiment.get('alpha_expression')}\n"
            f"  Sharpe: {experiment.get('sharpe')}, Fitness: {experiment.get('fitness')}, "
            f"Turnover: {experiment.get('turnover')}, Drawdown: {experiment.get('drawdown')}\n"
            f"  Current Status: {experiment.get('status')}\n"
            f"  Submission Result: {experiment.get('submission_result') or 'n/a'}\n"
            f"  Problem: {problem}\n"
            f"Generate 2 fixed variants."
        )
        try:
            raw = provider.generate(system_prompt, user_prompt)
            payload = json.loads(extract_json(raw))
            return [self._normalize_alpha_candidate(item) for item in payload[:2]]
        except Exception:
            return []

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
        blocked = 0
        for index, experiment in enumerate(experiments, start=1):
            self.store.set_agent_status(
                "reviewer",
                "running",
                f"reviewing experiment {index}/{len(experiments)}: {experiment.get('alpha_name') or experiment['idea_title'][:48]}",
            )
            final_status = "accepted"
            summary_prefix = "*Accepted alpha*"
            status_line = "Status: accepted without submit"
            idea_status = "accepted"
            idea_notes = "reviewer accepted candidate"
            payload: Dict[str, Any] = {
                "experiment_id": experiment["id"],
                "idea_id": experiment["idea_id"],
                "alpha_name": experiment.get("alpha_name"),
            }
            summary_body = (
                f"Idea: {experiment['idea_title']}\n"
                f"Sharpe: {experiment.get('sharpe')}\n"
                f"Fitness: {experiment.get('fitness')}\n"
                f"Turnover: {experiment.get('turnover')}\n"
                f"Alpha ID: `{experiment.get('wq_alpha_id') or 'n/a'}`"
            )

            if client and auto_submit and experiment.get("wq_alpha_id"):
                # Pre-check correlation before wasting a submission check
                try:
                    corr_data = client.check_alpha_correlation(experiment["wq_alpha_id"])
                    max_corr = corr_data.get("max_correlation", 0)
                    if isinstance(max_corr, (int, float)) and max_corr >= 0.65:
                        self.store.update_experiment(
                            experiment["id"],
                            status="blocked",
                            submitted=0,
                            submission_result=f"blocked:PROD_CORRELATION_PRECHECK={max_corr:.4f}",
                        )
                        blocked += 1
                        final_status = "blocked"
                        summary_prefix = "*Blocked alpha (correlation pre-check)*"
                        status_line = f"Status: blocked by correlation pre-check (max_corr={max_corr:.4f}, limit=0.7)"
                        idea_status = "blocked"
                        idea_notes = f"correlation pre-check failed: max_corr={max_corr:.4f}"
                        payload["max_correlation"] = max_corr
                        self.store.update_idea(experiment["idea_id"], status=idea_status, agent_notes=idea_notes)
                        summary = f"{summary_prefix}\n{summary_body}\n{status_line}"
                        self.notifier.send(summary)
                        self.store.add_event(
                            level="info", kind="reviewer",
                            message=f"experiment {experiment['id']} blocked by correlation pre-check (max_corr={max_corr:.4f})",
                            payload=payload,
                        )
                        continue
                except Exception as exc:
                    logger.debug("correlation pre-check skipped: %s", exc)

                submit_result = client.submit_alpha_with_checks(experiment["wq_alpha_id"])
                submit_reason = compact_submission_reason(submit_result.reason)
                payload["submission_reason"] = submit_reason
                if submit_result.submitted:
                    self.store.update_experiment(
                        experiment["id"],
                        status="submitted",
                        submitted=1,
                        submission_result="submitted_to_worldquant",
                    )
                    submitted += 1
                    final_status = "submitted"
                    summary_prefix = "*Submitted alpha*"
                    status_line = "Status: submitted to WorldQuant"
                    idea_notes = "reviewer accepted and submitted candidate"
                else:
                    if is_worldquant_submission_block(submit_result):
                        self.store.update_experiment(
                            experiment["id"],
                            status="blocked",
                            submitted=0,
                            submission_result=f"blocked:{submit_reason}",
                        )
                        blocked += 1
                        final_status = "blocked"
                        summary_prefix = "*Blocked alpha*"
                        status_line = f"Status: blocked by WorldQuant submit checks ({submit_reason})"
                        idea_status = "blocked"
                        idea_notes = f"submission blocked: {submit_reason}"
                    else:
                        self.store.update_experiment(
                            experiment["id"],
                            status="accepted",
                            submitted=0,
                            submission_result=f"submit_failed:{submit_reason}",
                        )
                        accepted += 1
                        status_line = f"Status: accepted, submit failed ({submit_reason})"
                        idea_notes = f"reviewer accepted candidate; auto-submit failed: {submit_reason}"
            else:
                self.store.update_experiment(
                    experiment["id"],
                    status="accepted",
                    submission_result="accepted_without_submit",
                )
                accepted += 1
            self.store.update_idea(experiment["idea_id"], status=idea_status, agent_notes=idea_notes)
            summary = f"{summary_prefix}\n{summary_body}\n{status_line}"
            self.notifier.send(summary)
            self.store.add_event(
                level="info",
                kind="reviewer",
                message=f"reviewed experiment {experiment['id']} with status {final_status}",
                payload=payload,
            )

        self.store.add_event(
            level="info",
            kind="reviewer",
            message=f"reviewer accepted {accepted}, submitted {submitted}, blocked {blocked}",
        )
        return f"accepted={accepted}, submitted={submitted}, blocked={blocked}"

    def run_analyst_cycle(self) -> str:
        analyst_config = self.config.get("agents", {}).get("analyst", {})
        review_every_n = int(analyst_config.get("review_every_n_experiments", 20))

        new_experiments = self.store.experiment_count_since("analyst_strategy")
        if new_experiments < review_every_n:
            return f"waiting ({new_experiments}/{review_every_n} new experiments)"

        self.store.set_agent_status("analyst", "running", "deep review in progress")

        all_experiments = self.store.all_experiments()
        if not all_experiments:
            return "no experiments to analyze"

        # Build statistical summary
        stats = self._build_experiment_stats(all_experiments)

        # Ask LLM for strategic analysis
        profile_name = analyst_config.get("llm_profile")
        provider = create_llm_provider(self.config, profile_name)
        provider_info = describe_llm_profile(self.config, profile_name, provider)
        self.store.add_event(
            level="info",
            kind="analyst",
            message=f"analyst using llm profile {provider_info['profile']} ({provider_info['provider']}) model={provider_info['model_name']}",
            payload=provider_info,
        )

        strategy = self._generate_research_strategy(provider, stats, all_experiments)
        if not strategy:
            return "llm failed, no strategy generated"

        strategy["stats"] = stats
        strategy["experiment_count"] = len(all_experiments)
        strategy["generated_at"] = utc_now()

        self.store.add_event(
            level="info",
            kind="analyst_strategy",
            message=f"analyst reviewed {len(all_experiments)} experiments, updated research strategy",
            payload=strategy,
        )
        self.store.add_event(
            level="info",
            kind="analyst",
            message=f"strategy updated: {strategy.get('headline', 'new strategy')}",
        )

        return f"analyzed {len(all_experiments)} experiments, strategy updated"

    def _build_experiment_stats(self, experiments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate statistics across all experiments."""
        by_status: Dict[str, int] = {}
        by_category: Dict[str, Dict[str, Any]] = {}
        sharpes: List[float] = []
        turnovers: List[float] = []
        fitnesses: List[float] = []

        for e in experiments:
            status = e.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1

            sharpe = e.get("sharpe")
            turnover = e.get("turnover")
            fitness = e.get("fitness")
            if isinstance(sharpe, (int, float)):
                sharpes.append(float(sharpe))
            if isinstance(turnover, (int, float)):
                turnovers.append(float(turnover))
            if isinstance(fitness, (int, float)):
                fitnesses.append(float(fitness))

            # Group by category (extracted from implementation_notes or alpha_name)
            notes = e.get("implementation_notes") or ""
            cat = "unknown"
            if "category=" in notes:
                cat = notes.split("category=")[1].split(";")[0].strip()
            cat_stats = by_category.setdefault(cat, {"total": 0, "promising": 0, "rejected": 0, "sharpes": []})
            cat_stats["total"] += 1
            if status == "promising" or status in ("accepted", "submitted"):
                cat_stats["promising"] += 1
            elif status == "rejected":
                cat_stats["rejected"] += 1
            if isinstance(sharpe, (int, float)):
                cat_stats["sharpes"].append(float(sharpe))

        # Compute category success rates
        category_summary = {}
        for cat, cs in by_category.items():
            avg_sharpe = sum(cs["sharpes"]) / len(cs["sharpes"]) if cs["sharpes"] else 0
            success_rate = cs["promising"] / cs["total"] if cs["total"] else 0
            category_summary[cat] = {
                "total": cs["total"],
                "promising": cs["promising"],
                "rejected": cs["rejected"],
                "success_rate": round(success_rate, 3),
                "avg_sharpe": round(avg_sharpe, 3),
            }

        # Find top performing expressions
        sorted_by_sharpe = sorted(
            [e for e in experiments if isinstance(e.get("sharpe"), (int, float))],
            key=lambda e: e["sharpe"],
            reverse=True,
        )
        top_expressions = [
            {
                "expression": e.get("alpha_expression", "")[:120],
                "sharpe": e.get("sharpe"),
                "fitness": e.get("fitness"),
                "turnover": e.get("turnover"),
                "status": e.get("status"),
            }
            for e in sorted_by_sharpe[:5]
        ]

        # Find most common failure patterns
        high_turnover_count = sum(1 for e in experiments if (e.get("turnover") or 0) > 0.7 and e.get("status") == "rejected")
        low_fitness_count = sum(1 for e in experiments if (e.get("fitness") or 0) < 0.7 and e.get("status") == "rejected")
        low_sharpe_count = sum(1 for e in experiments if (e.get("sharpe") or 0) < 1.25 and e.get("status") == "rejected")

        return {
            "total": len(experiments),
            "by_status": by_status,
            "avg_sharpe": round(sum(sharpes) / len(sharpes), 3) if sharpes else 0,
            "avg_turnover": round(sum(turnovers) / len(turnovers), 3) if turnovers else 0,
            "avg_fitness": round(sum(fitnesses) / len(fitnesses), 3) if fitnesses else 0,
            "category_summary": category_summary,
            "top_expressions": top_expressions,
            "failure_breakdown": {
                "high_turnover": high_turnover_count,
                "low_fitness": low_fitness_count,
                "low_sharpe": low_sharpe_count,
            },
        }

    def _generate_research_strategy(
        self,
        provider: BaseLLMProvider,
        stats: Dict[str, Any],
        experiments: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Use LLM to produce a strategic research direction from full experiment history."""
        criteria = self._criteria()

        # Build a condensed experiment history for the LLM
        exp_summary = []
        for e in experiments[-50:]:  # last 50 experiments for context window
            exp_summary.append({
                "name": e.get("alpha_name", "")[:48],
                "expr": (e.get("alpha_expression") or "")[:100],
                "sharpe": e.get("sharpe"),
                "fitness": e.get("fitness"),
                "turnover": e.get("turnover"),
                "status": e.get("status"),
            })

        system_prompt = (
            "You are a senior quant strategist reviewing the full experiment history of a WorldQuant BRAIN alpha research system.\n"
            "Your job: analyze patterns across ALL experiments and produce a strategic research direction.\n\n"
            "ACCEPTANCE CRITERIA:\n"
            f"  Sharpe >= {criteria.min_sharpe}, Fitness >= {criteria.min_fitness}, Turnover <= {criteria.max_turnover}\n\n"
            "Your analysis must cover:\n"
            "1. WINNING PATTERNS: What operator combinations, field choices, and structural patterns appear in successful alphas?\n"
            "2. LOSING PATTERNS: What patterns consistently fail? What should be permanently avoided?\n"
            "3. COVERAGE GAPS: What alpha categories or signal types are underexplored?\n"
            "4. STRATEGIC DIRECTION: Concrete recommendations for the next batch of research ideas.\n"
            "5. PRIORITY SHIFTS: Should the researcher focus more on momentum, mean-reversion, fundamental, or hybrid signals?\n\n"
            "Return JSON with these exact keys:\n"
            "  headline: one-line summary of the strategy\n"
            "  winning_patterns: list of strings describing what works\n"
            "  losing_patterns: list of strings describing what to avoid\n"
            "  coverage_gaps: list of underexplored areas worth trying\n"
            "  strategic_directions: list of concrete next-step recommendations\n"
            "  priority_categories: ordered list of categories to focus on (best first)\n"
            "  operator_preferences: list of preferred operator combinations\n"
            "  avoid_operators: list of operator patterns that consistently fail"
        )
        user_prompt = (
            f"Experiment statistics:\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n\n"
            f"Recent experiment history (last {len(exp_summary)}):\n"
            f"{json.dumps(exp_summary, ensure_ascii=False, indent=2)}"
        )
        try:
            raw = provider.generate(system_prompt, user_prompt)
            return json.loads(extract_json(raw))
        except Exception as exc:
            logger.warning("analyst LLM failed: %s", exc)
            self.store.add_event(level="error", kind="analyst", message=f"LLM call failed: {exc}")
            return None


def extract_json(text: str) -> str:
    if not text:
        return "[]"
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            _, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        return text[start : start + end]
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
    try:
        pid = int(runtime.pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        runtime.remove_runtime_metadata()
        return {"status": "stale_pid", "pid": 0}
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


def sync_brain_knowledge(config_path: Path) -> Dict[str, Any]:
    """Fetch operators and popular data fields from BRAIN API and update local knowledge base."""
    config = load_yaml_config(config_path)
    state_dir = Path(config["app"]["state_dir"]).resolve()
    wq_config = config.get("integrations", {}).get("worldquant", {})
    username = wq_config.get("username", "")
    password = wq_config.get("password", "")
    if not username or not password:
        return {"error": "WorldQuant credentials not configured"}

    client = WorldQuantBrainClient(
        username,
        password,
        disable_proxy=bool(wq_config.get("disable_proxy", False)),
    )
    if not client.authenticate():
        return {"error": "WorldQuant authentication failed"}

    base = client.BASE_URL

    # 1. Fetch all operators
    resp = client._request("get", f"{base}/operators", timeout=30)
    operators_raw = resp.json() if resp.status_code == 200 else []
    operators = [
        {
            "name": op["name"],
            "category": op.get("category", ""),
            "definition": op.get("definition", ""),
            "description": op.get("description", ""),
            "scope": op.get("scope", []),
        }
        for op in operators_raw
    ]

    # 2. Fetch popular data fields across key categories
    field_categories = ["fundamental", "model", "sentiment", "news", "option"]
    all_fields: Dict[str, Any] = {}
    for cat in field_categories:
        r = client._request(
            "get",
            f"{base}/data-fields?instrumentType=EQUITY&region=USA&delay=1&universe=TOP3000&limit=50&category={cat}",
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            for f in data.get("results", []) if isinstance(data, dict) else []:
                all_fields[f["id"]] = f

    # Also fetch default (alphabetical) pages to catch price-volume fields
    for offset in [0, 50]:
        r = client._request(
            "get",
            f"{base}/data-fields?instrumentType=EQUITY&region=USA&delay=1&universe=TOP3000&limit=50&offset={offset}",
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            for f in data.get("results", []) if isinstance(data, dict) else []:
                all_fields[f["id"]] = f

    # Sort by popularity and keep top 200
    sorted_fields = sorted(all_fields.values(), key=lambda f: f.get("alphaCount", 0), reverse=True)
    datafields = [
        {
            "id": f["id"],
            "desc": f.get("description", ""),
            "cat": f.get("category", {}).get("name", ""),
            "type": f.get("type", ""),
            "alphas": f.get("alphaCount", 0),
        }
        for f in sorted_fields[:200]
    ]

    # 3. Save raw JSON files
    ensure_directory(state_dir)
    ops_path = state_dir / "brain_operators.json"
    fields_path = state_dir / "brain_datafields.json"
    ops_path.write_text(json.dumps(operators, indent=2, ensure_ascii=False), encoding="utf-8")
    fields_path.write_text(json.dumps(datafields, indent=2, ensure_ascii=False), encoding="utf-8")

    # 4. Update brain_knowledge.yaml with synced data
    kb_path = state_dir / "brain_knowledge.yaml"
    kb: Dict[str, Any] = {}
    if kb_path.exists():
        try:
            kb = load_yaml_config(kb_path)
        except Exception:
            kb = {}

    # Build operator reference grouped by category
    op_by_cat: Dict[str, list] = {}
    for op in operators:
        cat = op["category"] or "Other"
        op_by_cat.setdefault(cat, []).append(f"{op['name']} — {op['definition']}")
    kb["operators_by_category"] = op_by_cat

    # Build top data fields reference
    top_fields_ref = [
        f"{f['id']} ({f['cat']}, {f['alphas']} alphas) — {f['desc'][:80]}"
        for f in datafields[:60]
    ]
    kb["top_data_fields"] = top_fields_ref

    if yaml is None:
        # Fallback: write as JSON if yaml not available
        kb_path.with_suffix(".json").write_text(
            json.dumps(kb, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        kb_path.write_text(
            yaml.dump(kb, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120),
            encoding="utf-8",
        )

    return {
        "operators": len(operators),
        "data_fields": len(datafields),
        "knowledge_base": str(kb_path),
        "operators_file": str(ops_path),
        "datafields_file": str(fields_path),
    }


def sync_account_info(config_path: Path) -> Dict[str, Any]:
    """Probe WQ account permissions, real submission thresholds, and save to knowledge base."""
    config = load_yaml_config(config_path)
    state_dir = Path(config["app"]["state_dir"]).resolve()
    wq_config = config.get("integrations", {}).get("worldquant", {})
    username = wq_config.get("username", "")
    password = wq_config.get("password", "")
    if not username or not password:
        return {"error": "WorldQuant credentials not configured"}

    client = WorldQuantBrainClient(
        username,
        password,
        disable_proxy=bool(wq_config.get("disable_proxy", False)),
    )
    if not client.authenticate():
        return {"error": "WorldQuant authentication failed"}

    base = client.BASE_URL
    profile: Dict[str, Any] = {}

    # 1. User profile
    r = client._request("get", f"{base}/users/self", timeout=15)
    if r.status_code == 200:
        u = r.json()
        profile["username"] = u.get("id", "")
        profile["genius_level"] = u.get("geniusLevel", "")
        profile["onboarding"] = u.get("onboarding", {}).get("status", "")

    # 2. Consultant info
    r = client._request("get", f"{base}/users/self/consultant", timeout=15)
    if r.status_code == 200:
        c = r.json()
        profile["submissions"] = c.get("submissions", 0)
        profile["super_submissions"] = c.get("superAlphaSubmissions", 0)

    # 3. Alpha stats
    r = client._request("get", f"{base}/users/self/alphas?limit=1", timeout=15)
    if r.status_code == 200:
        profile["total_alphas"] = r.json().get("count", 0)

    # 4. Check SUPER permission
    try:
        r = client._request("post", f"{base}/simulations", json={
            "type": "SUPER",
            "settings": {"instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000",
                         "delay": 1, "decay": 0, "neutralization": "SUBINDUSTRY", "truncation": 0.08,
                         "pasteurization": "ON", "unitHandling": "VERIFY", "nanHandling": "ON",
                         "language": "FASTEXPR", "visualization": False},
            "regular": "close"
        }, timeout=15)
        profile["super_permitted"] = "not permissioned" not in r.text.lower()
    except Exception:
        profile["super_permitted"] = False

    # 5. Probe available regions and delays together
    available_regions = []
    delay_map = {}
    for region in ["USA", "CHN", "EUR", "ASI"]:
        delays = []
        for d in [0, 1]:
            r = client._request("get", f"{base}/data-fields?instrumentType=EQUITY&region={region}&delay={d}&universe=TOP3000&limit=1", timeout=10)
            if r.status_code == 200:
                data = r.json()
                cnt = data.get("count", 0) if isinstance(data, dict) and "count" in data else len(data.get("results", [])) if isinstance(data, dict) else 0
                if cnt > 0:
                    delays.append(d)
        if delays:
            available_regions.append(region)
            delay_map[region] = delays
    profile["available_regions"] = available_regions
    profile["available_delays"] = delay_map

    # 7. Extract real submission checks from a recent alpha
    r = client._request("get", f"{base}/users/self/alphas?limit=10", timeout=15)
    if r.status_code == 200:
        checks_extracted = {}
        for alpha in r.json().get("results", []):
            is_data = alpha.get("is", {})
            for chk in is_data.get("checks", []) if isinstance(is_data, dict) else []:
                name = chk.get("name", "")
                if name and "limit" in chk and name not in checks_extracted:
                    checks_extracted[name] = chk["limit"]
        if checks_extracted:
            profile["real_submission_checks"] = checks_extracted

    # 8. Save to knowledge base
    ensure_directory(state_dir)
    kb_path = state_dir / "brain_knowledge.yaml"
    kb: Dict[str, Any] = {}
    if kb_path.exists():
        try:
            kb = load_yaml_config(kb_path)
        except Exception:
            kb = {}

    kb["account_profile"] = profile

    if yaml is not None:
        kb_path.write_text(
            yaml.dump(kb, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120),
            encoding="utf-8",
        )
    else:
        kb_path.with_suffix(".json").write_text(json.dumps(kb, indent=2, ensure_ascii=False), encoding="utf-8")

    return profile
