"""Driver plugin tests — hand-built db scenarios.

@context  Batch 2.1: gold's falling+alive logic and oil's same-week 5-yr
          seasonal comparison, each pinned by hand-computed cases.
@done     Gold falling/rising/dead-corr/insufficient; oil below/above the
          seasonal average, sparse-history None; engines() shape (3 markets
          honestly None).
@todo     —
@limits   Offline; synthetic series built with datetime arithmetic.
@affects  src/drivers.py.
"""

import datetime as dt

import pytest

from src import db, drivers

START = dt.date(2015, 1, 7)  # a Wednesday


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for sid in ("fred_dfii10", "price_gold", "oil_inventories"):
        conn.execute("INSERT INTO series VALUES (?, 's', 'u', 'weekly',"
                     " 'rolling10y', '')", (sid,))
    yield conn
    conn.close()


def put_weekly(conn, sid, values, start=START):
    conn.executemany(
        "INSERT INTO observations VALUES (?, ?, ?, ?)",
        [(sid, (start + dt.timedelta(weeks=i)).isoformat(),
          (start + dt.timedelta(weeks=i)).isoformat(), float(v))
         for i, v in enumerate(values)])


def wiggle(base, n, big, small):
    """A wiggly but trending series: steps alternate big, small (non-flat
    changes so correlations are defined). Net drift = (big + small) / 2."""
    out, v = [], base
    for i in range(n):
        v += big if i % 2 == 0 else small
        out.append(v)
    return out


AS_OF = "2026-07-18"


def test_gold_engine_on_when_yields_fall_and_corr_negative(conn):
    n = 560
    yields_falling = wiggle(5.0, n, big=-1.0, small=0.5)  # net down
    # gold moves OPPOSITE yields each week -> strong negative corr (alive)
    gold = [8000.0 - 100 * y for y in yields_falling]
    put_weekly(conn, "fred_dfii10", yields_falling)
    put_weekly(conn, "price_gold", gold)
    result = drivers.gold_engine(conn, AS_OF)
    assert result == {"engine": True, "alive": True}


def test_gold_engine_off_when_yields_rise(conn):
    n = 560
    yields_rising = wiggle(0.0, n, big=1.0, small=-0.5)  # net up
    gold = [8000.0 - 100 * y for y in yields_rising]
    put_weekly(conn, "fred_dfii10", yields_rising)
    put_weekly(conn, "price_gold", gold)
    result = drivers.gold_engine(conn, AS_OF)
    assert result == {"engine": False, "alive": True}


def test_gold_engine_dead_when_corr_flips_sign(conn):
    n = 560
    yields_falling = wiggle(5.0, n, big=-1.0, small=0.5)
    # anti-correlated for years (negative historical sign), then the last 52
    # weeks move WITH yields -> rho_now flips positive -> driver dead.
    gold = [8000.0 - 100 * y for y in yields_falling[:n - 52]]
    g = gold[-1]
    for i in range(n - 52, n):
        g += (yields_falling[i] - yields_falling[i - 1]) * 100  # WITH yields
        gold.append(g)
    put_weekly(conn, "fred_dfii10", yields_falling)
    put_weekly(conn, "price_gold", gold)
    result = drivers.gold_engine(conn, AS_OF)
    assert result["alive"] is False
    assert result["engine"] is False


def test_gold_engine_insufficient(conn):
    put_weekly(conn, "fred_dfii10", [1.0, 2.0, 3.0])
    put_weekly(conn, "price_gold", [4000.0, 4001.0, 4002.0])
    assert drivers.gold_engine(conn, AS_OF)["engine"] is None


def test_oil_engine_below_seasonal_average(conn):
    # 6 years of weekly stocks at 450,000; final week drops to 400,000
    # -> prior-year same-week values 450,000 -> below average -> engine on
    n = 6 * 52
    values = [450000.0] * (n - 1) + [400000.0]
    put_weekly(conn, "oil_inventories", values)
    assert drivers.oil_engine(conn, AS_OF) == {"engine": True, "alive": True}


def test_oil_engine_above_seasonal_average(conn):
    n = 6 * 52
    values = [450000.0] * (n - 1) + [500000.0]
    put_weekly(conn, "oil_inventories", values)
    assert drivers.oil_engine(conn, AS_OF) == {"engine": False, "alive": True}


def test_oil_engine_needs_history(conn):
    put_weekly(conn, "oil_inventories", [450000.0] * 60)  # barely > 1 year
    assert drivers.oil_engine(conn, AS_OF)["engine"] is None


def test_oil_engine_band_carries_previous(conn):
    # last = 454,500 = 101% of the 450,000 average: inside the (100%, 102%]
    # band -> previous answer holds; fresh -> off
    n = 6 * 52
    values = [450000.0] * (n - 1) + [454500.0]
    put_weekly(conn, "oil_inventories", values)
    assert drivers.oil_engine(conn, AS_OF, prev_engine=True)["engine"] is True
    assert drivers.oil_engine(conn, AS_OF, prev_engine=False)["engine"] is False
    assert drivers.oil_engine(conn, AS_OF, prev_engine=None)["engine"] is False


def test_oil_engine_turns_off_above_band(conn):
    n = 6 * 52
    values = [450000.0] * (n - 1) + [459500.0]  # 102.1% of average
    put_weekly(conn, "oil_inventories", values)
    assert drivers.oil_engine(conn, AS_OF, prev_engine=True)["engine"] is False


def test_engines_shape(conn):
    result = drivers.engines(conn, AS_OF)
    assert set(result) == {"gold", "wti", "ust10y", "eur", "corn",
                           "silver", "copper", "natgas", "semis"}
    assert result["eur"] == {"engine": None, "alive": None}
    assert result["corn"] == {"engine": None, "alive": None}
    assert result["copper"] == {"engine": None, "alive": None}  # driverless


def test_natgas_engine_seasonal(conn):
    conn.execute("INSERT INTO series VALUES ('natgas_storage', 's', 'u',"
                 " 'weekly', 'same_week_5y_avg', '')")
    n = 6 * 52
    put_weekly(conn, "natgas_storage", [3000.0] * (n - 1) + [2500.0])
    assert drivers.natgas_engine(conn, AS_OF) == {"engine": True,
                                                  "alive": True}
    conn.execute("DELETE FROM observations WHERE series_id ="
                 " 'natgas_storage'")
    put_weekly(conn, "natgas_storage", [3000.0] * (n - 1) + [3500.0])
    assert drivers.natgas_engine(conn, AS_OF) == {"engine": False,
                                                  "alive": True}


def test_silver_shares_gold_driver(conn):
    conn.execute("INSERT INTO series VALUES ('price_silver', 's', 'u',"
                 " 'weekly', 'sma200', '')")
    n = 560
    yields_falling = wiggle(5.0, n, big=-1.0, small=0.5)
    silver = [90.0 - y for y in yields_falling]  # moves opposite yields
    put_weekly(conn, "fred_dfii10", yields_falling)
    put_weekly(conn, "price_silver", silver)
    assert drivers.engines(conn, AS_OF)["silver"] == {"engine": True,
                                                      "alive": True}


# ── R5: the dollar OR-leg for gold and silver ────────────────────────────────

def test_dollar_leg_alone_turns_gold_on(conn):
    # no real-yields data at all; the dollar falls with anti-corr gold price
    conn.execute("INSERT INTO series VALUES ('fred_dtwexbgs', 's', 'u',"
                 " 'daily', 'rolling10y', '')")
    n = 560
    dollar_falling = wiggle(130.0, n, big=-1.0, small=0.5)
    gold = [8000.0 - 10 * d for d in dollar_falling]
    put_weekly(conn, "fred_dtwexbgs", dollar_falling)
    put_weekly(conn, "price_gold", gold)
    result = drivers.engines(conn, AS_OF)["gold"]
    assert result == {"engine": True, "alive": True}


def test_dollar_rising_leaves_yields_leg(conn):
    conn.execute("INSERT INTO series VALUES ('fred_dtwexbgs', 's', 'u',"
                 " 'daily', 'rolling10y', '')")
    n = 560
    dollar_rising = wiggle(90.0, n, big=1.0, small=-0.5)
    gold = [8000.0 - 10 * d for d in dollar_rising]
    yields_rising = wiggle(0.0, n, big=1.0, small=-0.5)
    put_weekly(conn, "fred_dtwexbgs", dollar_rising)
    put_weekly(conn, "fred_dfii10", yields_rising)
    put_weekly(conn, "price_gold", gold)
    # both legs answer no -> engine False (not None)
    assert drivers.engines(conn, AS_OF)["gold"]["engine"] is False


def test_or_legs_three_way():
    t = {"engine": True, "alive": True}
    f = {"engine": False, "alive": False}
    n = {"engine": None, "alive": None}
    assert drivers._or_legs(f, t)["engine"] is True
    assert drivers._or_legs(n, t)["engine"] is True
    assert drivers._or_legs(f, n)["engine"] is None
    assert drivers._or_legs(f, f)["engine"] is False
