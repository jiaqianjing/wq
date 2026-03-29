"""SourceCollector - fetch external paper/report/market feeds with 429 cooldowns.

CALLING SPEC:
    collector = SourceCollector(config)
    items = collector.collect() -> List[SourceItem]

Input:
    config["sources"][kind] entries with:
        name (optional), url, timeout_seconds (optional), user_agent (optional),
        honor_retry_after (optional), rate_limit_cooldown_seconds (optional)

Output:
    A flat list of parsed SourceItem records from atom/rss feeds.

Side effects:
    Performs outbound HTTP GET requests, logs fetch/cooldown events, and keeps
    in-memory per-source cooldown state for the lifetime of the process.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_TIMEOUT_SECONDS = 15
DEFAULT_SOURCE_USER_AGENT = "wqa-source-collector/1.0"
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 1800


@dataclass
class SourceItem:
    source_kind: str
    title: str
    summary: str
    url: str
    published_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def source_name(source: Dict[str, Any]) -> str:
    return str(source.get("name") or source.get("url") or "<unnamed-source>")


def source_key(kind: str, source: Dict[str, Any]) -> str:
    return f"{kind}:{source_name(source)}"


def parse_retry_after_seconds(value: str, now_ts: float) -> int | None:
    if not value:
        return None
    try:
        seconds = int(float(value))
        return max(seconds, 0)
    except (TypeError, ValueError):
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(int(retry_at.timestamp() - now_ts), 0)


def parse_feed_items(kind: str, default_url: str, xml_text: str) -> List[SourceItem]:
    root = ElementTree.fromstring(xml_text)
    items: List[SourceItem] = []

    atom_entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if atom_entries:
        for entry in atom_entries:
            title = entry.findtext("{http://www.w3.org/2005/Atom}title", default="").strip()
            summary = entry.findtext("{http://www.w3.org/2005/Atom}summary", default="").strip()
            published = entry.findtext("{http://www.w3.org/2005/Atom}published", default=utc_now())
            link_node = entry.find("{http://www.w3.org/2005/Atom}id")
            url_value = link_node.text.strip() if link_node is not None and link_node.text else default_url
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
        link = (entry.findtext("link") or default_url).strip()
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


class SourceCollector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._cooldowns: Dict[str, float] = {}

    def collect(self) -> List[SourceItem]:
        items: List[SourceItem] = []
        for kind in ("papers", "reports", "market"):
            for source in self.config.get("sources", {}).get(kind, []):
                try:
                    items.extend(self._fetch_source(kind, source))
                except requests.HTTPError as exc:
                    response = exc.response
                    source_label = source_name(source)
                    url = source.get("url", "")
                    if response is not None:
                        logger.warning(
                            "failed to fetch source %s: status=%s url=%s",
                            source_label,
                            response.status_code,
                            url,
                        )
                    else:
                        logger.warning("failed to fetch source %s: url=%s error=%s", source_label, url, exc)
                except Exception as exc:  # pragma: no cover
                    logger.warning("failed to fetch source %s: %s", source_name(source), exc)
        return items

    def _fetch_source(self, kind: str, source: Dict[str, Any]) -> List[SourceItem]:
        url = str(source.get("url", "")).strip()
        if not url:
            return []

        source_label = source_name(source)
        key = source_key(kind, source)
        now_ts = time.time()
        cooldown_until = self._cooldowns.get(key, 0.0)
        if cooldown_until > now_ts:
            remaining = max(int(cooldown_until - now_ts), 1)
            logger.info(
                "skipping source %s during rate-limit cooldown: url=%s remaining_seconds=%s",
                source_label,
                url,
                remaining,
            )
            return []

        timeout_seconds = int(source.get("timeout_seconds", DEFAULT_SOURCE_TIMEOUT_SECONDS))
        headers = {"User-Agent": str(source.get("user_agent") or DEFAULT_SOURCE_USER_AGENT)}
        response = requests.get(url, timeout=timeout_seconds, headers=headers)
        if response.status_code == 429:
            cooldown_seconds = self._cooldown_seconds(source, response, now_ts)
            self._cooldowns[key] = now_ts + cooldown_seconds
            logger.warning(
                "source rate limited: name=%s url=%s status=429 cooldown_seconds=%s retry_after_raw=%s",
                source_label,
                url,
                cooldown_seconds,
                response.headers.get("Retry-After"),
            )
            return []

        response.raise_for_status()
        return parse_feed_items(kind, url, response.text)

    def _cooldown_seconds(
        self,
        source: Dict[str, Any],
        response: requests.Response,
        now_ts: float,
    ) -> int:
        fallback = int(source.get("rate_limit_cooldown_seconds", DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS))
        if fallback < 1:
            fallback = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS

        honor_retry_after = as_bool(source.get("honor_retry_after"), default=True)
        if not honor_retry_after:
            return fallback

        retry_after_raw = response.headers.get("Retry-After", "")
        retry_after_seconds = parse_retry_after_seconds(retry_after_raw, now_ts)
        if retry_after_seconds is None:
            return fallback
        return max(retry_after_seconds, 1)
