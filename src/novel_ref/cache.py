from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Hashable


class TTLCache:
    """In-memory LRU + TTL. Never persists to disk."""

    def __init__(self, maxsize: int = 128, default_ttl: float = 1800.0) -> None:
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if time.time() > expires:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        expires = time.time() + (self.default_ttl if ttl is None else ttl)
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (expires, value)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)
