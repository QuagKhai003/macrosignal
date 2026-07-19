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
    """Richer F13 read (Tier-2): per flagged issuer, whether the cluster is
    OPPORTUNISTIC (Cohen-Malloy-Pomorski: a buy is ROUTINE if the same buyer
    at the same issuer also bought in the same calendar month of a PRIOR year
    — routine buys carry little signal; a cluster is opportunistic if at least
    one buyer breaks their own pattern) and whether a CFO is among the buyers.
    Routine detection needs 2+ years of history, so until then every cluster
    reads opportunistic — honest and self-correcting as history accrues."""
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
    enough_history = len({dt.date.fromisoformat(d).year
                          for _t, _b, d in history}) >= 2

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


def _is_routine(issuer, buyer, date, prior) -> bool:
    """The buyer bought this issuer in the same calendar month of a prior year."""
    d = dt.date.fromisoformat(date)
    return any((issuer, buyer, y, d.month) in prior for y in range(2006, d.year))


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
