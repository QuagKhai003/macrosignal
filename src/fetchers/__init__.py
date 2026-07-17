"""src.fetchers — one module per data source, all behind one seam.

@context  The seam pattern (WORKFLOW §10): every fetcher module exposes
          `fetch(entry, conn, session=None) -> int` (new observation rows
          added), where `entry` is the signals.yaml registry entry. Sources
          swap without caller changes; `session` injection keeps tests offline.
@done     Package init (batch 1.2); shared db helpers live in base.py.
@todo     1.3 cot.py; 1.4 prices.py; Phase 2 eia.py.
@limits   Fetchers RAISE on failure — the weekly_run orchestrator (1.5) logs
          the journal flag and continues; fetchers never swallow errors.
@affects  weekly_run.py; src/db.py.
"""
