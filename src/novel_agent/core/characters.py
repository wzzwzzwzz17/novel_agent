from __future__ import annotations

import json
import re

from novel_agent.core.models import CharacterCard
from novel_agent.providers.openai_compatible import OpenAICompatibleProvider

OPTIMIZE_PROMPT = """你是角色设定优化助手。把用户的原始角色描述整理成结构化人设，用于长篇小说创作，避免 OOC。
只返回 JSON，不要 markdown，不要解释。字段：
{{
  "optimized": "完整人设一段话（含性格、动机、说话方式）",
  "appearance": "外貌",
  "personality": "性格",
  "background": "背景经历",
  "speech_style": "说话习惯",
  "taboo": "禁忌行为/绝不做什么",
  "goal": "人物目标",
  "relation": "与他人关系（若原文无则写「待补充」）"
}}

角色名：{name}
原始描述：
{raw}
"""


async def optimize_character(
    char: CharacterCard,
    provider: OpenAICompatibleProvider,
) -> CharacterCard:
    raw = (char.raw_input or "").strip() or char.optimized.strip()
    if not raw:
        raise ValueError("角色原始描述为空，无法优化")
    messages = [
        {
            "role": "user",
            "content": OPTIMIZE_PROMPT.format(name=char.name, raw=raw),
        }
    ]
    text = await provider.complete(messages, temperature=0.4, top_p=0.9, max_tokens=1200)
    data = _parse_json_object(text)
    if not data:
        # fallback: keep raw as optimized blob
        char.optimized = raw
        return char
    char.optimized = str(data.get("optimized") or raw).strip()
    for field in (
        "appearance",
        "personality",
        "background",
        "speech_style",
        "taboo",
        "goal",
        "relation",
    ):
        val = data.get(field)
        if val is not None and str(val).strip():
            setattr(char, field, str(val).strip())
    return char


def _parse_json_object(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if fence:
        t = fence.group(1).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
