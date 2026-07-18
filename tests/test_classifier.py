"""Cage tests for the classifier — the Golden Rule enforced by assertion.

@context  Batch 4.2: temperature literally 0 in every request, frozen prompt
          text, strict label parse ('error' never guessed into a sentiment),
          audit stamps, NULL-rows-only idempotency, retry-once.
@done     Those. Live determinism test marked integration + skipped without
          the key.
@todo     —
@limits   Default offline via fake session.
@affects  src/classifier.py.
"""

import pytest

from src import classifier, config, db

ENTRY = {"model": "meta/llama-3.1-8b-instruct", "prompt_version": "v1.0"}


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

    def post(self, url, json, timeout):
        self.bodies.append(json)
        status = self._statuses[self._n] if self._n < len(self._statuses) else 200
        reply = self._replies[min(self._n, len(self._replies) - 1)]
        self._n += 1
        return FakeResponse(reply, status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for i, title in enumerate(["Gold soars to record!", "Crash fears grip oil",
                               "Corn unchanged"]):
        conn.execute("INSERT INTO headlines (theme, seen_date, title)"
                     " VALUES ('gold', '2026-07-14', ?)", (title,))
    yield conn
    conn.close()


def test_cage_temperature_zero_and_frozen_prompt(conn):
    session = FakeSession(replies=["excited", "scared", "neutral"])
    classifier.classify_pending(conn, ENTRY, session=session)
    for body in session.bodies:
        assert body["temperature"] == 0
        assert body["max_tokens"] == 5
        assert "exactly one word" in body["messages"][0]["content"]
    assert "Gold soars to record!" in session.bodies[0]["messages"][0]["content"]


def test_labels_stamped_with_model_and_version(conn):
    session = FakeSession(replies=["Excited.", "SCARED\n", "gibberish output"])
    counts = classifier.classify_pending(conn, ENTRY, session=session)
    assert counts == {"excited": 1, "scared": 1, "neutral": 0, "error": 1}
    rows = conn.execute("SELECT label, model, prompt_version FROM headlines"
                        " ORDER BY headline_id").fetchall()
    assert rows == [
        ("excited", ENTRY["model"], "v1.0"),
        ("scared", ENTRY["model"], "v1.0"),
        ("error", ENTRY["model"], "v1.0")]


def test_only_null_rows_processed(conn):
    classifier.classify_pending(conn, ENTRY, session=FakeSession())
    session2 = FakeSession()
    counts = classifier.classify_pending(conn, ENTRY, session=session2)
    assert session2.bodies == [] and sum(counts.values()) == 0


def test_retry_once_on_throttle(conn):
    session = FakeSession(replies=["neutral"], statuses=[429, 200, 200, 200])
    pauses = []
    classifier.classify_pending(conn, ENTRY, session=session,
                                pause=pauses.append)
    assert pauses == [5]


def test_hard_error_raises(conn):
    with pytest.raises(classifier.ClassifyError, match="HTTP 401"):
        classifier.classify_pending(conn, ENTRY,
                                    session=FakeSession(statuses=[401]))


def test_unknown_prompt_version_raises(conn):
    with pytest.raises(classifier.ClassifyError, match="unknown prompt"):
        classifier.classify_pending(conn, {**ENTRY, "prompt_version": "v9"},
                                    session=FakeSession())


def test_missing_key_raises(conn, monkeypatch, tmp_path):
    monkeypatch.delenv("NIM_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(classifier.ClassifyError, match="NIM_API_KEY"):
        classifier.classify_pending(conn, ENTRY)


@pytest.mark.integration
@pytest.mark.skipif(not config.get_key("NIM_API_KEY"),
                    reason="NIM_API_KEY not configured")
def test_live_determinism_same_headlines_twice(conn):
    """Build plan Phase 4 acceptance: identical labels on a second pass."""
    titles = [r[0] for r in conn.execute(
        "SELECT title FROM headlines ORDER BY headline_id")]
    first, second = [], []
    import requests
    session = classifier._KeyedSession(requests.Session(),
                                       config.get_key("NIM_API_KEY"))
    for out in (first, second):
        for title in titles:
            out.append(classifier._classify_one(
                session, ENTRY["model"], "v1.0", title, lambda s: None))
    assert first == second
    assert first[0] in classifier.LABELS  # sane label on obvious greed
