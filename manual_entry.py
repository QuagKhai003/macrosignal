"""Manual observation entry — the human keyboard as a fetcher.

@context  Some admitted signals are PDFs (BofA manager cash): automation is
          not a v1 goal (build plan Phase 3), so the operator types one number
          a month. This CLI writes it as a normal as-of observation so every
          downstream formula treats it like fetched data.
@done     store(): validates the series is registered with source MANUAL,
          ensures the series row, INSERT OR IGNORE with data_date + pub_date
          (default: today for both). CLI: manual_entry.py <series_id> <value>
          [--date YYYY-MM-DD] [--pub-date YYYY-MM-DD].
@todo     —
@limits   MANUAL-source series only — fetched series stay fetcher-owned.
          Append-only like everything else; a wrong entry is corrected by the
          next entry, never by editing history.
@affects  signals.db observations (manager_cash); src/weather.py gauge 1.
"""

import argparse
import datetime as dt

from src import db, registry
from src.fetchers import base


def store(series_id: str, value: float, data_date: str | None = None,
          pub_date: str | None = None, db_path=db.DB_PATH,
          registry_path=registry.REGISTRY_PATH) -> str:
    entries = {e["series_id"]: e for e in registry.load_registry(registry_path)}
    entry = entries.get(series_id)
    if entry is None or entry["source"] != "MANUAL":
        manual = sorted(sid for sid, e in entries.items()
                        if e["source"] == "MANUAL")
        raise SystemExit(f"'{series_id}' is not a MANUAL series."
                         f" Manual series: {manual}")
    data_date = data_date or dt.date.today().isoformat()
    pub_date = pub_date or data_date
    dt.date.fromisoformat(data_date), dt.date.fromisoformat(pub_date)

    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        base.ensure_series_row(conn, series_id, entry, "manual entry")
        added = base.insert_observations(
            conn, series_id, [(data_date, pub_date, float(value))])
        conn.commit()
    finally:
        conn.close()
    if added:
        return f"stored {series_id} = {value} (data {data_date}, pub {pub_date})"
    return (f"already recorded: {series_id} on {data_date} — append-only, so"
            f" enter a new date to correct")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("series_id")
    p.add_argument("value", type=float)
    p.add_argument("--date", dest="data_date")
    p.add_argument("--pub-date", dest="pub_date")
    args = p.parse_args(argv)
    print(store(args.series_id, args.value, args.data_date, args.pub_date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
