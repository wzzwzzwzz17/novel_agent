from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


@dataclass
class NovelMeta:
    url: str
    title: str
    site_id: str
    chapter_urls: list[str] = field(default_factory=list)


class SiteAdapter(ABC):
    def __init__(self, site_id: str, cfg: dict[str, Any], fetcher) -> None:
        self.site_id = site_id
        self.cfg = cfg
        self.fetcher = fetcher
        self.selectors = cfg.get("selectors") or {}

    @property
    def robots_url(self) -> str | None:
        return self.cfg.get("robots_url")

    def abs_url(self, href: str) -> str:
        base = self.cfg.get("base") or ""
        return urljoin(base, href)

    @abstractmethod
    def search_novel(self, keyword: str, max_candidates: int) -> list[NovelMeta]:
        raise NotImplementedError

    def parse_title(self, soup: BeautifulSoup) -> str:
        sel = self.selectors.get("book_title") or "h1"
        for part in sel.split(","):
            part = part.strip()
            if not part:
                continue
            node = soup.select_one(part)
            if not node:
                continue
            if node.name == "meta":
                content = (node.get("content") or "").strip()
                if content:
                    return content
            text = node.get_text(strip=True)
            if text:
                # <title> 常带站点后缀，取第一段
                if node.name == "title" and "|" in text:
                    text = text.split("|", 1)[0].strip()
                return text
        return ""

    def parse_chapter_urls(self, soup: BeautifulSoup) -> list[str]:
        sel = self.selectors.get("chapter_link") or "a"
        urls: list[str] = []
        for a in soup.select(sel):
            href = a.get("href")
            if href:
                urls.append(self.abs_url(href))
        # de-dup preserve order
        return list(dict.fromkeys(urls))
