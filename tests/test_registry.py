"""Admission-test validation tests.

@context  Batch 0.3 acceptance: the shipped signals.yaml holds exactly the 5
          spine signals and every entry passes the 5-question admission test;
          bad entries are rejected with named reasons.
@done     Real-file load, per-question rejection cases, duplicate ids.
@todo     —
@limits   Offline, deterministic — history check pinned to as_of 2026-07-18.
@affects  src/registry.py, signals.yaml.
"""

import datetime as dt

import pytest

from src import registry

AS_OF = dt.date(2026, 7, 18)


def valid_entry(**overrides):
    e = {
        "series_id": "test_signal", "name": "Test", "group": "A",
        "source": "FRED", "source_url": "https://example.com/data.csv",
        "schedule": "weekly", "pub_lag_days": 1,
        "history_start": dt.date(2010, 1, 1), "window": "rolling3y",
        "causal_sentence": "It moves prices because reasons.",
        "elite_watcher": "Famous Investor",
    }
    e.update(overrides)
    return e


def test_shipped_registry_has_7_admitted_signals():
    entries = registry.load_registry(as_of=AS_OF)
    assert len(entries) == 7
    assert {e["series_id"] for e in entries} == {
        "net_liquidity", "cot_managed_money", "spine_prices",
        "real_yields", "credit_spread", "oil_inventories",
        "market_valuation"}


def test_valid_entry_passes():
    assert registry.validate([valid_entry()], as_of=AS_OF) == []


@pytest.mark.parametrize("overrides, expected_fragment", [
    ({"causal_sentence": ""}, "missing causal_sentence"),      # question 4
    ({"elite_watcher": "  "}, "missing elite_watcher"),        # question 5
    ({"history_start": dt.date(2020, 1, 1)}, "years of history"),  # question 3
    ({"schedule": "whenever"}, "fixed cadence"),               # question 2
    ({"source_url": "ftp://x"}, "must be a URL"),              # question 2
    ({"group": "Z"}, "one letter A-H"),
    ({"window": "rolling99y"}, "window must be one of"),
])
def test_bad_entries_are_rejected(overrides, expected_fragment):
    problems = registry.validate([valid_entry(**overrides)], as_of=AS_OF)
    assert any(expected_fragment in p for p in problems), problems


def test_duplicate_ids_rejected():
    problems = registry.validate([valid_entry(), valid_entry()], as_of=AS_OF)
    assert any("duplicate series_id" in p for p in problems)


def test_load_raises_on_invalid(tmp_path):
    bad = tmp_path / "signals.yaml"
    bad.write_text("signals:\n  - series_id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="admission test"):
        registry.load_registry(bad, as_of=AS_OF)
