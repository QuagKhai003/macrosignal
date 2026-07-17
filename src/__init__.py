"""src — the machine: fetchers, formulas, state engine, report.

@context  Package root for all deterministic machine code (see CLAUDE.md
          "Where things live").
@done     Empty package (batch 0.1 scaffold).
@todo     0.2 db schema; 0.3 signals.yaml loader; Phase 1 fetchers + pct_rank.
@limits   Golden Rule: nothing in this package ever acts on an LLM-produced
          number; every value comes from the frozen formulas (F1-F14).
@affects  Imported by weekly_run.py and tests/.
"""
