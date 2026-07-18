"""F13 insider-cluster rule tests.

@context  Batch 3.5: the sliding-window distinct-buyer count, boundary cases.
@done     3 buyers inside 90 days flag; 2 don't; same buyer thrice doesn't;
          91-day spread doesn't; window slides (late cluster still found).
@todo     —
@limits   Pure.
@affects  src/insiders.py.
"""

from src.insiders import cluster_flags


def test_three_distinct_buyers_within_window_flags():
    events = [("ACME", "alice", "2026-01-10"), ("ACME", "bob", "2026-02-15"),
              ("ACME", "carol", "2026-04-01")]  # all within 90d of 01-10
    assert cluster_flags(events) == {"ACME": True}


def test_two_buyers_do_not_flag():
    events = [("ACME", "alice", "2026-01-10"), ("ACME", "bob", "2026-01-11")]
    assert cluster_flags(events) == {"ACME": False}


def test_same_buyer_three_times_does_not_flag():
    events = [("ACME", "alice", "2026-01-10"), ("ACME", "alice", "2026-02-01"),
              ("ACME", "alice", "2026-03-01")]
    assert cluster_flags(events) == {"ACME": False}


def test_spread_beyond_90_days_does_not_flag():
    events = [("ACME", "alice", "2026-01-01"), ("ACME", "bob", "2026-03-01"),
              ("ACME", "carol", "2026-04-02")]  # carol is day 91 after alice
    assert cluster_flags(events)["ACME"] is False


def test_window_slides_to_late_clusters():
    events = [("ACME", "zed", "2025-01-01"),  # lone early buy
              ("ACME", "alice", "2026-06-01"), ("ACME", "bob", "2026-06-15"),
              ("ACME", "carol", "2026-07-01")]
    assert cluster_flags(events)["ACME"] is True


def test_issuers_independent():
    events = [("ACME", "alice", "2026-01-10"), ("ACME", "bob", "2026-01-12"),
              ("ACME", "carol", "2026-01-14"), ("OTHER", "dave", "2026-01-10")]
    assert cluster_flags(events) == {"ACME": True, "OTHER": False}
