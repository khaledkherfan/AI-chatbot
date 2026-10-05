"""
Persistent query analytics for the JUST Assistant (aligned with /query and /query/stream).

Appends one JSON line per answered query to logs/query_events.jsonl for:
- Top questions (most asked)
- Topic (canonical) frequency
- Cache (redis) vs live_web split
- Time series of query volume
"""
from __future__ import annotations

import json
import os
import re
import threading
import time as _time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from config import ANALYTICS_CACHE_TTL

_LOCK = threading.Lock()
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_BASE, "logs", "query_events.jsonl")
_MAX_BYTES = 8 * 1024 * 1024  # rotate when file exceeds ~8 MB

# In-process result cache: days -> (monotonic_timestamp, payload)
_analytics_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_analytics_cache_lock = threading.Lock()


def _ensure_logs_dir() -> None:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)


def _rotate_if_needed() -> None:
    if not os.path.isfile(_LOG_PATH):
        return
    try:
        if os.path.getsize(_LOG_PATH) < _MAX_BYTES:
            return
    except OSError:
        return
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    rotated = _LOG_PATH.replace(".jsonl", f".{ts}.bak.jsonl")
    try:
        os.replace(_LOG_PATH, rotated)
    except OSError:
        pass


def _invalidate_analytics_cache() -> None:
    """Clear the in-process analytics cache so the next read reflects fresh data."""
    with _analytics_cache_lock:
        _analytics_cache.clear()


def record_query_event(
    query: str,
    source: str,
    topic: str | None,
    mode: str,
) -> None:
    """Record one assistant query after metadata is known.
    source values: redis | live_web | planner
    """
    if not query or not str(query).strip():
        return
    q = str(query).strip()
    if len(q) > 800:
        q = q[:800] + "…"

    # Preserve all three source labels for accurate analytics
    src = source if source in ("redis", "live_web", "planner") else "live_web"

    topic_key = (topic or "unknown").strip() or "unknown"
    row = {
        "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "q": q,
        "source": src,
        "topic": topic_key,
        "mode": mode,
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _LOCK:
        _ensure_logs_dir()
        _rotate_if_needed()
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    # New event means cached payload is stale
    _invalidate_analytics_cache()


def _read_events(max_lines: int = 20000) -> list[dict[str, Any]]:
    if not os.path.isfile(_LOG_PATH):
        return []
    lines: list[str] = []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    tail = lines[-max_lines:]
    out: list[dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _norm_question(q: str) -> str:
    """
    Normalize a question string for grouping purposes.
    Applies NFC Unicode normalization, strips punctuation, and lowercases — so
    Arabic dialect variants and minor spelling differences map to the same key.
    """
    q = unicodedata.normalize('NFC', q)
    q = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', q)
    return ' '.join(q.lower().split())


def _build_analytics_payload_impl(days: int) -> dict[str, Any]:
    """Core computation — called only when cache is cold."""
    events = _read_events()
    if not events:
        return {
            "totalQueries": 0,
            "queriesLast24h": 0,
            "queriesLast7d": 0,
            "cacheHitRate": None,
            "sourceSplit": {"redis": 0, "live_web": 0, "planner": 0},
            "dailyVolume": [],
            "topQuestions": [],
            "topTopics": [],
            "answersDelivered": 0,
            "modeSplit": {"sync": 0, "stream": 0},
        }

    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    cutoff24 = now - timedelta(hours=24)
    cutoff7 = now - timedelta(days=7)

    def parse_ts(s: str) -> datetime | None:
        try:
            if s.endswith("Z"):
                s = s[:-1]
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    filtered: list[dict[str, Any]] = []
    for e in events:
        ts = parse_ts(e.get("ts", ""))
        if ts is None:
            continue
        if ts.replace(tzinfo=None) >= cutoff:
            filtered.append(e)

    total = len(filtered)
    q24 = 0
    q7 = 0
    for e in filtered:
        ts = parse_ts(e.get("ts", ""))
        if not ts:
            continue
        t = ts.replace(tzinfo=None)
        if t >= cutoff24:
            q24 += 1
        if t >= cutoff7:
            q7 += 1

    src_c = Counter(e.get("source", "live_web") for e in filtered)
    redis_n = src_c.get("redis", 0)
    live_n = src_c.get("live_web", 0)
    planner_n = src_c.get("planner", 0)
    denom = redis_n + live_n  # planner excluded from cache-hit-rate denominator
    hit_rate = round(100.0 * redis_n / denom, 1) if denom else None

    # Daily volume
    day_counts: Counter[str] = Counter()
    for e in filtered:
        ts = parse_ts(e.get("ts", ""))
        if ts:
            day_counts[ts.strftime("%Y-%m-%d")] += 1
    daily_volume = [{"date": d, "count": day_counts[d]} for d in sorted(day_counts.keys())]

    # Top questions (normalized for grouping)
    q_counter: Counter[str] = Counter()
    q_display: dict[str, str] = {}
    for e in filtered:
        q = e.get("q", "").strip()
        if not q:
            continue
        key = _norm_question(q)
        q_counter[key] += 1
        if key not in q_display:
            q_display[key] = q[:120]

    top_q = [
        {"question": q_display[k], "count": c, "key": k}
        for k, c in q_counter.most_common(20)
    ]

    topic_c = Counter((e.get("topic") or "unknown").strip() for e in filtered)
    top_topics = [{"topic": t, "count": n} for t, n in topic_c.most_common(15)]

    mode_c = Counter(e.get("mode", "stream") for e in filtered)

    return {
        "totalQueries": total,
        "queriesLast24h": q24,
        "queriesLast7d": q7,
        "cacheHitRate": hit_rate,
        "sourceSplit": {"redis": redis_n, "live_web": live_n, "planner": planner_n},
        "dailyVolume": daily_volume,
        "topQuestions": top_q,
        "topTopics": top_topics,
        "answersDelivered": total,
        "modeSplit": {"sync": mode_c.get("sync", 0), "stream": mode_c.get("stream", 0)},
    }


def build_analytics_payload(days: int = 30) -> dict[str, Any]:
    """
    Aggregates for admin dashboard charts and tables.
    Results are cached in-process for ANALYTICS_CACHE_TTL seconds to avoid
    repeated full-file reads on every admin page load.
    """
    now = _time.monotonic()
    with _analytics_cache_lock:
        if days in _analytics_cache:
            ts, cached = _analytics_cache[days]
            if now - ts < ANALYTICS_CACHE_TTL:
                return cached

    result = _build_analytics_payload_impl(days)

    with _analytics_cache_lock:
        _analytics_cache[days] = (now, result)

    return result


def recent_events(limit: int = 80) -> list[dict[str, Any]]:
    ev = _read_events(max_lines=5000)
    tail = ev[-limit:] if len(ev) > limit else ev
    out = []
    for i, e in enumerate(reversed(tail)):
        q = (e.get("q", "") or "")[:160]
        out.append(
            {
                "id": str(i) + "-" + (e.get("ts", "") or ""),
                "title": q or "(empty)",
                "detail": (
                    "topic: " + str(e.get("topic", ""))
                    + " · " + str(e.get("source", ""))
                    + " · " + str(e.get("mode", ""))
                ),
                "level": "info",
                "timestamp": e.get("ts", ""),
                "type": "query",
            }
        )
    return out
