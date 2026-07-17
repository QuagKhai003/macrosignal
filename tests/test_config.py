"""Config reader tests.

@context  Batch 1.2: keys resolve from env first, then .env, without leaking.
@done     env precedence, .env parsing (comments, blanks, empty values), miss.
@todo     —
@limits   Offline; never asserts real key VALUES, only resolution behavior.
@affects  src/config.py.
"""

from src import config


def test_environ_wins(monkeypatch, tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text("MY_KEY=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MY_KEY", "from_env")
    assert config.get_key("MY_KEY", env_path=envfile) == "from_env"


def test_reads_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_KEY", raising=False)
    envfile = tmp_path / ".env"
    envfile.write_text(
        "# comment\n\nOTHER=x\nMY_KEY=abc123\nEMPTY=\n", encoding="utf-8")
    assert config.get_key("MY_KEY", env_path=envfile) == "abc123"
    assert config.get_key("EMPTY", env_path=envfile) is None
    assert config.get_key("ABSENT", env_path=envfile) is None
