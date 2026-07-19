"""EDGAR events fetcher tests (Tier-3 A4 — activist stakes + sale-intent).

@context  SC 13D activist stakes + Form 144 sale-intent on the equity
          universe → edgar_events → world picture event lines. Context only.
@done     13D/amendment + 144 match, 13G/144-amendment exclusion, lookback,
          idempotency, empty-universe no-op, unknown ticker loud, recent_
          events as-of, world line.
@limits   Offline via url-keyed fake session.
@affects  src/fetchers/edgarevents.py, src/worldview.py.
"""

import pytest

from src import db, worldview
from src.fetchers import edgarevents

TICKERS_JSON = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NV"},
                "1": {"cik_str": 2488, "ticker": "AMD", "title": "AMD"}}


def subs(forms):
    f, a, d = (list(x) for x in zip(*forms)) if forms else ([], [], [])
    return {"filings": {"recent": {"form": f, "accessionNumber": a,
                                   "filingDate": d}}}


class FakeSession:
    def __init__(self, by_cik):
        self._by_cik = by_cik  # cik-substring -> submissions dict

    def get(self, url, headers, timeout):
        if "company_tickers" in url:
            payload = TICKERS_JSON
        else:
            payload = next((v for k, v in self._by_cik.items() if k in url),
                           subs([]))

        class R:
            status_code = 200
            def json(self, _p=payload):
                return _p
        return R()


ENTRY = {"insider_tickers": ["NVDA"]}


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


NVDA_FORMS = subs([
    ("SC 13D", "0001-26-000010", "2026-07-05"),        # activist -> kept
    ("SC 13D/A", "0001-26-000011", "2026-07-08"),      # amendment -> kept
    ("SC 13G", "0001-26-000012", "2026-07-06"),        # passive -> dropped
    ("144", "0001-26-000013", "2026-07-07"),           # sale-intent -> kept
    ("144/A", "0001-26-000014", "2026-07-09"),         # 144 amendment -> drop
    ("4", "0001-26-000015", "2026-07-07"),             # form 4 -> not ours
    ("SC 13D", "0001-25-000001", "2025-01-01"),        # stale -> dropped
])


def test_matches_and_filters(conn):
    s = FakeSession({"CIK0001045810": NVDA_FORMS})
    added = edgarevents.fetch(ENTRY, conn, session=s,
                              today=__import__("datetime").date(2026, 7, 19))
    assert added == 3  # two 13D(+amend) + one 144
    rows = conn.execute("SELECT label, filing_date FROM edgar_events"
                        " ORDER BY filing_date").fetchall()
    assert rows == [("activist stake", "2026-07-05"),
                    ("insider sale-intent", "2026-07-07"),
                    ("activist stake", "2026-07-08")]


def test_idempotent(conn):
    s = FakeSession({"CIK0001045810": NVDA_FORMS})
    import datetime as dt
    edgarevents.fetch(ENTRY, conn, session=s, today=dt.date(2026, 7, 19))
    assert edgarevents.fetch(ENTRY, conn, session=s,
                             today=dt.date(2026, 7, 19)) == 0


def test_empty_universe_noop(conn):
    assert edgarevents.fetch({"insider_tickers": []}, conn, session=None) == 0


def test_unknown_ticker_raises(conn):
    import datetime as dt
    with pytest.raises(edgarevents.FetchError, match="unknown ticker"):
        edgarevents.fetch({"insider_tickers": ["ZZZZ"]}, conn,
                          session=FakeSession({}), today=dt.date(2026, 7, 19))


def test_recent_events_as_of(conn):
    conn.executemany("INSERT INTO edgar_events VALUES (?,?,?,?)", [
        ("NVDA", "activist stake", "2026-07-05", "a1"),
        ("AMD", "insider sale-intent", "2026-07-06", "a2"),
        ("NVDA", "activist stake", "2026-01-01", "a3")])  # outside 120d
    ev = edgarevents.recent_events(conn, "2026-07-19")
    assert ev == {"activist stake": ["NVDA"],
                  "insider sale-intent": ["AMD"]}


def test_worldview_event_line():
    lines = worldview.lines({}, "GREEN", {}, edgar_events={
        "activist stake": ["NVDA"], "insider sale-intent": ["AMD", "INTC"]})
    text = "\n".join(lines)
    assert "Recent activist stake filing(s): NVDA." in text
    assert "Recent insider sale-intent filing(s): AMD, INTC." in text
