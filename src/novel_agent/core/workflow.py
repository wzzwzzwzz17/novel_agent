"""全自动创作工作流（参考 SillyTavern Prompt 组装 + Smart-Memory 写后提取范式）。

一轮续写固定流水线，无需人工补代码：
1. 规划：从用户指令自动抽取 NovelRef 关键词 + 场景词（AUX API）
2. 参考：双站抓取 → L3 API 合格判定 → 注入参考区
3. 组装：总纲 / 摘要 / 角色卡 / 召回记忆 / 参考 / 近期对话
4. 生成：主模型续写正文
5. 后处理：清洗、OOC 提示
6. 记忆：事件提取 + 摘要更新 + 滑动窗口压缩
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from novel_agent.core.engine import NovelEngine, _ooc_hints, _post_clean
from novel_agent.core.memory import (
    compress_old_history,
    extract_events,
    recall_events,
    refresh_global_summary,
    upsert_event,
)
from novel_agent.core.models import ChatMessage, ToolLogEntry
from novel_agent.core.prompt import compose_messages
from novel_ref import list_tags
from novel_ref.models import ReferenceResult

logger = logging.getLogger(__name__)

PLAN_PROMPT = """你是小说创作规划助手。根据用户续写指令，从给定标签白名单中选 1 个最相关标签，并给出 2~4 个场景关键词。
只返回 JSON：{{"keyword":"标签","scene_keywords":["词1","词2"],"need_reference":true}}
若指令明显不需要外部风格参考，need_reference=false。
标签白名单：{tags}
用户指令：{instruction}
"""


async def plan_reference(
    engine: NovelEngine,
    instruction: str,
    mode: str,
) -> dict[str, Any]:
    tags = list_tags()
    provider = engine.router.for_task("aux", mode)
    messages = [
        {
            "role": "user",
            "content": PLAN_PROMPT.format(tags=tags, instruction=instruction),
        }
    ]
    try:
        raw = await provider.complete(messages, temperature=0.2, top_p=0.9, max_tokens=300)
    except Exception as e:
        logger.warning("plan_reference llm fail: %s", e)
        return _heuristic_plan(instruction, tags)

    data = _parse_json_obj(raw) or {}
    keyword = str(data.get("keyword") or "").strip()
    if keyword not in tags:
        # alias-ish: substring match
        keyword = next((t for t in tags if t in instruction), "")
    scenes = data.get("scene_keywords") or []
    if not isinstance(scenes, list):
        scenes = []
    scenes = [str(s).strip() for s in scenes if str(s).strip()][:4]
    need = bool(data.get("need_reference", True))
    if not keyword:
        return _heuristic_plan(instruction, tags)
    return {
        "keyword": keyword if need else None,
        "scene_keywords": scenes,
        "need_reference": need,
        "source": "llm",
    }


def _heuristic_plan(instruction: str, tags: list[str]) -> dict[str, Any]:
    hit = next((t for t in tags if t in instruction), None)
    # 简单场景词：抽 2 字以上中文片段
    toks = re.findall(r"[\u4e00-\u9fff]{2,4}", instruction)
    scenes = []
    for t in toks:
        if t not in scenes and t != hit:
            scenes.append(t)
        if len(scenes) >= 3:
            break
    return {
        "keyword": hit,
        "scene_keywords": scenes,
        "need_reference": bool(hit),
        "source": "heuristic",
    }


def _parse_json_obj(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


async def run_auto_turn(
    engine: NovelEngine,
    session_id: str,
    instruction: str,
    *,
    force_keyword: str | None = None,
    force_scene_keywords: list[str] | None = None,
    use_reference: bool = True,
) -> dict[str, Any]:
    """一键全自动续写：规划 → 双站参考+审稿 → 生成 → 记忆。"""
    session = engine.store.load(session_id)
    if session is None:
        raise KeyError(f"session not found: {session_id}")

    mode = session.session_config.model_mode or engine.settings.model_mode
    plan: dict[str, Any]
    if force_keyword:
        plan = {
            "keyword": force_keyword,
            "scene_keywords": list(force_scene_keywords or []),
            "need_reference": True,
            "source": "user",
        }
    elif use_reference and session.session_config.enable_novelref and engine.settings.novelref_enabled:
        plan = await plan_reference(engine, instruction, mode)
    else:
        plan = {
            "keyword": None,
            "scene_keywords": [],
            "need_reference": False,
            "source": "disabled",
        }

    ref: ReferenceResult | None = None
    if plan.get("need_reference") and plan.get("keyword"):
        ref = await asyncio.to_thread(
            engine.maybe_get_reference,
            session,
            keyword=plan["keyword"],
            scene_keywords=plan.get("scene_keywords") or [],
            call_index=0,
        )
        # 双站都失败也不阻塞
        if ref and ref.status != "ok":
            engine._append_tool_log(
                session,
                ToolLogEntry(
                    tool="auto_plan",
                    args=plan,
                    status=ref.status,
                    reason=ref.reason,
                ),
            )

    engine._append_tool_log(
        session,
        ToolLogEntry(
            tool="auto_plan",
            args=plan,
            status="ok",
            preview=[str(plan.get("keyword")), ",".join(plan.get("scene_keywords") or [])],
        ),
    )

    recalled = recall_events(session, instruction)
    from novel_agent.plugins.loader import prompt_injections

    messages = compose_messages(
        session,
        instruction,
        reference=ref,
        recalled=recalled,
        plugin_blocks=prompt_injections(engine.settings, session, instruction),
    )
    provider = engine.router.for_task("write", mode)
    cfg = session.session_config
    text = await provider.complete(
        messages,
        temperature=cfg.temperature,
        top_p=cfg.top_p,
        max_tokens=cfg.max_tokens,
    )
    text = _post_clean(text)
    min_chars = max(0, int(cfg.min_reply_chars or 0))
    retried = False
    if min_chars and len(text) < min_chars:
        retry_messages = messages + [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"上一稿只有{len(text)}字，少于要求的{min_chars}字。"
                    f"请重写这一段小说正文，只输出正文，篇幅不少于{min_chars}字。"
                ),
            },
        ]
        longer = _post_clean(
            await provider.complete(
                retry_messages,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                max_tokens=cfg.max_tokens,
            )
        )
        if len(longer) > len(text):
            text = longer
        retried = True
    ooc_hints = _ooc_hints(session, text) if cfg.enable_ooc_hint else []

    session.chat_history.append(ChatMessage(role="user", content=instruction))
    session.chat_history.append(
        ChatMessage(
            role="assistant",
            content=text,
            meta={
                "provider": provider.name,
                "workflow": "auto",
                "plan": plan,
                "reference_status": ref.status if ref else None,
                "ooc_hints": ooc_hints,
                "recalled_events": len(recalled),
                "chars": len(text),
                "min_reply_chars": min_chars,
                "retried_for_length": retried,
            },
        )
    )

    memory_note: dict[str, Any] = {"extracted": 0, "compressed": False}
    if cfg.enable_auto_memory:
        try:
            aux = engine.router.for_task("aux", mode)
            events = await extract_events(
                text, aux, source=f"auto:{len(session.chat_history)}"
            )
            for e in events:
                upsert_event(session, e)
            if events:
                session.memory.global_summary = await refresh_global_summary(
                    session, aux, events
                )
            before = len(session.chat_history)
            session = await compress_old_history(session, aux)
            memory_note = {
                "extracted": len(events),
                "compressed": len(session.chat_history) < before,
            }
        except Exception as e:
            logger.warning("auto memory failed: %s", e)
            memory_note["error"] = str(e)

    engine.store.save(session)
    return {
        "session_id": session.session_id,
        "text": text,
        "plan": plan,
        "reference": ref.model_dump() if ref else None,
        "provider": provider.name,
        "ooc_hints": ooc_hints,
        "memory": memory_note,
        "tool_logs": [t.model_dump() for t in session.tool_logs[-8:]],
        "workflow": "auto",
        "chars": len(text),
        "retried_for_length": retried,
    }


def _local_url(url: str) -> bool:
    host = (url or "").lower()
    return "127.0.0.1" in host or "localhost" in host


async def _stream_once(provider, messages, cfg):
    from novel_agent.core.speed_meter import record_stream

    parts: list[str] = []
    usage: dict = {}
    async for event in provider.stream_events(
        messages,
        temperature=cfg.temperature,
        top_p=cfg.top_p,
        max_tokens=cfg.max_tokens,
    ):
        if event.get("type") == "delta":
            parts.append(str(event.get("text") or ""))
            yield event
        elif event.get("type") == "speed":
            yield event
        elif event.get("type") == "usage":
            usage = event
    if _local_url(getattr(provider, "base_url", "")) and usage:
        speed = record_stream(
            usage,
            model=getattr(provider, "model", ""),
            base_url=getattr(provider, "base_url", ""),
        )
        yield {"type": "speed", **speed}
    yield {"type": "assembled", "text": _post_clean("".join(parts)), "usage": usage}


async def stream_auto_turn(
    engine: NovelEngine,
    session_id: str,
    instruction: str,
    *,
    force_keyword: str | None = None,
    force_scene_keywords: list[str] | None = None,
    use_reference: bool = True,
):
    """与 run_auto_turn 相同的准备步骤，正文改为流式吐出。"""
    session = engine.store.load(session_id)
    if session is None:
        raise KeyError(f"session not found: {session_id}")
    yield {"type": "status", "text": "正在准备上下文 · 已等 0s"}
    mode = session.session_config.model_mode or engine.settings.model_mode
    if force_keyword:
        plan = {
            "keyword": force_keyword,
            "scene_keywords": list(force_scene_keywords or []),
            "need_reference": True,
            "source": "user",
        }
    elif use_reference and session.session_config.enable_novelref and engine.settings.novelref_enabled:
        task = asyncio.create_task(plan_reference(engine, instruction, mode))
        started = time.perf_counter()
        while not task.done():
            yield {"type": "status", "text": f"正在规划 · 已等 {int(time.perf_counter() - started)}s"}
            await asyncio.sleep(1)
        plan = task.result()
    else:
        plan = {"keyword": None, "scene_keywords": [], "need_reference": False, "source": "disabled"}

    ref: ReferenceResult | None = None
    if plan.get("need_reference") and plan.get("keyword"):
        started = time.perf_counter()
        task = asyncio.create_task(
            asyncio.to_thread(
                engine.maybe_get_reference,
                session,
                keyword=plan["keyword"],
                scene_keywords=plan.get("scene_keywords") or [],
                call_index=0,
            )
        )
        while not task.done():
            yield {"type": "status", "text": f"正在抓取参考 · 已等 {int(time.perf_counter() - started)}s"}
            await asyncio.sleep(1)
        ref = task.result()
    recalled = recall_events(session, instruction)
    from novel_agent.plugins.loader import prompt_injections

    messages = compose_messages(
        session,
        instruction,
        reference=ref,
        recalled=recalled,
        plugin_blocks=prompt_injections(engine.settings, session, instruction),
    )
    provider = engine.router.for_task("write", mode)
    cfg = session.session_config
    yield {"type": "status", "text": "开始生成"}

    async def _collect(msgs):
        text = ""
        async for event in _stream_once(provider, msgs, cfg):
            if event.get("type") == "assembled":
                text = event.get("text") or ""
            else:
                yield event
        yield {"type": "assembled", "text": text}

    text = ""
    async for event in _collect(messages):
        if event.get("type") == "assembled":
            text = event.get("text") or ""
        else:
            yield event

    min_chars = max(0, int(cfg.min_reply_chars or 0))
    retried = False
    if min_chars and len(text) < min_chars:
        retried = True
        yield {"type": "replace"}
        yield {"type": "status", "text": f"不足 {min_chars} 字，正在重写"}
        retry_messages = messages + [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"上一稿只有{len(text)}字，少于要求的{min_chars}字。"
                    f"请重写这一段小说正文，只输出正文，篇幅不少于{min_chars}字。"
                ),
            },
        ]
        async for event in _collect(retry_messages):
            if event.get("type") == "assembled":
                longer = event.get("text") or ""
                if len(longer) > len(text):
                    text = longer
            else:
                yield event

    ooc_hints = _ooc_hints(session, text) if cfg.enable_ooc_hint else []
    session.chat_history.append(ChatMessage(role="user", content=instruction))
    session.chat_history.append(
        ChatMessage(
            role="assistant",
            content=text,
            meta={
                "provider": provider.name,
                "workflow": "stream",
                "plan": plan,
                "chars": len(text),
                "retried_for_length": retried,
                "ooc_hints": ooc_hints,
            },
        )
    )
    if cfg.enable_auto_memory:
        try:
            aux = engine.router.for_task("aux", mode)
            events = await extract_events(text, aux, source=f"stream:{len(session.chat_history)}")
            for event in events:
                upsert_event(session, event)
            if events:
                session.memory.global_summary = await refresh_global_summary(session, aux, events)
            session = await compress_old_history(session, aux)
        except Exception as exc:
            logger.warning("stream memory failed: %s", exc)
    engine.store.save(session)
    yield {
        "type": "done",
        "text": text,
        "chars": len(text),
        "retried_for_length": retried,
        "ooc_hints": ooc_hints,
        "provider": provider.name,
    }
