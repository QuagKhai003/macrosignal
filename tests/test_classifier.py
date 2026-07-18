"""Cage tests for the classifier — the Golden Rule enforced by assertion.

@context  Batch 4.2/4.4: temperature literally 0 in every request, frozen
          prompt, strict parse ('error' never guessed into a sentiment),
          provider:model audit stamps, NULL-rows-only idempotency, sticky
          failover NIM -> OpenRouter with a journal flag.
@done     Those. Live determinism test marked integration + skipped without
          any provider key.
@todo     —
@limits   Default offline via injected session chains.
@affects  src/classifier.py.
"""

import pytest

from src import classifier, config, db

ENTRY = {
    "prompt_version": "v1.0",
    "providers": [
        {"name": "nim", "url": "https://nim.example/v1",
         "key_env": "NIM_API_KEY", "model": "meta/llama-3.1-8b-instruct"},
        {"name": "openrouter", "url": "https://or.example/v1",
         "key_env": "OPENROUTER_API_KEY",
         "model": "meta-llama/llama-3.1-8b-instruct"},
    ],
}


class FakeResponse:
    def __init__(self, content="neutral", status=200):
        self._content, self.status_code = content, status

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeSession:
    def __init__(self, replies=None, statuses=None):
        self.bodies = []
        self._replies = replies or ["neutral"]
        self._statuses = statuses or []
        self._n = 0

    def post(self, body, timeout):
        self.bodies.append(body)
        status = self._statuses[self._n] if self._n < len(self._statuses) else 200
        reply = self._replies[min(self._n, len(self._replies) - 1)]
        self._n += 1
        return FakeResponse(reply, status)


def chain(session, name="nim", model="meta/llama-3.1-8b-instruct"):
    return [(name, model, session)]


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for title in ["Gold soars to record!", "Crash fears grip oil",
                  "Corn unchanged"]:
        conn.execute("INSERT INTO headlines (theme, seen_date, title)"
                     " VALUES ('gold', '2026-07-14', ?)", (title,))
    yield conn
    conn.close()


def test_cage_temperature_zero_and_frozen_prompt(conn):
    session = FakeSession(replies=["excited", "scared", "neutral"])
    classifier.classify_pending(conn, ENTRY, sessions=chain(session))
    for body in session.bodies:
        assert body["temperature"] == 0
        assert body["max_tokens"] == 5
        assert "exactly one word" in body["messages"][0]["content"]
    assert "Gold soars to record!" in session.bodies[0]["messages"][0]["content"]


def test_labels_stamped_with_provider_model_and_version(conn):
    session = FakeSession(replies=["Excited.", "SCARED\n", "gibberish output"])
    counts = classifier.classify_pending(conn, ENTRY, sessions=chain(session))
    assert counts == {"excited": 1, "scared": 1, "neutral": 0, "error": 1}
    rows = conn.execute("SELECT label, model, prompt_version FROM headlines"
                        " ORDER BY headline_id").fetchall()
    stamp = "nim:meta/llama-3.1-8b-instruct"
    assert rows == [("excited", stamp, "v1.0"), ("scared", stamp, "v1.0"),
                    ("error", stamp, "v1.0")]


def test_only_null_rows_processed(conn):
    classifier.classify_pending(conn, ENTRY, sessions=chain(FakeSession()))
    session2 = FakeSession()
    counts = classifier.classify_pending(conn, ENTRY, sessions=chain(session2))
    assert session2.bodies == [] and sum(counts.values()) == 0


def test_failover_to_second_provider_with_journal_flag(conn):
    primary = FakeSession(statuses=[500, 500])         # dies even after retry
    fallback = FakeSession(replies=["neutral"])
    sessions = [("nim", "m1", primary), ("openrouter", "m2", fallback)]
    counts = classifier.classify_pending(conn, ENTRY, sessions=sessions,
                                         pause=lambda s: None)
    assert sum(counts.values()) == 3
    stamps = {r[0] for r in conn.execute("SELECT model FROM headlines")}
    assert stamps == {"openrouter:m2"}
    flag = conn.execute("SELECT detail FROM journal WHERE event_type ="
                        " 'flag'").fetchone()[0]
    assert "failover nim -> openrouter" in flag


def test_all_providers_down_raises_but_keeps_earned_labels(conn):
    ok_then_dead = FakeSession(replies=["excited"], statuses=[200, 500, 500])
    sessions = [("nim", "m1", ok_then_dead)]
    with pytest.raises(classifier.ClassifyError, match="all providers failed"):
        classifier.classify_pending(conn, ENTRY, sessions=sessions,
                                    pause=lambda s: None)
    labeled = conn.execute("SELECT COUNT(*) FROM headlines WHERE label IS"
                           " NOT NULL").fetchone()[0]
    assert labeled == 1  # the first label survived the crash


def test_retry_once_on_throttle(conn):
    session = FakeSession(replies=["neutral"], statuses=[429, 200])
    pauses = []
    classifier.classify_pending(conn, ENTRY, sessions=chain(session),
                                pause=pauses.append)
    assert pauses == [5]


def test_no_keys_raises(conn, monkeypatch, tmp_path):
    monkeypatch.delenv("NIM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(classifier.ClassifyError, match="no classification"):
        classifier.classify_pending(conn, ENTRY)


def test_unknown_prompt_version_raises(conn):
    with pytest.raises(classifier.ClassifyError, match="unknown prompt"):
        classifier.classify_pending(conn, {**ENTRY, "prompt_version": "v9"},
                                    sessions=chain(FakeSession()))


@pytest.mark.integration
@pytest.mark.skipif(not (config.get_key("NIM_API_KEY")
                         or config.get_key("OPENROUTER_API_KEY")),
                    reason="no provider key configured")
def test_live_determinism_same_headlines_twice(conn):
    """Build plan Phase 4 acceptance: identical labels on a second pass."""
    from src import registry
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "news_heat")
    name, model, session = classifier._build_chain(entry)[0]
    titles = [r[0] for r in conn.execute(
        "SELECT title FROM headlines ORDER BY headline_id")]
    first, second = [], []
    for out in (first, second):
        for title in titles:
            out.append(classifier._classify_one(
                session, model, "v1.0", title, lambda s: None))
    assert first == second
    assert first[0] in classifier.LABELS
