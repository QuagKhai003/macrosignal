"""ECB fetcher + euro market-vs-market driver tests (research R4).

@context  Euro engine driver v2: US 2y minus euro-area AAA 2y, both MARKET
          rates (the policy-rate version failed C1). Fetcher parses the ECB
          csvdata payload; spine derives us_ez2y_diff; engines() reads the
          new driver for eur.
@done     CSV parse + publication lag, skipped unparseable rows, idempotency,
          failures; differential hand-check + as-of; engines() eur wiring on
          the new series.
@todo     —
@limits   Offline via fake session.
@affects  src/fetchers/ecb.py, src/spine.py, src/drivers.py.
"""

import datetime as dt

import pytest

from src import db, drivers, spine

from src.fetchers import ecb

ENTRY = {
    "series_id": "ez_2y_yield", "source": "ECB",
    "source_url": "https://example.com/yc", "schedule": "daily",
    "window": "rolling10y", "pub_lag_days": 1,
    "history_start": dt.date(2004, 9, 6),
}

CSV = "\n".join([
    "KEY,TIME_PERIOD,OBS_VALUE,TITLE",
    "YC...SR_2Y,2026-07-01,2.4738910102,AAA 2y",
    "YC...SR_2Y,2026-07-02,2.4543322029,AAA 2y",
    "YC...SR_2Y,2026-07-03,,AAA 2y",          # empty value: skipped
])


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


class FakeSession:
    def __init__(self, text=CSV, status=200):
        self._text, self._status = text, status

    def get(self, url, params, timeout):
        return FakeResponse(self._text, self._status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_parse_and_lag(conn):
    added = ecb.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'ecb_yc2y' ORDER BY data_date").fetchall()
    assert rows == [("2026-07-01", "2026-07-02", pytest.approx(2.4738910102)),
                    ("2026-07-02", "2026-07-03", pytest.approx(2.4543322029))]


def test_idempotent(conn):
    ecb.fetch(ENTRY, conn, session=FakeSession())
    assert ecb.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_http_error_raises(conn):
    with pytest.raises(ecb.FetchError, match="HTTP 500"):
        ecb.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_no_usable_rows_raises(conn):
    with pytest.raises(ecb.FetchError, match="zero usable"):
        ecb.fetch(ENTRY, conn, session=FakeSession(text="KEY,TIME_PERIOD\n"))


# ── the derived market-vs-market differential ────────────────────────────────

def seed(conn, sid, rows):
    conn.execute("INSERT OR IGNORE INTO series VALUES (?,'s','u','daily',"
                 "'rolling10y','')", (sid,))
    conn.executemany(
        "INSERT INTO observations VALUES (?,?,?,?)",
        [(sid, d, p, float(v)) for d, p, v in rows])


def test_differential_hand_check(conn):
    seed(conn, "fred_dgs2", [("2026-07-01", "2026-07-02", 3.9),
                             ("2026-07-06", "2026-07-07", 4.0)])
    # euro leg missing 07-06 (holiday) -> latest at-or-before carries
    seed(conn, "ecb_yc2y", [("2026-07-01", "2026-07-02", 2.5),
                            ("2026-07-03", "2026-07-04", 2.6)])
    assert spine.derive_market_rate_differential(conn, "2026-07-18") == 2
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'us_ez2y_diff' ORDER BY data_date").fetchall()
    assert rows == [("2026-07-01", "2026-07-02", pytest.approx(1.4)),
                    ("2026-07-06", "2026-07-07", pytest.approx(1.4))]


def test_differential_respects_as_of(conn):
    seed(conn, "fred_dgs2", [("2026-07-01", "2026-07-02", 3.9)])
    seed(conn, "ecb_yc2y", [("2026-07-01", "2026-07-09", 2.5)])  # late pub
    assert spine.derive_market_rate_differential(conn, "2026-07-05") == 0


def test_eur_not_wired_into_engines(conn):
    # PARKED by research R4: even a perfect falling market-rate gap must NOT
    # flip the euro engine (both rate-gap drivers failed C1; honest None).
    start = dt.date(2015, 1, 7)
    n = 560
    diff, price, d = [], [], 5.0
    for i in range(n):
        d += -0.02 if i % 2 == 0 else 0.01
        diff.append(round(d, 4))
        price.append(round(2.0 - 0.1 * d, 4))
    day = [(start + dt.timedelta(weeks=i)).isoformat() for i in range(n)]
    seed(conn, "us_ez2y_diff", list(zip(day, day, diff)))
    seed(conn, "price_eur", list(zip(day, day, price)))
    assert drivers.engines(conn, "2026-07-18")["eur"] == {"engine": None,
                                                          "alive": None}
    # the mechanism itself still answers on the series (kept testable)
    leg = drivers.falling_driver_engine(conn, "us_ez2y_diff", "price_eur",
                                        "2026-07-18")
    assert leg == {"engine": True, "alive": True}
