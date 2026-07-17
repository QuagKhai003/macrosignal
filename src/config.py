"""Config — API keys from the environment or the gitignored .env file.

@context  Secrets live in `.env` (CLAUDE.md); this is the one reader. Stdlib
          only — a 15-line parser beats a dependency.
@done     get_key(): os.environ first, then KEY=value lines in .env
          (comments/blanks ignored, no quotes stripping — keys are plain).
@todo     —
@limits   Never log or print returned values. No network.
@affects  src/fetchers/* (FRED now, EIA at Phase 2).
"""

import os
from pathlib import Path

ENV_PATH = Path(".env")


def get_key(name: str, env_path: Path | None = None) -> str | None:
    if env_path is None:
        env_path = ENV_PATH  # resolved at call time so tests can repoint it
    if name in os.environ:
        return os.environ[name]
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if key.strip() == name and value.strip():
                    return value.strip()
    return None
