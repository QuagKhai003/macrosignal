"""Insider cluster rule — F13, one of the best-documented buy signals.

@context  ≥3 distinct non-derivative open-market Form-4 buyers at one issuer
          within any 90-day window → flag (product doc §11 group B). Scope is
          deliberately the ~30 stocks inside the chosen theme ETFs — and the
          themes are NOT chosen yet, so the universe (signals.yaml
          whale_berkshire.insider_tickers) ships EMPTY: the rule is complete
          and tested, the fetch loop is a no-op, nothing is faked.
@done     cluster_flags(): pure sliding-window count of distinct buyers per
          issuer from (issuer, buyer, date) buy events.
@todo     When themes are chosen: per-ticker Form-4 fetch via EDGAR
          submissions (same session/UA plumbing as src/fetchers/edgar.py) to
          produce the buy events; flag lands in the report.
@limits   PURE. Buy events must already be filtered to non-derivative
          open-market purchases by the (future) fetch layer.
@affects  Nothing live yet (empty universe); tests/test_insiders.py.
"""

import datetime as dt

WINDOW_DAYS = 90
MIN_BUYERS = 3


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
