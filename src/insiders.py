"""Insider cluster rule — F13, one of the best-documented buy signals.

@context  ≥3 distinct non-derivative open-market Form-4 buyers at one issuer
          within any 90-day window → flag (product doc §11 group B). Scope is
          deliberately the ~30 stocks inside the chosen theme ETFs — and the
          themes are NOT chosen yet, so the universe (signals.yaml
          whale_berkshire.insider_tickers) ships EMPTY: the rule is complete
          and tested, the fetch loop is a no-op, nothing is faked.
@done     cluster_flags(): pure sliding-window count of distinct buyers per
          issuer from (issuer, buyer, date) buy events. current_flags():
          the db-reading wrapper (as-of on FILING date) over insider_buys.
          cluster_detail() (Tier-2): adds the opportunistic-vs-routine split
          (Cohen-Malloy-Pomorski) and a CFO-present flag from the role column.
@todo     Equity engine (F9) + semis state entry — the next semis batch.
@limits   cluster_flags stays PURE; the fetch layer guarantees events are
          non-derivative open-market purchases.
@affects  weekly report insider line; tests/test_insiders.py, test_form4.py.
"""

import datetime as dt

WINDOW_DAYS = 90
MIN_BUYERS = 3


def current_flags(conn, as_of: str) -> dict[str, bool]:
    """F13 flags from the insider_buys table, as-of honest: only filings
    published by as_of count, over transactions inside the trailing cluster
    window (+ the window's own reach-back)."""
    floor = (dt.date.fromisoformat(as_of)
             - dt.timedelta(days=2 * WINDOW_DAYS)).isoformat()
    events = conn.execute(
        "SELECT ticker, buyer, trans_date FROM insider_buys"
        " WHERE filing_date <= ? AND trans_date >= ?",
        (as_of, floor)).fetchall()
    return cluster_flags([tuple(e) for e in events])


def cluster_detail(conn, as_of: str) -> dict[str, dict]:
    """Richer F13 read (Tier-2, sharpened by research Round 2): per flagged
    issuer, whether the cluster is OPPORTUNISTIC and whether a CFO is among
    the buyers.

    ROUTINE (Cohen-Malloy-Pomorski exact rule, verified R2): a buyer is
    routine at an issuer if they bought there in the same calendar month in
    at least ROUTINE_YEARS CONSECUTIVE prior years — those trades carry ~zero
    signal. A cluster is opportunistic if at least one buyer is NOT routine.
    Routine detection needs ROUTINE_YEARS+1 years of history, so until then
    every cluster reads opportunistic — honest and self-correcting."""
    from src.fetchers.form4 import is_cfo
    floor = (dt.date.fromisoformat(as_of)
             - dt.timedelta(days=2 * WINDOW_DAYS)).isoformat()
    window = conn.execute(
        "SELECT ticker, buyer, trans_date, role FROM insider_buys"
        " WHERE filing_date <= ? AND trans_date >= ?",
        (as_of, floor)).fetchall()
    history = conn.execute(
        "SELECT ticker, buyer, trans_date FROM insider_buys"
        " WHERE filing_date <= ?", (as_of,)).fetchall()
    prior = {(t, b, dt.date.fromisoformat(d).year,
              dt.date.fromisoformat(d).month) for t, b, d in history}
    span = {dt.date.fromisoformat(d).year for _t, _b, d in history}
    enough_history = len(span) > ROUTINE_YEARS

    flags = cluster_flags([(t, b, d) for t, b, d, _r in window])
    out = {}
    for issuer, flagged in flags.items():
        rows = [(b, d, r) for t, b, d, r in window if t == issuer]
        cfo = flagged and any(is_cfo(r) for _b, _d, r in rows)
        if not flagged:
            opportunistic = False
        elif not enough_history:
            opportunistic = True  # cannot judge routine yet — honest default
        else:
            opportunistic = any(
                not _is_routine(issuer, b, d, prior) for b, d, _r in rows)
        out[issuer] = {"flagged": flagged, "opportunistic": opportunistic,
                       "cfo": cfo}
    return out


ROUTINE_YEARS = 3  # CMP: same calendar month in 3+ CONSECUTIVE prior years

# Research R2 (FAJ 2004, verified): insider sells are ASYMMETRIC — small sells
# are non-bearish (mildly bullish); a sale predicts negative returns ONLY when
# it is BOTH large in absolute size AND a large fraction of the stake.
BEARISH_SELL_SHARES = 100_000
BEARISH_SELL_FRACTION = 0.50


TURNOVER_SPIKE = 2.5  # latest week's volume vs its trailing median (proxy for
                      # the ~325%-of-normal wolf-pack trigger, R2)


def turnover_spikes(conn, as_of: str) -> list[str]:
    """Equity-universe tickers whose latest weekly share volume is a large
    multiple of its own trailing median — the pre-13D accumulation the R2
    wolf-pack literature measures in TURNOVER. Context only. Needs >=9 weeks
    of volume; None-safe."""
    out = []
    for (sid,) in conn.execute(
            "SELECT DISTINCT series_id FROM observations WHERE series_id"
            " LIKE 'vol_%'"):
        vols = [r[0] for r in conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND"
            " pub_date <= ? ORDER BY data_date", (sid, as_of))]
        if len(vols) < 9:
            continue
        trailing = sorted(vols[-9:-1])
        median = trailing[len(trailing) // 2]
        if median > 0 and vols[-1] > TURNOVER_SPIKE * median:
            out.append(sid.split("_", 1)[1].upper())
    return sorted(out)


def bearish_sells(conn, as_of: str, days: int = 2 * WINDOW_DAYS) -> list[str]:
    """Tickers with a GATED bearish insider sale in the trailing window: a
    sale that is BOTH >BEARISH_SELL_SHARES shares AND >BEARISH_SELL_FRACTION
    of the seller's stake. As-of honest on the filing date. Small sales are
    deliberately ignored — they are not a bearish signal (R2)."""
    floor = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM insider_sells WHERE filing_date <= ?"
        " AND trans_date >= ? AND shares > ? AND fraction > ? ORDER BY ticker",
        (as_of, floor, BEARISH_SELL_SHARES, BEARISH_SELL_FRACTION)).fetchall()
    return [r[0] for r in rows]


def _is_routine(issuer, buyer, date, prior) -> bool:
    """The buyer bought this issuer in the same calendar month in at least
    ROUTINE_YEARS CONSECUTIVE years immediately before this trade's year
    (the exact Cohen-Malloy-Pomorski definition, R2-verified)."""
    d = dt.date.fromisoformat(date)
    return all((issuer, buyer, d.year - k, d.month) in prior
               for k in range(1, ROUTINE_YEARS + 1))


def cluster_flags(buy_events: list[tuple[str, str, str]]) -> dict[str, bool]:
    """buy_events: (issuer, buyer, iso_date). Returns {issuer: flagged}."""
    by_issuer: dict[str, list[tuple[dt.date, str]]] = {}
    for issuer, buyer, date in buy_events:
        by_issuer.setdefault(issuer, []).append(
            (dt.date.fromisoformat(date), buyer))
    out = {}
    for issuer, events in by_issuer.items():
        events.sort()
        flagged = False
        for i, (start_date, _buyer) in enumerate(events):
            window_end = start_date + dt.timedelta(days=WINDOW_DAYS)
            buyers = {b for d, b in events[i:] if d <= window_end}
            if len(buyers) >= MIN_BUYERS:
                flagged = True
                break
        out[issuer] = flagged
    return out
