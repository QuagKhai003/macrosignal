"""GDELT Cloud fallback — keyed headlines when the free Project API refuses.

@context  api.gdeltproject.org rate-limits shared addresses; GDELT Cloud
          (gdeltcloud.com) sells keyed per-organization quotas. As a FALLBACK
          it supplies HEADLINES ONLY: Cloud counts story clusters while the
          Project counts articles, and mixing scales would corrupt the F6
          volume ratio — so volume rows are never written here (F6 degrades
          gracefully; F7's greed ratio only needs >=30 titles, scale-free).
@done     fetch_theme_headlines(): semantic search over the theme keywords,
          trailing window, English, paginated (<=3 pages), stored into the
          headlines table (same dedupe as the Project route).
@todo     Revisit if Cloud exposes an article-count timeline someday.
@limits   Requires the key named by cloud_fallback.key_env (user-created:
          GDLTE_CLOUD_API_KEY). 30-day window cap per their docs. Raises
          FetchError loud; the caller decides what failure means.
@affects  src/fetchers/gdelt.py (invokes on Project failure); headlines table.
"""

import datetime as dt
import re
import sqlite3

import requests

from src import config

MAX_PAGES = 3
PAGE_LIMIT = 100


class FetchError(RuntimeError):
    pass


def fetch_theme_headlines(entry: dict, conn: sqlite3.Connection, theme: str,
                          start: dt.date, end: dt.date, session=None) -> int:
    cfg = entry.get("cloud_fallback")
    if not cfg:
        raise FetchError("no cloud_fallback configured")
    key = config.get_key(cfg["key_env"])
    if not key:
        raise FetchError(f"{cfg['key_env']} missing from environment/.env")
    session = session or requests.Session()

    search_text = _plain_text(entry["themes"][theme])
    params = {"search": search_text, "date_start": start.isoformat(),
              "date_end": end.isoformat(), "languages": "en",
              "sort": "recent", "limit": str(PAGE_LIMIT)}
    added = 0
    cursor = None
    for _page in range(MAX_PAGES):
        if cursor:
            params["cursor"] = cursor
        resp = session.get(cfg["url"], params=params, timeout=90,
                           headers={"Authorization": f"Bearer {key}"})
        if resp.status_code != 200:
            raise FetchError(f"gdeltcloud: HTTP {resp.status_code}")
        try:
            payload = resp.json()
            stories = payload["data"]
        except (KeyError, ValueError, TypeError) as exc:
            raise FetchError("gdeltcloud: unexpected payload") from exc
        for story in stories:
            title = (story.get("title") or "").strip()
            seen = story.get("story_date") or ""
            if not title or len(seen) != 10:
                continue
            url = ""
            top = story.get("top_articles") or []
            if top:
                url = top[0].get("url", "")
            cur = conn.execute(
                "INSERT OR IGNORE INTO headlines (theme, seen_date, title,"
                " source_url) VALUES (?, ?, ?, ?)", (theme, seen, title, url))
            added += cur.rowcount
        cursor = (payload.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
    conn.commit()
    return added


def _plain_text(query: str) -> str:
    """'\"gold price\" OR \"gold rally\"' -> 'gold price gold rally' — the
    Cloud search is semantic free text, not boolean."""
    words = re.sub(r'["()]', " ", query).replace(" OR ", " ")
    return " ".join(words.split())
