from __future__ import annotations

import json
import re
from typing import Callable

JudgeFn = Callable[[str], str]


class LLMJudge:
    """Optional L3 judge via injected API complete function."""

    def __init__(self, enabled: bool, complete_fn: JudgeFn | None = None) -> None:
        self.enabled = enabled and complete_fn is not None
        self._complete = complete_fn

    def judge(
        self,
        tag: str,
        scene_keywords: list[str],
        candidates: list[str],
    ) -> list[tuple[int, float]] | None:
        if not self.enabled or not self._complete or not candidates:
            return None
        merged: list[tuple[int, float]] = []
        batch = 4
        for start in range(0, len(candidates), batch):
            chunk = candidates[start : start + batch]
            lines = [f"[{i + 1}] {c}" for i, c in enumerate(chunk)]
            prompt = (
                "你是小说风格参考审核员。只判断文段是否写到目标关键词所指的情节，不要改写原文。\n"
                f"目标关键词：「{tag}」。文段必须实际出现该关键词，或明确描写该关键词所指的行为/场景。\n"
                f"场景关键词：{scene_keywords or ['（未指定）']}\n"
                "打分 0~1：\n"
                "- ≥0.6：合格，正文确实在写该关键词相关情节\n"
                "- <0.6：不合格，只是书名/标签沾边，正文与关键词无关，或是广告、导航\n"
                '只返回 JSON 数组：[{"id":1,"score":0.82,"pass":true}, ...]\n'
                "不要解释。\n\n" + "\n".join(lines)
            )
            raw = self._complete(prompt)
            parsed = _parse_scores(raw, len(chunk))
            if not parsed:
                continue
            for idx, score in parsed:
                merged.append((start + idx, score))
        return merged or None


def _parse_scores(raw: str, n: int) -> list[tuple[int, float]] | None:
    if not raw:
        return None
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out: list[tuple[int, float]] = []
    for item in data:
        try:
            idx = int(item.get("id")) - 1
            score = float(item.get("score"))
            if "pass" in item and item["pass"] is False:
                score = min(score, 0.59)
        except Exception:
            continue
        if 0 <= idx < n:
            out.append((idx, score))
    return out or None
