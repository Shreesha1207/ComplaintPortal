# -*- coding: utf-8 -*-
"""
Package init. Its one job is to load `.env` before anything reads the
environment.

WHY THIS LIVES HERE AND NOT IN main.py
--------------------------------------
Configuration is read at import time -- `app.ai.groq_engine` resolves
GROQ_API_KEY, the model and the timeouts as module-level constants, and
`app.main` builds the engine on the very first import. Anything that loads the
file later has already missed its chance. This module runs before any submodule
can, whichever entry point is used: `uvicorn app.main:app`, `run.sh`, the test
suite, or a one-off `python3 -c "from app import auth"`.

NO NEW DEPENDENCY
-----------------
python-dotenv would be a fourth package to install for thirty lines of parsing.
This project has to run on a ministry laptop behind a proxy; the smaller the
install, the more places it works.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Project root -- the directory holding this package, not the package itself.
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("APP_ENV_FILE", ROOT / ".env"))


def load_env(path: Path = ENV_FILE) -> list[str]:
    """Read KEY=VALUE lines from `.env` into the environment.

    A real environment variable always wins: `setdefault` means a value already
    exported by the shell, a container or a CI runner is never silently
    replaced by a stale line in a file someone forgot about.

    Deliberately forgiving. A malformed line is skipped rather than raised on,
    because a typo in a config file should not stop the server from starting --
    the feature that line configures simply stays off, which is a state this
    application already handles everywhere.
    """
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return loaded                       # no file, or unreadable: fine

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        # Strip one matching pair of quotes, so KEY="value with spaces" works.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)
        loaded.append(key)
    return loaded


LOADED_ENV_KEYS = load_env()
