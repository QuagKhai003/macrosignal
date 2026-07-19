"""Treasury auction fetcher tests (niche actor A2 — foreign demand).

@context  Note-auction indirect-bidder share = foreign-official demand proxy.
@done     Share math, notes-only filter, same-day averaging, pagination,
          idempotency, HTTP + empty failures; foreign world-picture lines.
@limits   Offline via fake session.
@affects  src/fetchers/auctions.py, src/worldview.py.
"""

import pytest

from src import db, worldview
from src.fetchers import auctions

ENTRY = {"series_id": "treasury_auctions", "source": "TREASURY",
         "source_url": "https://example.com", "schedule": "weekly",
         "window": "rolling3y", "pub_lag_days": 0, "history_start": "2008-04-07"}


def rec(date, stype, ind, direct, dealer):
    return {"auction_date": date, "security_type": stype,
            "indirect_bidder_accepted": ind, "direct_bidder_accepted": direct,
            "primary_dealer_accepted": dealer}


PAGE = [
    rec("2026-07-01", "Note", "60", "20", "20"),   # share 0.60
    rec("2026-07-01", "Note", "40", "30", "30"),   # share 0.40 -> avg 0.50
    rec("2026-07-02", "Bill", "90", "5", "5"),     # bill: dropped
    rec("2026-07-08", "Note", "70", "10", "20"),   # share 0.70
]


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return {"data": self._data}


class FakeSession:
    def __init__(self, pages=None, status=200):
        self._pages = pages if pages is not None else [PAGE, []]
        self._status = status
        self.calls = 0

    def get(self, url, params, timeout):
        if self._status != 200:
            return FakeResponse([], self._status)
        i = params["page[number]"] - 1
        self.calls += 1
        return FakeResponse(self._pages[i] if i < len(self._pages) else [])


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_indirect_share_notes_only_and_averaged(conn):
    added = auctions.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2  # two note dates; the bill dropped
    rows = conn.execute(
        "SELECT data_date, value FROM observations WHERE series_id ="
        " 'auction_indirect_share' ORDER BY data_date").fetchall()
    assert rows == [("2026-07-01", pytest.approx(0.50)),
                    ("2026-07-08", pytest.approx(0.70))]


def test_pagination_walks_all_pages(conn):
    pages = [[rec("2026-07-01", "Note", "5", "3", "2")] * 10000,
             [rec("2026-07-09", "Note", "6", "2", "2")]]
    s = FakeSession(pages=pages)
    auctions.fetch(ENTRY, conn, session=s)
    assert s.calls == 2  # first full page forced a second request


def test_idempotent(conn):
    auctions.fetch(ENTRY, conn, session=FakeSession())
    assert auctions.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(auctions.FetchError, match="HTTP 500"):
        auctions.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_no_notes_raises(conn):
    only_bills = FakeSession(pages=[[rec("2026-07-02", "Bill", "9", "1", "0")],
                                    []])
    with pytest.raises(auctions.FetchError, match="zero usable"):
        auctions.fetch(ENTRY, conn, session=only_bills)


def test_worldview_foreign_lines():
    lines = worldview.lines({}, "GREEN", {}, foreign={
        "custody_change_4w": 1.4, "indirect_share_pct": 68.0,
        "indirect_share_avg_pct": 62.0})
    text = "\n".join(lines)
    assert "adding to their US holdings (+1.4% over 4 weeks" in text
    assert "latest note auction was 68% (above its recent 62% average)" in text
