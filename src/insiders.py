"""Insider cluster rule — F13, one of the best-documented buy signals.

@context  ≥3 distinct non-derivative open-market Form-4 buyers at one issuer
          within any 90-day window → flag (product doc §11 group B). Scope is
          deliberately the ~30 stocks inside the chosen theme ETFs — and the
          themes are NOT chosen yet, so the universe (signals.yaml
          whale_berkshire.insider_tickers) ships EMPTY: the rule is complete
          and tested, the fetch loop is a no-op, nothing is faked.
@done     cluster_flags(): pure sliding-window count of distinct buyers per
          issuer from (issuer, buyer, date) buy events. current_flags():
          the db-reading wrapper (as-of on FILING date) over insider_buys —
          live since the semis batch (universe = 10 chip names,
          src/fetchers/form4.py feeds the table).
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
