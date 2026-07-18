"""IMF IRFCL fetcher + gold dominant-flow leg tests (research R2).

@context  Research R2 (KILLED): world CB gold accumulation as gold's second
          engine leg (spec §3.3). The fetcher turns per-country monthly
          holdings into one world flow series; cb_flow_strong compares
          trailing-12m purchases to the prior five years' trailing sums.
          The replay killed the wiring; the mechanism stays pinned here.
@done     CSV parse (sector filter, aggregate-code drop, panel-entry immunity,
          maturity embargo, idempotency, failures); cb_flow_strong strong/
          weak/thin-history; engines() does NOT consume the leg.
@todo     —
@limits   Offline via fake session; live smoke gated on network.
@affects  src/fetchers/imf.py, src/drivers.py.
"""

import datetime as dt

import pytest

from src import db, drivers
from src.fetchers import imf

ENTRY = {
    "series_id": "cb_gold", "source": "IMF",
    "source_url": "https://example.com/data", "schedule": "monthly",
    "window": "ttm_vs_5y_avg", "pub_lag_days": 45,
    "history_start": dt.date(1999, 12, 31),
}

HEADER = "DATAFLOW,COUNTRY,INDICATOR,SECTOR,FREQUENCY,TIME_PERIOD,OBS_VALUE"


def csv_payload(rows):
    lines = [HEADER] + [
        f"IMF.STA:IRFCL(12.0.0),{c},IRFCLDT1_IRFCL56V_FTO,{s},M,{t},{v}"
        for c, s, t, v in rows]
    return "\n".join(lines)


# USA flat, TUR buys 32150.7466 oz (= exactly 1 tonne) in M02; CHN enters the
# panel in M02 (no prior month -> contributes NOTHING); G163 aggregate and the
# split-sector BEL rows are excluded.
ROWS = [
    ("USA", "S1XS1311", "2024-M01", "261499000"),
    ("USA", "S1XS1311", "2024-M02", "261499000"),
    ("TUR", "S1XS1311", "2024-M01", "19400000"),
    ("TUR", "S1XS1311", "2024-M02", "19432150.7466"),
    ("CHN", "S1XS1311", "2024-M02", "72960000"),
    ("G163", "S1XS1311", "2024-M01", "347000000"),
    ("G163", "S1XS1311", "2024-M02", "999000000"),
    ("BEL", "S1311", "2024-M01", "7310000"),
    ("BEL", "S1311", "2024-M02", "9310000"),
]

TODAY = dt.date(2026, 7, 18)


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


class FakeSession:
    def __init__(self, text=None, status=200):
        self._text = csv_payload(ROWS) if text is None else text
        self._status = status

    def get(self, url, params, timeout):
        return FakeResponse(self._text, self._status)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_world_flow_hand_check(conn):
    added = imf.fetch(ENTRY, conn, session=FakeSession(), today=TODAY)
    assert added == 1  # only M02 has an adjacent prior month
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = 'cb_gold_flow'").fetchall()
    # USA 0 + TUR exactly 1 tonne; CHN panel entry ignored; G163/BEL excluded.
    # Feb 29 2024 (leap) + 45d = Apr 14 2024.
    assert rows == [("2024-02-29", "2024-04-14", pytest.approx(1.0))]


def test_immature_months_embargoed(conn):
    # M02 flow matures Apr 14 (Feb 29 + 45d); M03 matures May 15 (Mar 31 + 45d)
    rows = ROWS + [("USA", "S1XS1311", "2024-M03", "261499000"),
                   ("TUR", "S1XS1311", "2024-M03", "19500000")]
    added = imf.fetch(ENTRY, conn, session=FakeSession(text=csv_payload(rows)),
                      today=dt.date(2024, 5, 14))  # one day before M03 matures
    assert added == 1  # M02 stored, M03 embargoed
    assert conn.execute("SELECT MAX(data_date) FROM observations WHERE"
                        " series_id = 'cb_gold_flow'").fetchone()[0] \
        == "2024-02-29"


def test_all_immature_raises(conn):
    with pytest.raises(imf.FetchError, match="zero usable"):
        imf.fetch(ENTRY, conn, session=FakeSession(),
                  today=dt.date(2024, 4, 13))


def test_idempotent(conn):
    imf.fetch(ENTRY, conn, session=FakeSession(), today=TODAY)
    assert imf.fetch(ENTRY, conn, session=FakeSession(), today=TODAY) == 0


def test_http_error_raises(conn):
    with pytest.raises(imf.FetchError, match="HTTP 500"):
        imf.fetch(ENTRY, conn, session=FakeSession(status=500))


def test_no_template_rows_raises(conn):
    with pytest.raises(imf.FetchError, match="unexpected payload"):
        imf.fetch(ENTRY, conn, session=FakeSession(text=HEADER + "\n"))


# ── the dominant-flow engine leg ─────────────────────────────────────────────

def put_monthly_flow(conn, values, start_year=2020):
    conn.execute("INSERT OR IGNORE INTO series VALUES ('cb_gold_flow','IMF',"
                 "'u','monthly','ttm_vs_5y_avg','')")
    rows = []
    for i, v in enumerate(values):
        year, month = divmod(start_year * 12 + i, 12)
        date = f"{year}-{month + 1:02d}-28"
        rows.append(("cb_gold_flow", date, date, float(v)))
    conn.executemany("INSERT INTO observations VALUES (?,?,?,?)", rows)


AS_OF = "2026-07-18"


def test_cb_flow_strong_when_ttm_above_trend(conn):
    # five years at 10 t/month (TTM 120), final year at 50 (TTM 600)
    put_monthly_flow(conn, [10.0] * 60 + [50.0] * 12, start_year=2020)
    assert drivers.cb_flow_strong(conn, AS_OF) is True


def test_cb_flow_weak_when_ttm_below_trend(conn):
    put_monthly_flow(conn, [10.0] * 60 + [5.0] * 12, start_year=2020)
    assert drivers.cb_flow_strong(conn, AS_OF) is False


def test_cb_flow_needs_four_prior_years(conn):
    put_monthly_flow(conn, [10.0] * 48 + [50.0] * 12, start_year=2021)
    assert drivers.cb_flow_strong(conn, AS_OF) is True  # 4 priors: enough
    conn.execute("DELETE FROM observations")
    put_monthly_flow(conn, [10.0] * 36 + [50.0] * 12, start_year=2022)
    assert drivers.cb_flow_strong(conn, AS_OF) is None  # 3 priors: cannot answer


def test_cb_flow_respects_as_of(conn):
    put_monthly_flow(conn, [10.0] * 60 + [50.0] * 12, start_year=2020)
    assert drivers.cb_flow_strong(conn, "2020-06-01") is None


def test_cb_leg_not_wired_into_engines(conn):
    # KILLED by research R2: even a roaring flow must NOT flip the gold
    # engine (the mechanism stays testable above, but disconnected)
    put_monthly_flow(conn, [10.0] * 60 + [50.0] * 12, start_year=2020)
    assert drivers.engines(conn, AS_OF)["gold"] == {"engine": None,
                                                    "alive": None}
