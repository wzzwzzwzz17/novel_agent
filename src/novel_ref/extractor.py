from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

NAV_PATTERN = re.compile(r"^第[一二三四五六七八九十百千\d]+章")


def extract_paragraphs(
    soup: BeautifulSoup,
    site_id: str,
    runtime: dict[str, Any],
) -> list[str]:
    sites = runtime.get("sites") or {}
    site_cfg = sites.get(site_id) or {}
    selectors = site_cfg.get("selectors") or {}
    content_sel = selectors.get("content") or ".content"
    content = None
    for part in content_sel.split(","):
        content = soup.select_one(part.strip())
        if content:
            break
    if not content:
        return []
    paras = [p.get_text(strip=True) for p in content.find_all("p")]
    if not paras:
        # some sites dump text without <p>
        raw = content.get_text("\n", strip=True)
        paras = [x.strip() for x in raw.split("\n") if x.strip()]
    return [p for p in paras if p]


def clean_paragraphs(
    paras: list[str],
    scene_keywords: list[str],
    runtime: dict[str, Any],
    site_id: str = "",
) -> list[str]:
    out = _usable_paragraphs(paras, runtime, site_id)
    if not scene_keywords:
        return out
    return [p for p in out if any(kw in p for kw in scene_keywords)]


def wide_chunks(
    paras: list[str],
    runtime: dict[str, Any],
    site_id: str = "",
    *,
    target_chars: int = 2000,
    limit: int = 10,
) -> list[str]:
    """把一章正文顺序切成约 target_chars 字的大段，不做关键词过滤。"""
    clean = _usable_paragraphs(paras, runtime, site_id)
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in clean:
        buf.append(para)
        size += len(para)
        if size >= target_chars:
            chunks.append(_trim_wide("\n".join(buf), target_chars))
            buf = []
            size = 0
            if len(chunks) >= limit:
                break
    if buf and len(chunks) < limit and size >= 400:
        chunks.append("\n".join(buf).strip())
    return chunks


def best_keyword_span(
    text: str,
    focus_keywords: list[str],
    prefer_keywords: list[str] | None = None,
    *,
    target_chars: int = 500,
) -> str:
    """在一段长文里截取最贴关键词的连续约 target_chars 字。没有焦点词则返回空。"""
    raw = (text or "").strip()
    focus = [k for k in focus_keywords if k and k in raw]
    if not raw or not focus:
        return ""
    prefer = [k for k in (prefer_keywords or []) if k]
    if len(raw) <= target_chars + 40:
        return raw
    best_at = 0
    best_score = -1
    step = 30
    last = len(raw) - target_chars
    for i in range(0, max(last, 0) + 1, step):
        window = raw[i : i + target_chars]
        score = sum(window.count(k) * 8 for k in focus)
        score += sum(window.count(k) for k in prefer)
        if score > best_score:
            best_score = score
            best_at = i
    if best_score <= 0:
        return ""
    end = min(len(raw), best_at + target_chars)
    tail = raw[end : end + 40]
    for mark in ("。", "！", "？"):
        pos = tail.find(mark)
        if pos >= 0:
            end = end + pos + 1
            break
    return raw[best_at:end].strip()


def _trim_wide(text: str, target_chars: int) -> str:
    if len(text) <= target_chars + 80:
        return text.strip()
    cut = text[: target_chars + 40]
    end = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if end >= target_chars - 150:
        return cut[: end + 1].strip()
    return cut.strip()


def _usable_paragraphs(
    paras: list[str],
    runtime: dict[str, Any],
    site_id: str,
) -> list[str]:
    ads = list(runtime.get("ad_blacklist") or [])
    noise = list(runtime.get("ui_noise_b") or []) if site_id == "site_b" else []
    out: list[str] = []
    for p in paras:
        p = (p or "").strip()
        if len(p) < 8 or len(p) > 4000:
            continue
        if any(w in p for w in ads):
            continue
        if any(w == p or p.startswith(w) for w in noise):
            continue
        if NAV_PATTERN.match(p):
            continue
        out.append(p)
    return list(dict.fromkeys(out))
