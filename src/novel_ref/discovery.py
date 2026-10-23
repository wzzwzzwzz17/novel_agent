from __future__ import annotations

import random
from typing import Any

from novel_ref.errors import NoCandidateSiteError
from novel_ref.site_adapter import SiteAdapter
from novel_ref.site_adapter.base import NovelMeta


def discover_novels(
    keyword: str,
    adapters: dict[str, SiteAdapter],
    runtime: dict[str, Any],
) -> NovelMeta:
    """按站点优先级轮询；单站失败自动换下一站；全空则 NoCandidateSiteError。"""
    priority = list(runtime.get("site_priority") or adapters.keys())
    max_c = int(runtime.get("max_candidates", 3))
    pool: list[NovelMeta] = []
    for site_id in priority:
        adapter = adapters.get(site_id)
        if not adapter:
            continue
        try:
            found = adapter.search_novel(keyword, max_c)
            pool.extend(found or [])
        except Exception:
            continue
        # 单站有结果即可进入候选池；继续合并其它站以增加多样性
    if not pool:
        raise NoCandidateSiteError("两个站点均无可用候选")
    return random.choice(pool)


def pick_chapter_url(meta: NovelMeta) -> str | None:
    if not meta.chapter_urls:
        return None
    return random.choice(meta.chapter_urls)
