"""Signal registry — signals.yaml loader + admission-test validation.

@context  signals.yaml is the admission-test config (product doc §11): a signal
          exists for the machine only if it is registered here and passes the
          five admission questions. This module is the gatekeeper.
@done     load_registry() + validate(): required fields present, group A-H,
          schedule fixed, window from the tech-spec set, 10+ years of history
          as of the run date, causal sentence + elite watcher named.
          (Question 1 — "a number, not a vibe" — is enforced structurally:
          admitted signals live as numeric rows in observations.)
@todo     Phase 1: per-market keyword lists for news themes; markets sub-list
          for multi-market entries (COT, prices).
@limits   Deterministic: date-dependent history check takes as_of explicitly
          (tests pass a fixed date). No network.
@affects  weekly_run.py (series sync, 0.4); every fetcher (Phase 1) reads its
          config from here; windows here are the ONLY source of percentile
          windows (never hardcoded — build plan Phase 1 rule).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

REGISTRY_PATH = Path("signals.yaml")

REQUIRED_FIELDS = (
    "series_id", "name", "group", "source", "source_url", "schedule",
    "pub_lag_days", "history_start", "window", "causal_sentence", "elite_watcher",
)
ALLOWED_GROUPS = set("ABCDEFGH")
ALLOWED_SCHEDULES = {"daily", "weekly", "monthly", "quarterly"}
# The window vocabulary used by the tech spec (Part 1) — the db stores it as
# free text; THIS set is the semantic gate (decision 2026-07-18).
ALLOWED_WINDOWS = {
    "rolling3y", "rolling10y", "rolling20y", "sma200",
    "same_week_5y_avg", "same_quarter_5y_avg", "fixed_threshold",
    "ttm_vs_5y_avg",  # trailing-12m sum vs the prior 5 years' sums (research R2)
}
MIN_HISTORY_YEARS = 10  # admission question 3 (percentile-windowed signals)
# Question 3's stated rationale is "no history -> no percentile ->
# inadmissible" (product doc §11). fixed_threshold signals never compute a
# percentile; they need enough history for their own formula instead —
# F6's trailing-year ratio needs 1+ year (decision 2026-07-18).
MIN_HISTORY_YEARS_FIXED = 1


def load_registry(path: Path | str = REGISTRY_PATH,
                  as_of: dt.date | None = None) -> list[dict]:
    """Load signals.yaml and raise ValueError unless every entry passes."""
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["signals"]
    problems = validate(entries, as_of=as_of)
    if problems:
        raise ValueError(
            "signals.yaml failed the admission test:\n- " + "\n- ".join(problems))
    return entries


def validate(entries: list[dict], as_of: dt.date | None = None) -> list[str]:
    """Return a list of admission-test violations (empty = all pass)."""
    as_of = as_of or dt.date.today()
    problems: list[str] = []
    seen: set[str] = set()
    for e in entries:
        sid = str(e.get("series_id") or "<missing series_id>")
        if sid in seen:
            problems.append(f"{sid}: duplicate series_id")
        seen.add(sid)
        for field in REQUIRED_FIELDS:
            if not str(e.get(field) if e.get(field) is not None else "").strip():
                problems.append(f"{sid}: missing {field}")
        if e.get("group") not in ALLOWED_GROUPS:
            problems.append(f"{sid}: group must be one letter A-H")
        if e.get("schedule") not in ALLOWED_SCHEDULES:
            problems.append(f"{sid}: schedule must be a fixed cadence "
                            f"{sorted(ALLOWED_SCHEDULES)} (admission question 2)")
        if e.get("window") not in ALLOWED_WINDOWS:
            problems.append(f"{sid}: window must be one of {sorted(ALLOWED_WINDOWS)}")
        if not str(e.get("source_url", "")).startswith(("http://", "https://")):
            problems.append(f"{sid}: source_url must be a URL (admission question 2)")
        start = e.get("history_start")
        if isinstance(start, dt.date):
            need = (MIN_HISTORY_YEARS_FIXED
                    if e.get("window") == "fixed_threshold"
                    else MIN_HISTORY_YEARS)
            years = (as_of - start).days / 365.25
            if years < need:
                problems.append(
                    f"{sid}: only {years:.1f} years of history, need "
                    f"{need}+ (admission question 3)")
        elif start is not None:
            problems.append(f"{sid}: history_start must be a YYYY-MM-DD date")
    return problems
