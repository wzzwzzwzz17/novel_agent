from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class KeywordRegistry:
    def __init__(self, path: Path) -> None:
        data: dict[str, Any] = {}
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.keywords: set[str] = set(data.get("keywords") or [])
        self.aliases: dict[str, str] = dict(data.get("aliases") or {})

    def list_tags(self) -> list[str]:
        return sorted(self.keywords)

    def normalize(self, keyword: str) -> str | None:
        k = (keyword or "").strip()
        if not k:
            return None
        if k in self.keywords:
            return k
        mapped = self.aliases.get(k)
        if mapped and mapped in self.keywords:
            return mapped
        return None
