"""GDELT Cloud fallback — keyed headlines when the free Project API refuses.

@context  api.gdeltproject.org rate-limits shared addresses; GDELT Cloud
          (gdeltcloud.com) sells keyed per-organization quotas. As a FALLBACK
          it supplies HEADLINES ONLY: Cloud counts story clusters while the
          Project counts articles, and mixing scales would corrupt the F6
          volume ratio — so volume rows are never written here (F6 degrades
          gracefully; F7's greed ratio only needs >=30 titles, scale-free).
@done     fetch_theme_headlines(): semantic search over the theme keywords,
          trailing window, English, paginated (<=3 pages), stored into the
          headlines table (same dedupe as the Project route). KEY ROTATION:
          the free tier is 100 req/month/key, so cloud_fallback.key_envs lists
          several keys; a quota-exhausted key (429 / PLAN_REQUIRED / 403) rolls
          to the next, mid-request; only all-keys-exhausted raises.
@todo     Revisit if Cloud exposes an article-count timeline someday.
@limits   Needs >=1 key from cloud_fallback.key_envs present. 30-day window
          cap per their docs. Raises FetchError only when every key is out.
@affects  src/fetchers/gdelt.py (invokes on Project failure); headlines table.
"""

import datetime as dt
import re
import sqlite3
import time

import requests

from src import config

MAX_PAGES = 3
PAGE_LIMIT = 100
QUOTA_CODES = (402, 403, 429)  # quota / plan-required / rate: roll to next key
RETRIES_PER_KEY = 3            # try a declined key a few times before rolling
RETRY_BACKOFF_S = 5


class FetchError(RuntimeError):
    pass


def _keys(cfg) -> list[str]:
    names = cfg.get("key_envs") or ([cfg["key_env"]] if cfg.get("key_env")
                                    else [])
    present = [(n, config.get_key(n)) for n in names]
    return [(n, k) for n, k in present if k]


def fetch_theme_headlines(entry: dict, conn: sqlite3.Connection, theme: str,
                          start: dt.date, end: dt.date, session=None,
                          pause=time.sleep) -> int:
    cfg = entry.get("cloud_fallback")
    if not cfg:
        raise FetchError("no cloud_fallback configured")
    keys = _keys(cfg)
    if not keys:
        raise FetchError("no GDELT Cloud key present in "
                         f"{cfg.get('key_envs') or [cfg.get('key_env')]}")
    session = session or requests.Session()
    search_text = _plain_text(entry["themes"][theme])
    base_params = {"search": search_text, "date_start": start.isoformat(),
                   "date_end": end.isoformat(), "languages": "en",
                   "sort": "recent", "limit": str(PAGE_LIMIT)}

    last = "no key tried"
    for name, key in keys:  # rotate: each key retried, then roll to the next
        for attempt in range(RETRIES_PER_KEY):
            try:
                return _run(session, conn, theme, cfg["url"], key, base_params)
            except _QuotaExhausted as exc:
                last = f"{name}: {exc}"
                if attempt < RETRIES_PER_KEY - 1:
                    pause(RETRY_BACKOFF_S)  # transient decline: retry same key
    raise FetchError(f"gdeltcloud: all {len(keys)} keys exhausted ({last})")


class _QuotaExhausted(RuntimeError):
    pass


def _run(session, conn, theme, url, key, base_params) -> int:
    params = dict(base_params)
    added, cursor = 0, None
    for _page in range(MAX_PAGES):
        if cursor:
            params["cursor"] = cursor
        resp = session.get(url, params=params, timeout=90,
                           headers={"Authorization": f"Bearer {key}"})
        if resp.status_code in QUOTA_CODES:
            raise _QuotaExhausted(f"HTTP {resp.status_code}")
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
            top = story.get("top_articles") or []
            url_field = top[0].get("url", "") if top else ""
            cur = conn.execute(
                "INSERT OR IGNORE INTO headlines (theme, seen_date, title,"
                " source_url) VALUES (?, ?, ?, ?)",
                (theme, seen, title, url_field))
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
