"""The caged LLM — headline labels, nothing else.

@context  The ONE place the machine calls an LLM (Golden Rule). Every
          headline gets exactly one of {excited, scared, neutral} at
          temperature 0 with a frozen, versioned prompt; every label is
          written onto its headline row (the audit log) stamped
          "provider:model". The LLM never produces a number: ratios are
          computed by src/newsscore.py deterministically. Providers are a
          CONFIG CHAIN (signals.yaml `providers:`, both OpenAI-compatible):
          NIM primary, OpenRouter fallback — user decision 2026-07-18.
@done     PROMPTS registry (v1.0 frozen); classify_pending(): NULL-label rows
          only (idempotent), temp 0 + seed 0, strict one-word parse,
          unparseable -> 'error' (excluded, never guessed); providers without
          keys skipped; sticky mid-run failover to the next provider with a
          journal flag recording it; raises only when every provider is out.
@todo     Nothing — a new prompt is a NEW dict entry, never an edit to v1.0.
@limits   Determinism holds per (provider:model, prompt) pair — the audit
          stamp on each row says exactly which pair produced it.
@affects  headlines table (+ journal on failover); src/newsscore.py;
          weekly_run.
"""

import re
import sqlite3
import time

import requests

from src import config

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


def classify_pending(conn: sqlite3.Connection, entry: dict, sessions=None,
                     limit: int = 500, pause=time.sleep) -> dict:
    """Label up to `limit` unlabeled headlines. Returns counts per label.
    sessions: optional [(name, model, session)] override for tests."""
    version = entry["prompt_version"]
    if version not in PROMPTS:
        raise ClassifyError(f"unknown prompt version {version}")
    rows = conn.execute(
        "SELECT headline_id, title FROM headlines WHERE label IS NULL"
        " ORDER BY headline_id LIMIT ?", (limit,)).fetchall()
    counts = {label: 0 for label in (*LABELS, "error")}
    if not rows:
        return counts  # nothing pending: no keys needed, no calls made

    chain = sessions if sessions is not None else _build_chain(entry)
    if not chain:
        raise ClassifyError("no classification provider has a key configured")

    active = 0
    for headline_id, title in rows:
        while True:
            name, model, session = chain[active]
            try:
                label = _classify_one(session, model, version, title, pause)
                break
            except ClassifyError as exc:
                if active + 1 >= len(chain):
                    conn.commit()  # keep the labels earned so far
                    raise ClassifyError(
                        f"all providers failed; last ({name}): {exc}") from exc
                conn.execute(
                    "INSERT INTO journal (date, market_id, event_type,"
                    " detail, price_at_event) VALUES (date('now'), NULL,"
                    " 'flag', ?, NULL)",
                    (f"classifier failover {name} -> {chain[active + 1][0]}:"
                     f" {exc}",))
                active += 1
        conn.execute(
            "UPDATE headlines SET label = ?, model = ?, prompt_version = ?"
            " WHERE headline_id = ?",
            (label, f"{name}:{model}", version, headline_id))
        counts[label] += 1
    conn.commit()
    return counts


def _build_chain(entry) -> list:
    chain = []
    for p in entry["providers"]:
        key = config.get_key(p["key_env"])
        if key:
            chain.append((p["name"], p["model"],
                          _KeyedSession(requests.Session(), p["url"], key,
                                        int(p.get("max_tokens", 5)))))
    return chain


def _classify_one(session, model, version, title, pause) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user",
                      "content": PROMPTS[version].format(headline=title)}],
        "temperature": 0,
        "max_tokens": getattr(session, "max_tokens", 5),
        "seed": 0,
    }
    for attempt in (0, 1):
        resp = session.post(body, timeout=120)
        if resp.status_code == 200:
            break
        if attempt == 0 and resp.status_code in (429, 500, 502, 503):
            pause(5)
            continue
        raise ClassifyError(f"HTTP {resp.status_code}")
    try:
        message = resp.json()["choices"][0]["message"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ClassifyError("unexpected payload") from exc
    label = _parse_label(message.get("content") or "")
    if label == "error":
        # some reasoning APIs put the words in a separate reasoning field
        label = _parse_label(message.get("reasoning_content")
                             or message.get("reasoning") or "")
    return label


def _parse_label(text: str) -> str:
    """Exactly one DISTINCT label word anywhere in the reply -> that label
    (handles both bare one-word answers and reasoning models that think out
    loud before concluding). None, or several different ones -> 'error' —
    an ambiguous reply is never guessed into a sentiment."""
    found = {w for w in re.findall(r"\b(excited|scared|neutral)\b",
                                   (text or "").lower())}
    return found.pop() if len(found) == 1 else "error"


class _KeyedSession:
    def __init__(self, session: requests.Session, url: str, key: str,
                 max_tokens: int = 5):
        self._session, self._url, self._key = session, url, key
        self.max_tokens = max_tokens

    def post(self, body, timeout):
        return self._session.post(
            self._url, json=body, timeout=timeout,
            headers={"Authorization": f"Bearer {self._key}"})
