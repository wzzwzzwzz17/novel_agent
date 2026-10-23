from __future__ import annotations

import json
import re
from typing import Any

from novel_agent.core.models import EventMemory, NovelSession, new_id
from novel_agent.providers.openai_compatible import OpenAICompatibleProvider

EXTRACT_PROMPT = """从下面这段刚写完的小说正文中，提取对后续剧情重要的记忆条目。
只返回 JSON 数组，不要解释：
[{{"content":"一句话事件/关系/伏笔","weight":"high|medium|low"}}]

规则：
- high：主线转折、人物关系质变、关键伏笔
- medium：一般情节推进
- low：氛围细节、次要信息
- 最多 5 条；没有则返回 []

正文：
{text}
"""

SUMMARY_PROMPT = """把下列事件记忆压缩成不超过 400 字的「全局剧情摘要」，保留主线走向、核心人物状态与未回收伏笔。
只输出摘要正文，不要标题。

已有摘要：
{old}

新事件：
{events}
"""

COMPRESS_PROMPT = """将以下较早的对话轮次压缩成不超过 200 字的剧情摘要，供后续续写使用。只输出摘要：

{dialog}
"""


async def extract_events(
    text: str,
    provider: OpenAICompatibleProvider,
    *,
    source: str = "",
) -> list[EventMemory]:
    messages = [{"role": "user", "content": EXTRACT_PROMPT.format(text=text[:6000])}]
    raw = await provider.complete(messages, temperature=0.3, top_p=0.9, max_tokens=800)
    items = _parse_json_array(raw)
    out: list[EventMemory] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        weight = str(item.get("weight") or "medium").lower()
        if weight not in ("high", "medium", "low"):
            weight = "medium"
        out.append(EventMemory(content=content, weight=weight, source=source))  # type: ignore[arg-type]
    return out


async def refresh_global_summary(
    session: NovelSession,
    provider: OpenAICompatibleProvider,
    new_events: list[EventMemory],
) -> str:
    if not new_events and session.memory.global_summary:
        return session.memory.global_summary
    events_text = "\n".join(f"- [{e.weight}] {e.content}" for e in new_events) or "（无）"
    messages = [
        {
            "role": "user",
            "content": SUMMARY_PROMPT.format(
                old=session.memory.global_summary or "（空）",
                events=events_text,
            ),
        }
    ]
    summary = await provider.complete(messages, temperature=0.3, top_p=0.9, max_tokens=600)
    return (summary or "").strip() or session.memory.global_summary


async def compress_old_history(
    session: NovelSession,
    provider: OpenAICompatibleProvider,
) -> NovelSession:
    """滑动窗口：超出 recent_turns 的旧对话压缩进 compressed_digests，并从 history 去掉。"""
    recent = max(2, session.session_config.recent_turns)
    keep = recent * 2
    if len(session.chat_history) <= keep + 4:
        return session
    old = session.chat_history[: -keep]
    keep_msgs = session.chat_history[-keep:]
    dialog = "\n".join(f"{m.role}: {m.content[:500]}" for m in old[-20:])
    messages = [{"role": "user", "content": COMPRESS_PROMPT.format(dialog=dialog)}]
    digest = await provider.complete(messages, temperature=0.3, top_p=0.9, max_tokens=400)
    digest = (digest or "").strip()
    if digest:
        session.memory.compressed_digests.append(digest)
        # fold into global summary lightly
        if not session.memory.global_summary:
            session.memory.global_summary = digest
        else:
            session.memory.global_summary = (
                session.memory.global_summary.strip() + "\n" + digest
            )[-2000:]
    session.chat_history = keep_msgs
    return session


def _query_tokens(query: str) -> list[str]:
    q = (query or "").strip()
    parts = [t for t in re.split(r"[\s,，。；;、！？!?]+", q) if len(t) >= 2]
    compact = re.sub(r"\s+", "", q)
    grams: list[str] = []
    for n in (2, 3):
        for i in range(max(0, len(compact) - n + 1)):
            grams.append(compact[i : i + n])
    # 去重保序；过滤过泛单字已由 n>=2 保证
    return list(dict.fromkeys(parts + grams))


def recall_events(session: NovelSession, query: str, *, limit: int = 8) -> list[EventMemory]:
    """高权重必带；中低权重按字面/字 n-gram 命中召回。"""
    high = [e for e in session.memory.event_memory if e.weight == "high"]
    others = [e for e in session.memory.event_memory if e.weight != "high"]
    tokens = _query_tokens(query)
    scored: list[tuple[int, EventMemory]] = []
    for e in others:
        score = 0
        for t in tokens:
            if t in e.content:
                score += len(t)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    picked = [e for _, e in scored[: max(0, limit - len(high))]]
    seen: set[str] = set()
    out: list[EventMemory] = []
    for e in high[-12:] + picked:
        if e.event_id in seen:
            continue
        seen.add(e.event_id)
        out.append(e)
    return out


def upsert_event(session: NovelSession, event: EventMemory) -> NovelSession:
    if not event.event_id:
        event.event_id = new_id()
    for i, e in enumerate(session.memory.event_memory):
        if e.event_id == event.event_id:
            session.memory.event_memory[i] = event
            return session
    session.memory.event_memory.append(event)
    return session


def delete_event(session: NovelSession, event_id: str) -> NovelSession:
    session.memory.event_memory = [
        e for e in session.memory.event_memory if e.event_id != event_id
    ]
    return session


def _parse_json_array(text: str) -> list[Any]:
    if not text:
        return []
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if fence:
        t = fence.group(1).strip()
    m = re.search(r"\[[\s\S]*\]", t)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
