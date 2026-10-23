from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from novel_ref.auditor import audit
from novel_ref.cache import TTLCache
from novel_ref.discovery import pick_chapter_url
from novel_ref.errors import (
    CaptchaError,
    CircuitOpenError,
    NovelRefError,
    RobotsDisallowError,
)
from novel_ref.extractor import best_keyword_span, extract_paragraphs, wide_chunks
from novel_ref.fetcher import Fetcher
from novel_ref.keywords import KeywordRegistry
from novel_ref.llm_adapter import LLMJudge
from novel_ref.models import ReferenceParagraph, ReferenceResult
from novel_ref.paths import keywords_path, runtime_path
from novel_ref.site_adapter import build_adapters
from novel_ref.site_adapter.base import NovelMeta

logger = logging.getLogger(__name__)

NOTE_TMPL = (
    "以下段落来自关键词「{tag}」的随机作品（多站点参考），"
    "仅作风格参考，可能不完全匹配，请自行判断；禁止复制传播原文。"
)

_registry: KeywordRegistry | None = None
_runtime: dict[str, Any] | None = None
_fetcher: Fetcher | None = None
_result_cache = TTLCache(maxsize=128, default_ttl=1800)
_url_cache = TTLCache(maxsize=256, default_ttl=600)
_judge_factory: Callable[[], LLMJudge] | None = None


def configure(
    *,
    keywords_file: Path | None = None,
    runtime_file: Path | None = None,
    judge_factory: Callable[[], LLMJudge] | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> None:
    global _registry, _runtime, _fetcher, _judge_factory
    kp = keywords_file or keywords_path()
    rp = runtime_file or runtime_path()
    _registry = KeywordRegistry(kp)
    _runtime = yaml.safe_load(rp.read_text(encoding="utf-8")) or {}
    if runtime_overrides:
        _runtime.update(runtime_overrides)
    # env overlays
    if os.environ.get("NOVELREF_PROXY"):
        _runtime["proxy"] = os.environ["NOVELREF_PROXY"]
    if os.environ.get("NOVELREF_LLM_JUDGE", "").lower() in ("1", "true", "yes"):
        _runtime["llm_enable"] = True
    _fetcher = Fetcher(_runtime)
    _judge_factory = judge_factory


def _ensure() -> tuple[KeywordRegistry, dict[str, Any], Fetcher]:
    global _registry, _runtime, _fetcher
    if _registry is None or _runtime is None or _fetcher is None:
        configure()
    assert _registry and _runtime and _fetcher
    return _registry, _runtime, _fetcher


def list_tags() -> list[str]:
    reg, _, _ = _ensure()
    return reg.list_tags()


def _fetch_site_candidates(
    site_id: str,
    keyword: str,
    scene_keywords: list[str],
    runtime: dict[str, Any],
    fetcher: Fetcher,
    *,
    want: int,
) -> tuple[list[tuple[str, NovelMeta]], str]:
    """先按章切成约 2000 字大段，再从每段里截最贴关键词的约 500 字。"""
    adapters = build_adapters(runtime, fetcher)
    adapter = adapters.get(site_id)
    if not adapter:
        return [], "adapter missing"
    try:
        metas = adapter.search_novel(keyword, int(runtime.get("max_candidates", 5)))
    except Exception as e:
        return [], f"search fail: {e}"
    if not metas:
        return [], "搜索无候选"
    metas = sorted(metas, key=lambda m: len(m.chapter_urls), reverse=True)
    wide_target = int(runtime.get("wide_chars", 2000))
    span_target = int(runtime.get("chunk_chars", 500))
    max_chapters = int(runtime.get("max_chapters", 8))
    selected: list[tuple[str, NovelMeta]] = []
    grabbed = 0
    tried = 0
    for meta in metas:
        chapters = list(dict.fromkeys(meta.chapter_urls))
        if not chapters:
            continue
        chapters.sort(key=lambda u: _url_cache.get(u) is not None)
        for chapter in chapters:
            if len(selected) >= want or tried >= max_chapters:
                break
            tried += 1
            try:
                soup = fetcher.get_soup(chapter, adapter.robots_url)
            except Exception:
                continue
            _url_cache.put(chapter, True, ttl=float(runtime.get("url_cache_ttl", 600)))
            raw = extract_paragraphs(soup, site_id, runtime)
            wides = wide_chunks(
                raw,
                runtime,
                site_id=site_id,
                target_chars=wide_target,
                limit=max(want, 10),
            )
            for wide in wides:
                grabbed += 1
                span = best_keyword_span(
                    wide,
                    [keyword],
                    scene_keywords,
                    target_chars=span_target,
                )
                if span:
                    selected.append((span, meta))
                if len(selected) >= want and grabbed >= want:
                    break
        if len(selected) >= want or tried >= max_chapters:
            break
    if not selected:
        return [], f"抓到大段 {grabbed} 块，其中没有含「{keyword}」的片段"
    return selected, ""


def get_reference(
    tag: str,
    scene_keywords: list[str] | None = None,
    max_paragraphs: int = 10,
    *,
    require_both_sites: bool = False,
) -> ReferenceResult:
    """双站都尝试抓取，合并候选后做 L3 合格判定；永不抛异常。"""
    scene_keywords = list(scene_keywords or [])
    max_paragraphs = max(1, min(int(max_paragraphs), 12))
    try:
        reg, runtime, fetcher = _ensure()
        normalized = reg.normalize(tag)
        if not normalized:
            return ReferenceResult(
                status="empty",
                reason="不支持的标签",
                note=NOTE_TMPL.format(tag=tag),
            )
        cache_key = (
            normalized,
            tuple(sorted(scene_keywords)),
            max_paragraphs,
            "both",
        )
        cached = _result_cache.get(cache_key)
        if cached is not None:
            return cached

        priority = list(runtime.get("site_priority") or ["site_a", "site_b"])
        site_packs: list[dict[str, Any]] = []
        all_candidates: list[str] = []
        meta_by_idx: list[tuple[NovelMeta, str]] = []  # meta + site_id

        per_site = max(4, (max_paragraphs + 1) // 2)
        for site_id in priority:
            pairs, reason = _fetch_site_candidates(
                site_id,
                normalized,
                scene_keywords,
                runtime,
                fetcher,
                want=per_site,
            )
            site_packs.append(
                {
                    "site_id": site_id,
                    "ok": bool(pairs),
                    "reason": reason,
                    "title": pairs[0][1].title if pairs else "",
                    "count": len(pairs),
                }
            )
            if not pairs:
                logger.info("site %s empty: %s", site_id, reason)
                continue
            for text, meta in pairs:
                all_candidates.append(text)
                meta_by_idx.append((meta, site_id))

        both_ok = all(p["ok"] for p in site_packs) and len(site_packs) >= 2
        if require_both_sites and not both_ok:
            result = ReferenceResult(
                status="empty",
                reason="未同时从两个站点抓到文段: "
                + "; ".join(f"{p['site_id']}={p['reason'] or 'ok'}" for p in site_packs),
                note=NOTE_TMPL.format(tag=normalized),
            )
            _result_cache.put(
                cache_key, result, ttl=float(runtime.get("cache_ttl_fail", 300))
            )
            return result

        if not all_candidates:
            result = ReferenceResult(
                status="empty",
                reason="全部站点无可用段落: "
                + "; ".join(f"{p['site_id']}={p['reason']}" for p in site_packs),
                note=NOTE_TMPL.format(tag=normalized),
            )
            _result_cache.put(
                cache_key, result, ttl=float(runtime.get("cache_ttl_fail", 300))
            )
            return result

        llm_enable = bool(runtime.get("llm_enable", False))
        if _judge_factory is not None and llm_enable:
            judge = _judge_factory()
        else:
            judge = LLMJudge(enabled=False)

        # 按站点交错取样，保证双站都有机会进审
        interleaved: list[str] = []
        inter_meta: list[tuple[NovelMeta, str]] = []
        buckets: dict[str, list[tuple[str, NovelMeta, str]]] = {}
        for text, (meta, sid) in zip(all_candidates, meta_by_idx):
            buckets.setdefault(sid, []).append((text, meta, sid))
        # round-robin
        while any(buckets.values()):
            for sid in list(buckets.keys()):
                if not buckets[sid]:
                    continue
                text, meta, s = buckets[sid].pop(0)
                interleaved.append(text)
                inter_meta.append((meta, s))
            # drop empty
            buckets = {k: v for k, v in buckets.items() if v}

        paras, conf = audit(
            interleaved[:16],
            normalized,
            scene_keywords,
            judge,
            source_novel="",
            source_chapter="",
            source_site="",
            max_paragraphs=max_paragraphs,
        )
        # 回填真实来源
        filled: list[ReferenceParagraph] = []
        used_sites: set[str] = set()
        for p in paras:
            # 在 interleaved 中找原文
            try:
                idx = interleaved.index(p.text)
                meta, sid = inter_meta[idx]
            except ValueError:
                meta, sid = inter_meta[0]
            filled.append(
                ReferenceParagraph(
                    text=p.text,
                    source_novel=meta.title,
                    source_chapter=pick_chapter_url(meta) or meta.url,
                    source_site=sid,
                    score=p.score,
                )
            )
            used_sites.add(sid)

        if len(filled) < max_paragraphs:
            have = {p.text for p in filled}
            for text, (meta, sid) in zip(interleaved, inter_meta):
                if text in have or normalized not in text:
                    continue
                filled.append(
                    ReferenceParagraph(
                        text=text,
                        source_novel=meta.title,
                        source_chapter=meta.url,
                        source_site=sid,
                        score=0.6,
                    )
                )
                have.add(text)
                used_sites.add(sid)
                if len(filled) >= max_paragraphs:
                    break
        if not filled:
            result = ReferenceResult(
                status="empty",
                reason="语义审核未通过（API 判定均不合格）",
                note=NOTE_TMPL.format(tag=normalized),
            )
            _result_cache.put(
                cache_key, result, ttl=float(runtime.get("cache_ttl_fail", 300))
            )
            return result

        # 尽量让返回结果覆盖双站（若审核后只剩单站，从合格池外再补同站外段落）
        if len(used_sites) < 2 and both_ok:
            for text, (meta, sid) in zip(interleaved, inter_meta):
                if sid in used_sites:
                    continue
                if any(x.text == text for x in filled):
                    continue
                filled.append(
                    ReferenceParagraph(
                        text=text,
                        source_novel=meta.title,
                        source_chapter=pick_chapter_url(meta) or meta.url,
                        source_site=sid,
                        score=0.55,
                    )
                )
                used_sites.add(sid)
                if len(filled) >= max_paragraphs or len(used_sites) >= 2:
                    break
            filled = filled[:max_paragraphs]

        result = ReferenceResult(
            status="ok",
            paragraphs=filled,
            confidence=conf,  # type: ignore[arg-type]
            reason="; ".join(
                f"{p['site_id']}:{'ok' if p['ok'] else p['reason']}" for p in site_packs
            ),
            note=NOTE_TMPL.format(tag=normalized),
        )
        _result_cache.put(cache_key, result, ttl=float(runtime.get("cache_ttl_ok", 1800)))
        return result
    except CircuitOpenError as e:
        return ReferenceResult(status="error", reason=str(e), note=NOTE_TMPL.format(tag=tag))
    except CaptchaError:
        return ReferenceResult(status="error", reason="触发反爬", note=NOTE_TMPL.format(tag=tag))
    except RobotsDisallowError as e:
        return ReferenceResult(status="error", reason=str(e), note=NOTE_TMPL.format(tag=tag))
    except NovelRefError as e:
        return ReferenceResult(status="error", reason=str(e), note=NOTE_TMPL.format(tag=tag))
    except Exception as e:
        logger.exception("get_reference failed")
        return ReferenceResult(
            status="error",
            reason=f"内部异常: {type(e).__name__}: {e}",
            note=NOTE_TMPL.format(tag=tag),
        )
