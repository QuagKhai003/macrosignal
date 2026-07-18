"""GDELT fetcher tests — canned payloads, throttle behavior.

@context  Batch 4.1: weekly aggregation arithmetic (hand-summed), the
          completed-weeks-only rule, headline dedupe, 429 backoff.
@done     Those cases + live smoke (marked integration, slow by throttle).
@todo     —
@limits   Default offline; pause injected as a no-op recorder.
@affects  src/fetchers/gdelt.py.
"""

import datetime as dt

import pytest

from src import db
from src.fetchers import gdelt

ENTRY = {
    "series_id": "news_heat", "source": "GDELT",
    "source_url": "https://example.com", "schedule": "weekly",
    "window": "fixed_threshold", "pub_lag_days": 0,
    "themes": {"wti": '"oil price"'},
}
TODAY = dt.date(2026, 7, 18)  # a Saturday; current ISO week is incomplete

TIMELINE = {"timeline": [{"data": [
    # week 2026-W27: Mon 06-29 .. Sun 07-05 -> 10 + 20 = 30
    {"date": "20260629000000", "value": 10},
    {"date": "20260703000000", "value": 20},
    # week 2026-W28: Mon 07-06 .. Sun 07-12 -> 40
    {"date": "20260708000000", "value": 40},
    # week 2026-W29 (current, incomplete) -> must NOT be stored
    {"date": "20260714000000", "value": 99},
]}]}

ARTICLES = {"articles": [
    {"title": "Oil surges on Middle East tensions", "seendate": "20260714T050000Z",
     "url": "https://x/1"},
    {"title": "Oil surges on Middle East tensions", "seendate": "20260714T090000Z",
     "url": "https://x/1b"},  # same title+date after trim: deduped
    {"title": "Crude climbs as war resumes", "seendate": "20260715T000000Z",
     "url": "https://x/2"},
    {"title": "", "seendate": "20260715T000000Z", "url": "https://x/3"},  # empty
]}


class FakeSession:
    def __init__(self, status_first=200):
        self.status_first, self.calls = status_first, 0

    def get(self, url, params, headers, timeout):
        self.calls += 1
        status = self.status_first if self.calls == 1 else 200

        class R:
            status_code = status
            def json(self, _mode=params["mode"]):
                return TIMELINE if _mode == "timelinevolraw" else ARTICLES
        return R()


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_weekly_aggregation_and_completed_weeks_only(conn):
    added = gdelt.fetch(ENTRY, conn, session=FakeSession(), today=TODAY,
                        pause=lambda s: None)
    vols = conn.execute(
        "SELECT data_date, value FROM observations WHERE series_id ="
        " 'news_vol_wti' ORDER BY data_date").fetchall()
    assert vols == [("2026-07-05", 30.0), ("2026-07-12", 40.0)]  # no W29
    heads = conn.execute("SELECT COUNT(*) FROM headlines").fetchone()[0]
    assert heads == 2  # dedupe + empty title dropped
    assert added == 4


def test_idempotent(conn):
    gdelt.fetch(ENTRY, conn, session=FakeSession(), today=TODAY,
                pause=lambda s: None)
    assert gdelt.fetch(ENTRY, conn, session=FakeSession(), today=TODAY,
                       pause=lambda s: None) == 0


def test_non_json_200_retries_like_throttle(conn):
    class ThrottlePageSession(FakeSession):
        def get(self, url, params, headers, timeout):
            self.calls += 1
            if self.calls == 1:
                class Bad:
                    status_code = 200
                    def json(self):
                        raise ValueError("html throttle page")
                return Bad()
            return super().get(url, params, headers, timeout)
    pauses = []
    added = gdelt.fetch(ENTRY, conn, session=ThrottlePageSession(),
                        today=TODAY, pause=pauses.append)
    assert added == 4
    assert gdelt._BACKOFF_S in pauses  # the bad response cost one backoff


def test_every_call_is_paced(conn):
    pauses = []
    gdelt.fetch(ENTRY, conn, session=FakeSession(), today=TODAY,
                pause=pauses.append)
    # 2 successful calls (timeline + artlist), each preceded by a pace pause
    assert pauses == [gdelt._PACE_S, gdelt._PACE_S]


def test_429_backs_off_then_succeeds(conn):
    pauses = []
    added = gdelt.fetch(ENTRY, conn, session=FakeSession(status_first=429),
                        today=TODAY, pause=pauses.append)
    assert added == 4
    assert gdelt._BACKOFF_S in pauses


def test_headlines_start_unlabeled(conn):
    gdelt.fetch(ENTRY, conn, session=FakeSession(), today=TODAY,
                pause=lambda s: None)
    labels = conn.execute("SELECT DISTINCT label FROM headlines").fetchall()
    assert labels == [(None,)]


@pytest.mark.integration
def test_live_wti_smoke(conn):
    from src import registry
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "news_heat")
    added = gdelt.fetch({**entry, "themes": {"wti": entry["themes"]["wti"]}},
                        conn, session=None)
    vols = conn.execute("SELECT COUNT(*) FROM observations WHERE series_id ="
                        " 'news_vol_wti'").fetchone()[0]
    assert vols > 40  # ~57 completed weeks in the 400-day lookback
    assert added > 40
