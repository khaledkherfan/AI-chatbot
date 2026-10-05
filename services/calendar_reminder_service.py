"""
Academic calendar: fetch official page (or HTML snapshot), parse semester grid
from HTML when present, else structure with GPT; Redis cache (7d); per-user
reminders (authenticated user_id).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import requests

from config import (
    CALENDAR_CACHE_TTL_SECONDS,
    CALENDAR_HTML_FILE,
    CALENDAR_PAGE_URL,
    CALENDAR_REMINDERS_TTL_SECONDS,
    CALENDAR_REQUEST_COOKIE,
    CALENDAR_REQUEST_REFERER,
)
from logger import get_logger

REDIS_KEY_CALENDAR = "calendar:academic_events_v1"
REDIS_PREFIX_REMINDERS = "calendar:reminders:"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.9",
}

_USER_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_MAX_REMINDERS_PER_USER = 40

_CAL_BOT_MARKERS = (
    "please enable javascript",
    "enable javascript to view",
    "your support id is:",
)


def _calendar_looks_like_bot_wall(plain: str) -> bool:
    t = (plain or "").lower()
    return any(m in t for m in _CAL_BOT_MARKERS)


class _HTMLToText(HTMLParser):
    # Skip script/style/svg/iframe only. Do NOT skip noscript: many sites (incl. JUST WAF)
    # put the only human-readable fallback text inside <noscript> when JS is required.
    _SKIP = frozenset({"script", "style", "svg", "iframe"})

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        s = (data or "").strip()
        if s:
            self._chunks.append(s)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _regex_plain_text_fallback(html: str) -> str:
    """Strip tags crudely when structured parse yields little text (WAF / odd markup)."""
    if not html:
        return ""
    t = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _html_to_plain_text(html: str) -> str:
    p = _HTMLToText()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return _regex_plain_text_fallback(html)
    out = p.text().strip()
    if len(out) < 50:
        fb = _regex_plain_text_fallback(html)
        if len(fb) > len(out):
            out = fb.strip()
    return out


def _calendar_semester_table_chunk(html: str) -> Optional[str]:
    """Slice the SharePoint semester calendar <table> that contains grvSemCalendar."""
    if not html or "grvSemCalendar" not in html:
        return None
    pos = html.find("grvSemCalendar")
    start = html.rfind("<table", 0, pos)
    if start < 0:
        return None
    end = html.find("</table>", pos)
    if end < 0:
        return None
    return html[start : end + len("</table>")]


def _inner_text_from_td_fragment(raw: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", raw or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def _td_cells_from_tr_inner(row_inner: str) -> List[str]:
    return [
        _inner_text_from_td_fragment(m.group(1))
        for m in re.finditer(r"<td\b[^>]*>([\s\S]*?)</td>", row_inner or "", re.I)
    ]


def _parse_just_date_cell_to_iso(date_note: str) -> tuple[Optional[str], Optional[str]]:
    parts = re.findall(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_note or "")
    if not parts:
        return None, None

    def fmt(y: str, mo: str, d: str) -> str:
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    if len(parts) == 1:
        y, mo, d = parts[0]
        return fmt(y, mo, d), None
    y, mo, d = parts[0]
    y2, mo2, d2 = parts[-1]
    return fmt(y, mo, d), fmt(y2, mo2, d2)


def _guess_calendar_category(title_ar: str) -> str:
    t = title_ar or ""
    tl = t.lower()
    if any(x in t for x in ("امتحان", "اختبار", "شفهي", "سبر", "نظري")) or any(
        x in tl for x in ("exam", "midterm", "final", "quiz", "assessment")
    ):
        return "exams"
    if any(
        x in t for x in ("تسجيل", "إضافة", "انسحاب", "حذف", "تعليق", "استثناء")
    ) or any(
        x in tl
        for x in (
            "registration",
            "add/drop",
            "withdraw",
            "enroll",
            "enrolment",
            "enrollment",
        )
    ):
        return "registration"
    if any(x in t for x in ("عطلة", "إجازة", "عيد")) or any(
        x in tl for x in ("holiday", "vacation", "break", "eid")
    ):
        return "holiday"
    if any(
        x in t for x in ("الفصل", "بداية", "نهاية", "نهاية دوام", "آخر يوم")
    ) or any(
        x in tl for x in ("semester", "term begins", "term ends", "last day of")
    ):
        return "semester"
    return "general"


_LEGACY_CALENDAR_CATEGORY = {
    "تسجيل": "registration",
    "امتحانات": "exams",
    "عطلة": "holiday",
    "فصل_دراسي": "semester",
    "موعد_عام": "general",
    "أخرى": "other",
}

_VALID_CALENDAR_CATEGORIES = frozenset(
    {"registration", "exams", "holiday", "semester", "general", "other"}
)


def _normalize_event_category(raw: Any) -> str:
    if not isinstance(raw, str):
        return "general"
    s = raw.strip()
    if s in _LEGACY_CALENDAR_CATEGORY:
        return _LEGACY_CALENDAR_CATEGORY[s]
    if s in _VALID_CALENDAR_CATEGORIES:
        return s
    return "general"


def _normalize_calendar_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        row = dict(ev)
        row["category"] = _normalize_event_category(row.get("category"))
        out.append(row)
    return out


def _stable_calendar_event_id(date_note: str, title_ar: str, index: int) -> str:
    base = f"{index:04d}|{date_note}|{title_ar}".encode("utf-8", errors="replace")
    return "ev_" + hashlib.sha256(base).hexdigest()[:16]


def _academic_year_hint_from_calendar_html(html: str) -> Optional[str]:
    m = re.search(
        r'<select[^>]*\bddlYear\b[^>]*>([\s\S]*?)</select>',
        html or "",
        re.I,
    )
    if not m:
        return None
    block = m.group(1)
    mo2 = re.search(
        r'<option[^>]*\bselected[^>]*>([^<]*)</option>',
        block,
        re.I,
    )
    if not mo2:
        mo2 = re.search(r"<option[^>]+>([^<]*)</option>", block, re.I)
    if not mo2:
        return None
    s = (mo2.group(1) or "").strip()
    return s or None


def _count_semester_gv_rows(html: str) -> int:
    return len(
        re.findall(
            r'<tr[^>]*\bclass\s*=\s*["\'][^"\']*\bgv-row\b',
            html or "",
            re.I,
        )
    )


def _events_from_just_semester_grid_html(html: str) -> List[Dict[str, Any]]:
    """
    Deterministic parse of JUST SharePoint semester grid (grvSemCalendar / gv-row).
    Avoids LLM truncation when the full table is present in HTML.
    """
    chunk = _calendar_semester_table_chunk(html)
    if not chunk:
        return []
    rows = list(
        re.finditer(
            r'<tr\s[^>]*\bclass\s*=\s*["\'][^"\']*\bgv-row\b[^"\']*["\'][^>]*>([\s\S]*?)</tr>',
            chunk,
            re.I,
        )
    )
    events: List[Dict[str, Any]] = []
    for idx, m in enumerate(rows):
        cells = _td_cells_from_tr_inner(m.group(1))
        if len(cells) < 2:
            continue
        date_note = cells[0] or ""
        title_ar = (cells[1] or "").strip()
        details_raw = cells[2] if len(cells) > 2 else ""
        details_ar = details_raw if details_raw else None
        if not title_ar and not date_note:
            continue
        if not title_ar:
            title_ar = date_note or "Event"
        st, en = _parse_just_date_cell_to_iso(date_note)
        events.append(
            {
                "id": _stable_calendar_event_id(date_note, title_ar, idx),
                "title_ar": title_ar,
                "date_note_ar": date_note or None,
                "start_date": st,
                "end_date": en,
                "category": _guess_calendar_category(title_ar),
                "details_ar": details_ar,
            }
        )
    return events


def _fetch_calendar_headers() -> Dict[str, str]:
    """Headers for the official calendar page (optional session cookies from .env)."""
    h = dict(_FETCH_HEADERS)
    h["Referer"] = CALENDAR_REQUEST_REFERER or "https://www.just.edu.jo/"
    if CALENDAR_REQUEST_COOKIE:
        h["Cookie"] = CALENDAR_REQUEST_COOKIE
    return h


def _fetch_calendar_html(url: str) -> str:
    log = get_logger()
    cookie_set = bool(CALENDAR_REQUEST_COOKIE and CALENDAR_REQUEST_COOKIE.strip())
    log.info(
        "[calendar] fetch start url=%s CALENDAR_HTML_FILE=%s cookie_set=%s referer=%s",
        url,
        CALENDAR_HTML_FILE or "(none)",
        cookie_set,
        CALENDAR_REQUEST_REFERER or "(default just.edu.jo)",
    )
    if CALENDAR_HTML_FILE:
        path = (
            CALENDAR_HTML_FILE
            if os.path.isabs(CALENDAR_HTML_FILE)
            else os.path.join(_PROJECT_ROOT, CALENDAR_HTML_FILE)
        )
        if os.path.isfile(path):
            log.info("[calendar] reading file path=%s", path)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()
            log.info("[calendar] file read ok chars=%s", len(html))
            return html
        log.warning("[calendar] CALENDAR_HTML_FILE not found path=%s — using HTTP", path)
    if cookie_set:
        log.info("[calendar] HTTP GET with Cookie header (length=%s)", len(CALENDAR_REQUEST_COOKIE or ""))
    hdrs = _fetch_calendar_headers()
    r = requests.get(url, headers=hdrs, timeout=50, allow_redirects=True)
    log.info(
        "[calendar] HTTP response status=%s final_url=%s html_chars=%s encoding=%s",
        r.status_code,
        getattr(r, "url", url),
        len(r.text or ""),
        r.encoding or r.apparent_encoding or "?",
    )
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _normalize_remind_at(raw: str) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


class CalendarReminderService:
    def __init__(self, redis_service, openai_service):
        self.redis = redis_service
        self.openai = openai_service
        self.logger = get_logger()

    def _redis_ok(self) -> bool:
        return bool(self.redis and self.redis.is_connected() and self.redis.client)

    def _ttl_remaining(self, key: str) -> Optional[int]:
        if not self._redis_ok():
            return None
        try:
            t = self.redis.client.ttl(key)
            return int(t) if t is not None and t >= 0 else None
        except Exception:
            return None

    def get_events(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Return calendar payload; use Redis for up to CALENDAR_CACHE_TTL_SECONDS."""
        self.logger.info(
            "[calendar] get_events start force_refresh=%s redis_ok=%s",
            force_refresh,
            self._redis_ok(),
        )
        if (
            not force_refresh
            and self._redis_ok()
        ):
            try:
                raw = self.redis.client.get(REDIS_KEY_CALENDAR)
                if raw:
                    data = json.loads(raw)
                    ev = data.get("events") or []
                    if isinstance(ev, list):
                        data["events"] = _normalize_calendar_events(ev)
                    self.logger.info(
                        "[calendar] cache HIT key=%s events=%s ttl_remaining_s=%s",
                        REDIS_KEY_CALENDAR,
                        len(ev) if isinstance(ev, list) else "?",
                        self._ttl_remaining(REDIS_KEY_CALENDAR),
                    )
                    data["cached"] = True
                    data["cache_ttl_seconds_remaining"] = self._ttl_remaining(
                        REDIS_KEY_CALENDAR
                    )
                    return data
                self.logger.info("[calendar] cache key empty — will fetch fresh")
            except Exception as e:
                self.logger.warning("[calendar] cache read failed: %s", e)

        self.logger.info("[calendar] fetching HTML (cache miss or force_refresh)")
        html = _fetch_calendar_html(CALENDAR_PAGE_URL)
        plain = _html_to_plain_text(html)
        preview = (plain[:160] or "").replace("\n", " ").strip()
        if len(plain) > 160:
            preview += "…"
        bot = _calendar_looks_like_bot_wall(plain)
        self.logger.info(
            "[calendar] plain_text chars=%s bot_wall=%s preview=%r",
            len(plain),
            bot,
            preview,
        )
        if not plain or len(plain) < 50:
            self.logger.error("[calendar] plain text too short — abort")
            raise RuntimeError(
                "Could not extract enough plain text from the calendar page."
            )

        grid_rows = _count_semester_gv_rows(html)
        table_events = _events_from_just_semester_grid_html(html)
        parsed: Optional[Dict[str, Any]] = None
        min_ok = max(3, int(grid_rows * 0.85)) if grid_rows else 3
        if table_events and grid_rows and len(table_events) >= min_ok:
            self.logger.info(
                "[calendar] semester grid HTML parse ok events=%s gv_rows=%s — skip LLM",
                len(table_events),
                grid_rows,
            )
            parsed = {
                "academic_year_hint": _academic_year_hint_from_calendar_html(html),
                "events": table_events,
            }
        elif table_events and grid_rows:
            self.logger.warning(
                "[calendar] grid parse incomplete (%s/%s) — using LLM on plain text",
                len(table_events),
                grid_rows,
            )

        if parsed is None:
            self.logger.info("[calendar] calling LLM extract_academic_calendar_events …")
            parsed = self.openai.extract_academic_calendar_events(plain, CALENDAR_PAGE_URL)
            if not parsed:
                self.logger.error("[calendar] LLM returned no parsed data")
                raise RuntimeError(
                    "Could not parse the calendar with the language model. "
                    "Check the OpenAI API key and configuration."
                )

        events = parsed.get("events") or []
        if not isinstance(events, list):
            events = []
        events = _normalize_calendar_events(events)

        self.logger.info(
            "[calendar] parsed events=%s academic_year_hint=%r",
            len(events),
            (parsed.get("academic_year_hint") or "")[:80],
        )

        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload: Dict[str, Any] = {
            "source_url": CALENDAR_PAGE_URL,
            "fetched_at": fetched_at,
            "academic_year_hint": parsed.get("academic_year_hint"),
            "events": events,
            "cached": False,
            "cache_ttl_seconds_remaining": None,
        }
        if _calendar_looks_like_bot_wall(plain):
            payload["fetch_note"] = (
                "The official calendar page expects JavaScript; the server cannot download the full grid. "
                "The list below may be incomplete. Open the official link in a browser or follow department announcements."
            )
            self.logger.warning("[calendar] bot/WAF shell detected — fetch_note set")

        ttl = CALENDAR_CACHE_TTL_SECONDS if len(events) > 0 else 3600
        if self._redis_ok():
            try:
                self.redis.client.setex(
                    REDIS_KEY_CALENDAR,
                    ttl,
                    json.dumps(
                        {
                            "source_url": payload["source_url"],
                            "fetched_at": payload["fetched_at"],
                            "academic_year_hint": payload["academic_year_hint"],
                            "events": payload["events"],
                        },
                        ensure_ascii=False,
                    ),
                )
                payload["cache_ttl_seconds_remaining"] = ttl
                self.logger.info(
                    "[calendar] redis SETEX ok key=%s ttl_s=%s events=%s",
                    REDIS_KEY_CALENDAR,
                    ttl,
                    len(events),
                )
            except Exception as e:
                self.logger.error("[calendar] redis SETEX failed: %s", e)

        self.logger.info("[calendar] get_events done returning payload keys=%s", list(payload.keys()))
        return payload

    def _reminder_key(self, user_id: str) -> str:
        return f"{REDIS_PREFIX_REMINDERS}{user_id.lower()}"

    def _load_reminders(self, user_id: str) -> List[Dict[str, Any]]:
        if not self._redis_ok():
            return []
        try:
            raw = self.redis.client.get(self._reminder_key(user_id))
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception as e:
            self.logger.error(f"Reminder load failed: {e}")
            return []

    def _save_reminders(self, user_id: str, items: List[Dict[str, Any]]) -> bool:
        if not self._redis_ok():
            return False
        try:
            self.redis.client.setex(
                self._reminder_key(user_id),
                CALENDAR_REMINDERS_TTL_SECONDS,
                json.dumps(items, ensure_ascii=False),
            )
            return True
        except Exception as e:
            self.logger.error(f"Reminder save failed: {e}")
            return False

    @staticmethod
    def validate_user_id(user_id: str) -> bool:
        return bool(user_id and _USER_ID_RE.match(user_id.strip()))

    def list_reminders(self, user_id: str) -> List[Dict[str, Any]]:
        if not self.validate_user_id(user_id):
            return []
        return list(self._load_reminders(user_id))

    def add_reminder(self, user_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.validate_user_id(user_id):
            raise ValueError("Invalid user id.")
        remind_at = _normalize_remind_at(body.get("remind_at") or "")
        if not remind_at:
            raise ValueError("remind_at is invalid (expected ISO-8601).")
        title_ar = (body.get("title_ar") or "").strip()
        if not title_ar:
            raise ValueError("title_ar is required.")
        event_id = (body.get("event_id") or "").strip() or None

        items = self._load_reminders(user_id)
        if len(items) >= _MAX_REMINDERS_PER_USER:
            raise ValueError("Maximum reminders reached for this account.")

        rid = body.get("reminder_id")
        if rid and isinstance(rid, str) and rid.strip():
            reminder_id = rid.strip()
        else:
            reminder_id = str(uuid.uuid4())

        row = {
            "reminder_id": reminder_id,
            "event_id": event_id,
            "title_ar": title_ar[:500],
            "remind_at": remind_at,
            "created_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        items.append(row)
        if not self._save_reminders(user_id, items):
            raise RuntimeError("Could not save reminder (Redis is not connected).")
        return row

    def delete_reminder(self, user_id: str, reminder_id: str) -> bool:
        if not self.validate_user_id(user_id):
            return False
        rid = (reminder_id or "").strip()
        if not rid:
            return False
        items = self._load_reminders(user_id)
        new_items = [x for x in items if x.get("reminder_id") != rid]
        if len(new_items) == len(items):
            return False
        return self._save_reminders(user_id, new_items)
