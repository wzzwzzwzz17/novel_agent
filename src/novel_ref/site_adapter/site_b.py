from __future__ import annotations

from urllib.parse import quote

from novel_ref.site_adapter.base import NovelMeta, SiteAdapter


class SiteBAdapter(SiteAdapter):
    """m.jinshuge.org — keyword search adapter."""

    def search_novel(self, keyword: str, max_candidates: int) -> list[NovelMeta]:
        template = self.cfg.get("search_url") or ""
        if not template:
            return []
        url = template.format(keyword=quote(keyword))
        try:
            soup = self.fetcher.get_soup(url, self.robots_url)
        except Exception:
            return []
        sel = self.selectors.get("search_result") or "a"
        metas: list[NovelMeta] = []
        seen: set[str] = set()
        for a in soup.select(sel):
            href = a.get("href")
            if not href:
                continue
            novel_url = self.abs_url(href)
            if novel_url in seen:
                continue
            seen.add(novel_url)
            title = a.get_text(strip=True) or "unknown"
            try:
                detail = self.fetcher.get_soup(novel_url, self.robots_url)
            except Exception:
                continue
            book_title = self.parse_title(detail) or title
            chapters = self.parse_chapter_urls(detail)
            if not book_title:
                continue
            metas.append(
                NovelMeta(
                    url=novel_url,
                    title=book_title,
                    site_id=self.site_id,
                    chapter_urls=chapters,
                )
            )
            if len(metas) >= max_candidates:
                break
        return metas
