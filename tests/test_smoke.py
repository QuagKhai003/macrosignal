"""Smoke test for the batch 0.1 scaffold.

@context  Batch 0.1 acceptance: the runner executes cleanly and the machine
          package imports.
@done     Asserts weekly_run.main() returns 0 and prints "run complete".
@todo     —
@limits   Offline, deterministic.
@affects  weekly_run.py, src/.
"""

import weekly_run


def test_weekly_run_prints_run_complete(capsys):
    assert weekly_run.main() == 0
    assert capsys.readouterr().out.strip() == "run complete"


def test_src_package_imports():
    import src  # noqa: F401
