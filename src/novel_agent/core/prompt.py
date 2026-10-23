from __future__ import annotations

from novel_agent.core.memory import recall_events
from novel_agent.core.models import EventMemory, NovelSession
from novel_ref.models import ReferenceResult


SYSTEM_DEFAULT = (
    "你是长篇小说创作助手。严格遵循总纲与角色设定，保持人设一致。"
    "直接输出小说正文，不要输出元评论、大纲分析或工具标记。"
    "若提供了「风格参考素材」，只借鉴氛围与笔法，禁止抄袭或大段复述原文。"
)


def compose_messages(
    session: NovelSession,
    user_instruction: str,
    *,
    reference: ReferenceResult | None = None,
    recalled: list[EventMemory] | None = None,
    plugin_blocks: list[str] | None = None,
    system_prompt: str = SYSTEM_DEFAULT,
) -> list[dict[str, str]]:
    parts: list[str] = [system_prompt.strip()]

    if session.novel_outline.strip():
        parts.append(f"【小说总纲】\n{session.novel_outline.strip()}")

    if session.memory.global_summary.strip():
        parts.append(f"【全局剧情摘要】\n{session.memory.global_summary.strip()}")

    digests = session.memory.compressed_digests[-3:]
    if digests:
        parts.append("【早期对话压缩】\n" + "\n---\n".join(digests))

    active_chars = [c for c in session.character_list if c.active]
    if active_chars:
        blocks = []
        for c in active_chars:
            body = c.optimized.strip() or c.raw_input.strip()
            extra = []
            for label, val in (
                ("外貌", c.appearance),
                ("性格", c.personality),
                ("背景", c.background),
                ("说话习惯", c.speech_style),
                ("禁忌", c.taboo),
                ("目标", c.goal),
                ("关系", c.relation),
            ):
                if val.strip():
                    extra.append(f"{label}: {val.strip()}")
            text = f"· {c.name}\n{body}"
            if extra:
                text += "\n" + "\n".join(extra)
            blocks.append(text)
        parts.append("【活跃角色卡】\n" + "\n\n".join(blocks))

    events = recalled if recalled is not None else recall_events(session, user_instruction)
    if events:
        parts.append(
            "【召回事件记忆】\n"
            + "\n".join(f"- [{e.weight}] {e.content}" for e in events)
        )

    for block in plugin_blocks or []:
        if block.strip():
            parts.append(block.strip())

    if reference and reference.status == "ok" and reference.paragraphs:
        ref_lines = [reference.note]
        for i, p in enumerate(reference.paragraphs, 1):
            ref_lines.append(
                f"[{i}]（{p.source_novel}/{p.source_site} score={p.score:.2f}）\n{p.text}"
            )
        parts.append(
            "【风格参考素材（仅参考，禁止复制）】\n" + "\n\n".join(ref_lines)
        )

    system_content = "\n\n".join(parts)
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    recent = session.session_config.recent_turns
    history = session.chat_history[-recent * 2 :]
    for msg in history:
        if msg.role in ("user", "assistant"):
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": user_instruction})
    return messages
