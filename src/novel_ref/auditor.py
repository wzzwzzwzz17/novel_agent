from __future__ import annotations

from dataclasses import dataclass

from novel_ref.llm_adapter import LLMJudge
from novel_ref.models import ReferenceParagraph


@dataclass
class ScoredParagraph:
    text: str
    score: float
    source_novel: str = ""
    source_chapter: str = ""
    source_site: str = ""


def audit(
    candidates: list[str],
    tag: str,
    scene_keywords: list[str],
    llm: LLMJudge,
    *,
    source_novel: str = "",
    source_chapter: str = "",
    source_site: str = "",
    max_paragraphs: int = 3,
) -> tuple[list[ReferenceParagraph], str]:
    """Returns (paragraphs, confidence)."""
    if not candidates:
        return [], "low"
    scored = llm.judge(tag, scene_keywords, candidates[:16])
    if scored is None:
        kept = [
            ReferenceParagraph(
                text=p,
                source_novel=source_novel,
                source_chapter=source_chapter,
                source_site=source_site,
                score=0.5,
            )
            for p in candidates[:max_paragraphs]
        ]
        return kept, "low"
    paras: list[ReferenceParagraph] = []
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    for idx, score in ranked:
        if score < 0.6:
            continue
        paras.append(
            ReferenceParagraph(
                text=candidates[idx],
                source_novel=source_novel,
                source_chapter=source_chapter,
                source_site=source_site,
                score=score,
            )
        )
        if len(paras) >= max_paragraphs:
            break
    if not paras:
        return [], "low"
    avg = sum(p.score for p in paras) / len(paras)
    if len(paras) >= 2 and avg >= 0.75:
        conf = "high"
    else:
        conf = "medium"
    return paras, conf
