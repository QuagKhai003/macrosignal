"""The caged LLM — headline labels, nothing else.

@context  The ONE place the machine calls an LLM (Golden Rule). Every
          headline gets exactly one of {excited, scared, neutral} at
          temperature 0 with a frozen, versioned prompt; every label is
          written onto its headline row (the audit log). The LLM never
          produces a number: ratios are computed by src/newsscore.py from
          these labels deterministically. Provider: NVIDIA NIM
          (OpenAI-compatible; user decision 2026-07-18).
@done     PROMPTS registry (v1.0 frozen — changing it = a new version + a
          logged event); classify_pending(): NULL-label rows only
          (idempotent), temp 0 + seed 0, strict one-word parse, unparseable
          -> 'error' (excluded from ratios, never guessed), model + prompt
          version stamped per row; retry once on throttle/5xx.
@todo     Nothing — this file should almost never change. A new prompt is a
          NEW dict entry, never an edit to v1.0.
@limits   Requires NIM_API_KEY unless a session is injected (tests). Labels
          update the headline row in place — that IS the audit record.
@affects  headlines table; src/newsscore.py (4.3); weekly_run.
"""

import sqlite3
import time

import requests

from src import config

API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
LABELS = ("excited", "scared", "neutral")

# FROZEN. v1.0 since 2026-07-18. Never edit an existing version.
PROMPTS = {
    "v1.0": (
        "You label financial news headlines by emotional tone.\n"
        "Answer with exactly one word: excited, scared, or neutral.\n"
        "excited = celebrates gains, urges buying, radiates greed or FOMO.\n"
        "scared = warns of losses, crisis, war, crash, or panic.\n"
        "neutral = factual, mixed, or emotionless.\n"
        "Headline: {headline}\n"
        "Answer:"),
}


class ClassifyError(RuntimeError):
    pass


def classify_pending(conn: sqlite3.Connection, entry: dict, session=None,
                     limit: int = 500, pause=time.sleep) -> dict:
    """Label up to `limit` unlabeled headlines. Returns counts per label."""
    model, version = entry["model"], entry["prompt_version"]
    if version not in PROMPTS:
        raise ClassifyError(f"unknown prompt version {version}")
    rows = conn.execute(
        "SELECT headline_id, title FROM headlines WHERE label IS NULL"
        " ORDER BY headline_id LIMIT ?", (limit,)).fetchall()
    counts = {label: 0 for label in (*LABELS, "error")}
    if not rows:
        return counts  # nothing pending: no key needed, no calls made
    if session is None:
        key = config.get_key("NIM_API_KEY")
        if not key:
            raise ClassifyError("NIM_API_KEY missing from environment/.env")
        session = _KeyedSession(requests.Session(), key)
    for headline_id, title in rows:
        label = _classify_one(session, model, version, title, pause)
        conn.execute(
            "UPDATE headlines SET label = ?, model = ?, prompt_version = ?"
            " WHERE headline_id = ?", (label, model, version, headline_id))
        counts[label] += 1
    conn.commit()
    return counts


def _classify_one(session, model, version, title, pause) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user",
                      "content": PROMPTS[version].format(headline=title)}],
        "temperature": 0,
        "max_tokens": 5,
        "seed": 0,
    }
    for attempt in (0, 1):
        resp = session.post(API_URL, json=body, timeout=120)
        if resp.status_code == 200:
            break
        if attempt == 0 and resp.status_code in (429, 500, 502, 503):
            pause(5)
            continue
        raise ClassifyError(f"NIM: HTTP {resp.status_code}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ClassifyError("NIM: unexpected payload") from exc
    word = text.strip().lower().split()[0].strip(".,!:;\"'") if text.strip() else ""
    return word if word in LABELS else "error"


class _KeyedSession:
    def __init__(self, session: requests.Session, key: str):
        self._session, self._key = session, key

    def post(self, url, json, timeout):
        return self._session.post(
            url, json=json, timeout=timeout,
            headers={"Authorization": f"Bearer {self._key}"})
