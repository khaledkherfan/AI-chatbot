#!/usr/bin/env python3
"""
Derive calendar-related .env values from a browser-saved JUST calendar HTML
(e.g. test.html): SharePoint canonical URL, _spPageContextInfo, and a quick
sanity check for the semester grid (grvSemCalendar).

CALENDAR_REQUEST_COOKIE cannot be extracted from a static file — the script
prints how to copy it from the browser.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


_BOT_MARKERS = (
    "please enable javascript",
    "enable javascript to view",
    "your support id is:",
)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    u = re.sub(r"^https://([^/:]+):443/", r"https://\1/", u, flags=re.I)
    return u


def _extract_canonical(html: str) -> str | None:
    m = re.search(
        r'<link\s+[^>]*rel\s*=\s*["\']canonical["\'][^>]*href\s*=\s*["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if not m:
        m = re.search(
            r'<link\s+[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*rel\s*=\s*["\']canonical["\']',
            html,
            re.I,
        )
    if not m:
        return None
    return _normalize_url(m.group(1))


def _extract_json_object_after_marker(html: str, marker: str) -> dict | None:
    """Brace-match a single {...} starting after marker (for quoted-key JS objects)."""
    i = html.find(marker)
    if i < 0:
        return None
    i = html.find("{", i + len(marker))
    if i < 0:
        return None
    depth = 0
    start = i
    for j in range(i, len(html)):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                blob = html[start : j + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def _looks_like_bot_wall(html: str) -> bool:
    t = html.lower()
    return any(m in t for m in _BOT_MARKERS)


def _calendar_page_url_from_context(ctx: dict | None, canonical: str | None) -> str | None:
    if canonical:
        return canonical
    if not ctx:
        return None
    site = (ctx.get("siteAbsoluteUrl") or "").rstrip("/")
    path = ctx.get("serverRequestPath") or ""
    if site and path.startswith("/"):
        return _normalize_url(site + path)
    wa = (ctx.get("webAbsoluteUrl") or "").rstrip("/")
    if wa and path.startswith("/"):
        # web is /ar/calendar; path is /ar/calendar/Pages/default.aspx
        return _normalize_url(wa.split("/ar/calendar")[0] + path if "/ar/" in path else wa + path)
    return None


def _referer_from_context(ctx: dict | None, page_url: str | None) -> str:
    if page_url:
        return page_url
    if ctx and ctx.get("webAbsoluteUrl"):
        return str(ctx["webAbsoluteUrl"]).rstrip("/") + "/"
    return "https://www.just.edu.jo/"


def _grid_row_estimate(html: str) -> tuple[int, bool]:
    """Count gv-row rows; confirm grvSemCalendar table exists (JUST test.html structure)."""
    has_grid = "grvSemCalendar" in html
    # Semester table uses class gv-row on data rows (header is gv-header).
    rows = len(re.findall(r'class="gv-row"', html))
    return rows, has_grid


def main() -> int:
    root = _project_root()
    p = argparse.ArgumentParser(
        description="Print CALENDAR_* .env lines from a saved calendar HTML (e.g. test.html)."
    )
    p.add_argument(
        "html_file",
        nargs="?",
        default=os.path.join(root, "test.html"),
        help="Path to saved HTML (default: <project>/test.html)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Also print a JSON summary to stdout (after the dotenv block).",
    )
    args = p.parse_args()

    path = os.path.abspath(args.html_file)
    if not os.path.isfile(path):
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()

    canonical = _extract_canonical(html)
    ctx = _extract_json_object_after_marker(html, "var _spPageContextInfo=")
    page_url = _calendar_page_url_from_context(ctx, canonical)
    referer = _referer_from_context(ctx, page_url)
    rows, has_grid = _grid_row_estimate(html)
    bot = _looks_like_bot_wall(html)

    rel_for_env = os.path.relpath(path, root).replace("\\", "/")

    summary = {
        "html_file": path,
        "canonical_url": canonical,
        "sp_page_context_parsed": ctx is not None,
        "calendar_page_url": page_url,
        "calendar_request_referer": referer,
        "sp_web_title": (ctx or {}).get("webTitle"),
        "sp_server_time": (ctx or {}).get("serverTime"),
        "grvSemCalendar_present": has_grid,
        "gv_row_count_estimate": rows,
        "looks_like_waf_js_wall": bot,
    }

    print("# --- Paste into .env (values derived from saved HTML) ---")
    if page_url:
        print(f"CALENDAR_PAGE_URL={page_url}")
    else:
        print("# CALENDAR_PAGE_URL=   # could not parse canonical or _spPageContextInfo")
    print(f"CALENDAR_REQUEST_REFERER={referer}")
    print(f"CALENDAR_HTML_FILE={rel_for_env}")
    print(
        "# CALENDAR_REQUEST_COOKIE=   # not in HTML: DevTools -> Network -> default.aspx "
        "-> Request Headers -> copy full Cookie"
    )
    print("# ---")
    if bot:
        print(
            "# WARNING: This file looks like a WAF/bot JS wall, not the real calendar. "
            "Save again from the browser after the table loads, or use a live Cookie.",
            file=sys.stderr,
        )
    if has_grid and rows == 0 and not bot:
        print(
            "# NOTE: grvSemCalendar id found but no gv-row — page may be incomplete.",
            file=sys.stderr,
        )
    if has_grid and rows > 0:
        print(f"# OK: ~{rows} calendar table row(s) detected (class gv-row).", file=sys.stderr)

    if args.json:
        # Use ASCII escapes so JSON prints on Windows cp1252 consoles.
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
