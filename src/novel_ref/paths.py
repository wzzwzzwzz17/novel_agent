from __future__ import annotations

import os
from pathlib import Path


def package_root() -> Path:
    # src/novel_ref/paths.py -> repo root (…/novel_agent)
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    env = os.environ.get("NOVELREF_CONFIG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return package_root() / "config" / "novel_ref"


def keywords_path() -> Path:
    return default_config_dir() / "keywords.yaml"


def runtime_path() -> Path:
    return default_config_dir() / "runtime.yaml"
