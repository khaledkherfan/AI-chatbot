from __future__ import annotations
import json
import os
import time
from datetime import datetime
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from config import OPENAI_API_KEY, SIMILARITY_THRESHOLD
from services.analytics_service import build_analytics_payload, recent_events

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

admin_bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_BASE_DIR, "logs", "assistant.log")


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _qc():
    return current_app.config.get("QUERY_CONTROLLER")


def _read_log_tail(max_lines: int = 300) -> list[str]:
    if not os.path.isfile(_LOG_PATH):
        return []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-max_lines:]]
    except OSError:
        return []


def _parse_log_level(line: str) -> str:
    upper = line.upper()
    if "ERROR" in upper or "CRITICAL" in upper:
        return "error"
    if "WARN" in upper:
        return "warning"
    return "info"


@admin_bp.route("/overview", methods=["GET"])
def overview():
    """Health + Redis/embeddings stats + real query analytics (no revenue)."""
    qc = _qc()
    stats: dict[str, Any] = {}
    if qc:
        try:
            stats = qc.get_stats()
        except Exception:
            stats = {}

    ax = build_analytics_payload(days=30)

    redis_ok = bool(stats.get("redis_connected"))
    openai_ok = bool(stats.get("openai_configured"))
    emb_ok = bool(stats.get("embeddings_configured"))

    return jsonify(
        {
            "assistant": {
                "openaiConfigured": openai_ok,
                "embeddingsConfigured": emb_ok,
                "similarityThreshold": float(stats.get("similarity_threshold", SIMILARITY_THRESHOLD)),
                "redisConnected": redis_ok,
            },
            "redis": {
                "connected": redis_ok,
                "dataKeys": stats.get("total_data_keys", 0),
                "aliasKeys": stats.get("total_aliases", 0),
                "embeddingVectors": stats.get("total_embeddings", 0),
                "totalKeys": (
                    stats.get("total_data_keys", 0)
                    + stats.get("total_aliases", 0)
                    + stats.get("total_embeddings", 0)
                ),
            },
            "queries": {
                "totalRecorded": ax.get("totalQueries", 0),
                "last24h": ax.get("queriesLast24h", 0),
                "last7d": ax.get("queriesLast7d", 0),
                "cacheHitRatePct": ax.get("cacheHitRate"),
                "answersDelivered": ax.get("answersDelivered", 0),
            },
            "updatedAt": _now_iso(),
        }
    )


@admin_bp.route("/analytics", methods=["GET"])
def analytics():
    """Real assistant analytics: volume, topics, questions, cache vs live."""
    range_key = request.args.get("range", "30d")
    days = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}.get(range_key, 30)
    payload = build_analytics_payload(days=days)
    payload["range"] = range_key
    return jsonify(payload)


@admin_bp.route("/activity", methods=["GET"])
def activity_feed():
    """Recent answered queries (from query_events.jsonl)."""
    return jsonify({"items": recent_events(100)})


@admin_bp.route("/logs", methods=["GET"])
def logs():
    q = (request.args.get("q") or "").lower()
    level = request.args.get("level") or "all"
    sort_dir = request.args.get("sort", "desc")

    raw = _read_log_tail(400)
    if not raw:
        raw = [f"{_now_iso()} | INFO | No assistant.log yet — queries will appear after traffic."]

    parsed = []
    for i, line in enumerate(raw):
        lvl = _parse_log_level(line)
        parsed.append(
            {
                "id": f"log-{i}",
                "timestamp": line[:26] if len(line) > 26 else _now_iso(),
                "level": lvl,
                "message": line,
            }
        )

    if q:
        parsed = [p for p in parsed if q in p["message"].lower()]
    if level != "all":
        parsed = [p for p in parsed if p["level"] == level]

    parsed.sort(key=lambda x: x["timestamp"], reverse=(sort_dir == "desc"))
    return jsonify({"logs": parsed})


def _load_alert_thresholds() -> dict:
    """
    Load alert thresholds from admin_alert_rules.json.
    Returns defaults if the file is absent or unreadable.
    """
    defaults = {"cpu": 85, "memory": 88, "disk": 90}
    path = os.path.join(_BASE_DIR, "admin_alert_rules.json")
    if not os.path.isfile(path):
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as f:
            rules_data = json.load(f)
        for rule in rules_data.get("rules", []):
            metric = rule.get("metric")
            threshold = rule.get("threshold")
            enabled = rule.get("enabled", True)
            if metric in defaults and isinstance(threshold, (int, float)) and enabled:
                defaults[metric] = threshold
    except (json.JSONDecodeError, OSError):
        pass
    return defaults


@admin_bp.route("/performance", methods=["GET"])
def performance():
    if not psutil:
        return jsonify({
            "cpu": None,
            "memory": None,
            "disk": None,
            "history": [],
            "alerts": [],
            "warning": "psutil is not installed — real metrics unavailable. Install with: pip install psutil",
        })

    cpu = psutil.cpu_percent(interval=0.15)
    mem = psutil.virtual_memory().percent
    try:
        disk = psutil.disk_usage("/").percent
    except OSError:
        disk = psutil.disk_usage(os.path.abspath(os.sep)).percent

    history = []
    now = time.time()
    for i in range(30):
        history.append(
            {
                "t": datetime.utcfromtimestamp(now - (29 - i) * 5).replace(microsecond=0).isoformat() + "Z",
                "cpu": round(cpu, 1),
                "memory": round(mem, 1),
            }
        )

    thresholds = _load_alert_thresholds()
    alerts = []
    if cpu > thresholds["cpu"]:
        alerts.append({"id": "cpu", "message": f"CPU above {thresholds['cpu']}%", "severity": "critical"})
    if mem > thresholds["memory"]:
        alerts.append({"id": "mem", "message": f"Memory above {thresholds['memory']}%", "severity": "warning"})
    if disk > thresholds["disk"]:
        alerts.append({"id": "disk", "message": f"Disk above {thresholds['disk']}%", "severity": "warning"})

    return jsonify(
        {
            "cpu": round(cpu, 1),
            "memory": round(mem, 1),
            "disk": round(disk, 1),
            "history": history,
            "alerts": alerts,
        }
    )


@admin_bp.route("/notifications", methods=["GET"])
def notifications():
    """Surface performance + analytics highlights as inbox items."""
    ax = build_analytics_payload(days=7)
    items = []
    if ax.get("cacheHitRate") is not None:
        items.append(
            {
                "id": "n-cache",
                "title": "Cache hit rate (7d window)",
                "body": f"{ax['cacheHitRate']}% of recorded answers used Redis cache.",
                "time": _now_iso(),
                "read": True,
                "kind": "info",
            }
        )
    if ax.get("queriesLast24h", 0) > 0:
        items.append(
            {
                "id": "n-q24",
                "title": "Query volume",
                "body": f"{ax['queriesLast24h']} queries recorded in the last 24h.",
                "time": _now_iso(),
                "read": True,
                "kind": "info",
            }
        )
    if not items:
        items.append(
            {
                "id": "n-empty",
                "title": "No traffic yet",
                "body": "Use assistant.html or POST /query — events append to logs/query_events.jsonl",
                "time": _now_iso(),
                "read": False,
                "kind": "info",
            }
        )
    return jsonify({"notifications": items})


@admin_bp.route("/alerts/rules", methods=["GET", "PUT"])
def alert_rules():
    path = os.path.join(_BASE_DIR, "admin_alert_rules.json")
    if request.method == "GET":
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return jsonify(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
        return jsonify(
            {
                "rules": [
                    {"id": "r1", "metric": "cpu", "op": ">", "threshold": 85, "enabled": True},
                    {"id": "r2", "metric": "memory", "op": ">", "threshold": 88, "enabled": True},
                ]
            }
        )
    data = request.get_json() or {}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
    return jsonify({"ok": True})


@admin_bp.route("/settings", methods=["GET", "PUT"])
def settings():
    path = os.path.join(_BASE_DIR, "admin_settings.json")
    qc = _qc()
    redis_ok = bool(qc and qc.redis_service.is_connected()) if qc else False

    default = {
        "siteName": "JUST University Assistant",
        "maintenanceMode": False,
        "integrations": [
            {"id": "i1", "name": "OpenAI", "connected": bool(OPENAI_API_KEY)},
            {"id": "i2", "name": "Redis", "connected": redis_ok},
        ],
    }
    if request.method == "GET":
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    merged = {**default, **json.load(f)}
                    return jsonify(merged)
            except (json.JSONDecodeError, OSError):
                pass
        return jsonify(default)
    data = request.get_json() or {}
    merged = {**default, **data}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    except OSError:
        pass
    return jsonify(merged)


@admin_bp.route("/insights", methods=["GET"])
def insights():
    ax = build_analytics_payload(days=14)
    top_q = (ax.get("topQuestions") or [])[:3]
    top_t = (ax.get("topTopics") or [])[:3]
    summary_parts = []
    if ax.get("totalQueries"):
        summary_parts.append(
            f"Recorded {ax['totalQueries']} assistant queries in the selected window."
        )
    if ax.get("cacheHitRate") is not None:
        summary_parts.append(f"Redis cache served {ax['cacheHitRate']}% of answers.")
    if top_q:
        summary_parts.append('Most repeated question stem: "' + (top_q[0].get("question") or "")[:80] + '…"')
    summary = " ".join(summary_parts) if summary_parts else "No query events yet — traffic will populate analytics."

    anomalies = []
    if ax.get("totalQueries", 0) > 50 and (ax.get("cacheHitRate") or 0) < 15:
        anomalies.append(
            {
                "metric": "cache_hit_rate",
                "severity": "medium",
                "detail": "Low cache reuse — consider more alias coverage or seeding Redis.",
            }
        )

    return jsonify(
        {
            "summary": summary,
            "topQuestions": top_q,
            "topTopics": top_t,
            "anomalies": anomalies,
        }
    )


def register_admin_dashboard(app, query_controller=None):
    """Register API blueprint and static admin-ui. Pass the app's QueryController for live stats."""
    from flask import redirect, send_from_directory

    app.config["QUERY_CONTROLLER"] = query_controller
    app.register_blueprint(admin_bp)

    admin_dist = os.path.join(_BASE_DIR, "admin-ui")

    @app.route("/admin")
    def admin_no_slash():
        return redirect("/admin/", code=302)

    @app.route("/admin/")
    @app.route("/admin/<path:path>")
    def admin_spa(path=None):
        if not os.path.isdir(admin_dist) or not os.path.isfile(os.path.join(admin_dist, "index.html")):
            return (
                jsonify(
                    {
                        "message": "Admin UI missing. Expected folder with index.html at:",
                        "path": admin_dist,
                    }
                ),
                503,
            )
        if path:
            candidate = os.path.join(admin_dist, path)
            if os.path.isfile(candidate):
                return send_from_directory(admin_dist, path)
        return send_from_directory(admin_dist, "index.html")
