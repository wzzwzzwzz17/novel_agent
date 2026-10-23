from __future__ import annotations

from typing import Any

from novel_ref.site_adapter.base import NovelMeta, SiteAdapter
from novel_ref.site_adapter.site_a import SiteAAdapter
from novel_ref.site_adapter.site_b import SiteBAdapter


def build_adapters(runtime: dict[str, Any], fetcher) -> dict[str, SiteAdapter]:
    sites = runtime.get("sites") or {}
    return {
        "site_a": SiteAAdapter("site_a", sites.get("site_a") or {}, fetcher),
        "site_b": SiteBAdapter("site_b", sites.get("site_b") or {}, fetcher),
    }


__all__ = ["NovelMeta", "SiteAdapter", "SiteAAdapter", "SiteBAdapter", "build_adapters"]
