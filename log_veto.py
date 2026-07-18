"""Veto log — the honesty ritual (build plan Phase 6 task 3).

@context  Every time the operator WANTS to buy something the machine says no
          to, that override-urge is logged with the price at the moment. Later
          (quarterly) the price-then vs price-now tells whether obeying the
          machine was worth it. Discipline made into data.
@done     store(): writes an event_type='veto' journal row (market, reason,
          price at veto). CLI: log_veto.py <market> <price> "<reason>".
@todo     Quarterly veto review (build plan task 5).
@limits   Append-only like the rest of the journal.
@affects  journal table; src/alarms.veto_stats; the Journal screen (Phase 6.4).
"""

import argparse
import datetime as dt

from src import db


def store(market: str, price: float, reason: str, db_path=db.DB_PATH,
          today: dt.date | None = None) -> str:
    today = (today or dt.date.today()).isoformat()
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        conn.execute(
            "INSERT INTO journal (date, market_id, event_type, detail,"
            " price_at_event) VALUES (?, ?, 'veto', ?, ?)",
            (today, market, f"wanted to buy, machine said no: {reason}",
             float(price)))
        conn.commit()
    finally:
        conn.close()
    return f"veto logged: {market} @ {price} on {today} — {reason}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("market")
    p.add_argument("price", type=float)
    p.add_argument("reason")
    args = p.parse_args(argv)
    print(store(args.market, args.price, args.reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
