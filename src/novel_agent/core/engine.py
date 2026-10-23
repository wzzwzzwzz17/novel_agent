from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from novel_agent.core.characters import optimize_character
from novel_agent.core.memory import (
    compress_old_history,
    delete_event,
    extract_events,
    recall_events,
    refresh_global_summary,
    upsert_event,
)
from novel_agent.core.models import (
    CharacterCard,
    ChatMessage,
    EventMemory,
    NovelSession,
    ToolLogEntry,
)
from novel_agent.core.prompt import compose_messages
from novel_agent.core.router import ModelRouter
from novel_agent.core.session_store import SessionStore
from novel_agent.settings import Settings
from novel_ref.llm_adapter import LLMJudge
from novel_ref.models import ReferenceResult

logger = logging.getLogger(__name__)


class NovelEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = SessionStore(settings)
        self.router = ModelRouter(settings)
        self._wire_novelref()

    def _wire_novelref(self) -> None:
        if not self.settings.novelref_enabled:
            return
        from novel_ref.skill import configure

        settings = self.settings
        os.environ.setdefault("NOVELREF_PROXY", settings.novelref_proxy)
        enable = bool(settings.novelref_llm_judge and settings.aux_api_key())

        def factory() -> LLMJudge:
            if not enable:
                return LLMJudge(enabled=False)

            def complete_fn(prompt: str) -> str:
                url = settings.aux_base_url().rstrip("/") + "/chat/completions"
                headers = {"Content-Type": "application/json"}
                key = settings.aux_api_key()
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                payload = {
                    "model": settings.aux_model(),
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是严格的文段审核员，只输出 JSON。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 800,
                }
                try:
                    with httpx.Client(timeout=90.0) as client:
                        r = client.post(url, headers=headers, json=payload)
                        r.raise_for_status()
                        data = r.json()
                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    return str(msg.get("content") or choice.get("text") or "")
                except Exception as e:
                    logger.warning("NovelRef L3 judge failed: %s", e)
                    return ""

            return LLMJudge(enabled=True, complete_fn=complete_fn)

        configure(
            judge_factory=factory,
            runtime_overrides={
                "llm_enable": enable,
                "proxy": settings.novelref_proxy,
                "trust_env": settings.novelref_trust_env,
            },
        )

    def _append_tool_log(self, session: NovelSession, entry: ToolLogEntry) -> None:
        session.tool_logs.append(entry)
        session.tool_logs = session.tool_logs[-50:]

    def maybe_get_reference(
        self,
        session: NovelSession,
        *,
        keyword: str | None,
        scene_keywords: list[str] | None,
        call_index: int = 0,
        require_both_sites: bool = False,
    ) -> ReferenceResult | None:
        cfg = session.session_config
        if not (self.settings.novelref_enabled and cfg.enable_novelref):
            return None
        if not keyword:
            return None
        if call_index >= 2:
            result = ReferenceResult(
                status="error",
                reason="本轮 NovelRef 调用已达上限(2)",
            )
            self._append_tool_log(
                session,
                ToolLogEntry(
                    tool="get_reference",
                    args={"tag": keyword, "scene_keywords": scene_keywords or []},
                    status=result.status,
                    reason=result.reason,
                ),
            )
            return result

        from novel_ref import get_reference

        result = get_reference(
            keyword,
            scene_keywords or [],
            max_paragraphs=10,
            require_both_sites=require_both_sites,
        )
        preview = [p.text[:120] for p in result.paragraphs[:4]]
        self._append_tool_log(
            session,
            ToolLogEntry(
                tool="get_reference",
                args={
                    "tag": keyword,
                    "scene_keywords": scene_keywords or [],
                    "require_both_sites": require_both_sites,
                },
                status=result.status,
                reason=result.reason,
                confidence=result.confidence,
                preview=preview,
            ),
        )
        return result

    async def optimize_char(self, session_id: str, char_id: str) -> CharacterCard:
        session = self.store.load(session_id)
        if session is None:
            raise KeyError(f"session not found: {session_id}")
        mode = session.session_config.model_mode or self.settings.model_mode
        provider = self.router.for_task("aux", mode)
        target = None
        for c in session.character_list:
            if c.char_id == char_id:
                target = c
                break
        if target is None:
            raise KeyError(f"character not found: {char_id}")
        optimized = await optimize_character(target, provider)
        for i, c in enumerate(session.character_list):
            if c.char_id == char_id:
                session.character_list[i] = optimized
                break
        self._append_tool_log(
            session,
            ToolLogEntry(
                tool="optimize_character",
                args={"char_id": char_id, "name": optimized.name},
                status="ok",
                preview=[(optimized.optimized or "")[:160]],
            ),
        )
        self.store.save(session)
        return optimized

    async def write(
        self,
        session_id: str,
        instruction: str,
        *,
        use_reference_keyword: str | None = None,
        scene_keywords: list[str] | None = None,
        auto: bool = True,
    ) -> dict[str, Any]:
        # 默认全自动流水线（规划→双站参考+审稿→生成→记忆）
        if auto:
            from novel_agent.core.workflow import run_auto_turn

            return await run_auto_turn(
                self,
                session_id,
                instruction,
                force_keyword=use_reference_keyword,
                force_scene_keywords=scene_keywords,
                use_reference=True,
            )

        session = self.store.load(session_id)
        if session is None:
            raise KeyError(f"session not found: {session_id}")

        ref = await asyncio.to_thread(
            self.maybe_get_reference,
            session,
            keyword=use_reference_keyword,
            scene_keywords=scene_keywords,
            call_index=0,
            require_both_sites=False,
        )
        recalled = recall_events(session, instruction)
        from novel_agent.plugins.loader import prompt_injections

        messages = compose_messages(
            session,
            instruction,
            reference=ref,
            recalled=recalled,
            plugin_blocks=prompt_injections(self.settings, session, instruction),
        )
        mode = session.session_config.model_mode or self.settings.model_mode
        provider = self.router.for_task("write", mode)
        cfg = session.session_config
        text = await provider.complete(
            messages,
            temperature=cfg.temperature,
            top_p=cfg.top_p,
            max_tokens=cfg.max_tokens,
        )
        text = _post_clean(text)
        ooc_hints = _ooc_hints(session, text) if cfg.enable_ooc_hint else []

        session.chat_history.append(ChatMessage(role="user", content=instruction))
        session.chat_history.append(
            ChatMessage(
                role="assistant",
                content=text,
                meta={
                    "provider": provider.name,
                    "reference_status": ref.status if ref else None,
                    "ooc_hints": ooc_hints,
                    "recalled_events": len(recalled),
                },
            )
        )

        memory_note: dict[str, Any] = {"extracted": 0, "compressed": False}
        if cfg.enable_auto_memory:
            try:
                aux = self.router.for_task("aux", mode)
                events = await extract_events(
                    text, aux, source=f"turn:{len(session.chat_history)}"
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

        self.store.save(session)
        return {
            "session_id": session.session_id,
            "text": text,
            "reference": ref.model_dump() if ref else None,
            "provider": provider.name,
            "ooc_hints": ooc_hints,
            "memory": memory_note,
            "tool_logs": [t.model_dump() for t in session.tool_logs[-5:]],
            "chars": len(text),
        }

    async def regenerate(self, session_id: str) -> dict[str, Any]:
        """丢掉最后一条回复，用同一条用户指令再生成一次。"""
        session = self.store.load(session_id)
        if session is None:
            raise KeyError(session_id)
        history = session.chat_history
        if history and history[-1].role == "user":
            instruction = history[-1].content
            plan = None
            session.chat_history = history[:-1]
        elif len(history) >= 2 and history[-1].role == "assistant" and history[-2].role == "user":
            instruction = history[-2].content
            plan = history[-1].meta.get("plan") if isinstance(history[-1].meta, dict) else None
            session.chat_history = history[:-2]
        else:
            raise ValueError("没有可重新生成的回复")
        keyword = None
        scenes: list[str] = []
        if isinstance(plan, dict):
            keyword = plan.get("keyword") or None
            scenes = list(plan.get("scene_keywords") or [])
        session.chat_history = history[:-2]
        self.store.save(session)
        from novel_agent.core.workflow import run_auto_turn

        result = await run_auto_turn(
            self,
            session_id,
            instruction,
            force_keyword=keyword,
            force_scene_keywords=scenes,
            use_reference=True,
        )
        result["workflow"] = "regenerate"
        return result

    def add_event(self, session_id: str, event: EventMemory) -> NovelSession:
        session = self.store.load(session_id)
        if session is None:
            raise KeyError(session_id)
        upsert_event(session, event)
        return self.store.save(session)

    def remove_event(self, session_id: str, event_id: str) -> NovelSession:
        session = self.store.load(session_id)
        if session is None:
            raise KeyError(session_id)
        delete_event(session, event_id)
        return self.store.save(session)

    def set_global_summary(self, session_id: str, summary: str) -> NovelSession:
        session = self.store.load(session_id)
        if session is None:
            raise KeyError(session_id)
        session.memory.global_summary = summary
        return self.store.save(session)


def _post_clean(text: str) -> str:
    t = (text or "").strip()
    for marker in ("Thinking Process:", "Thinking Process", "<think>", "</think>", "```", "写作说明", "【分析】", "【说明】"):
        at = t.find(marker)
        if at == 0:
            t = t[len(marker) :].lstrip()
        elif at > 0:
            t = t[:at].rstrip()
    if t.startswith("assistant"):
        t = t[len("assistant") :].lstrip(" :：\n")
    return t.strip()


def _ooc_hints(session: NovelSession, text: str) -> list[str]:
    hints: list[str] = []
    for c in session.character_list:
        if not c.active or not c.taboo.strip():
            continue
        parts = [p.strip() for p in re_split_taboo(c.taboo) if len(p.strip()) >= 2]
        for p in parts[:6]:
            if p in text and ("不" in c.taboo or "禁" in c.taboo or "绝不" in c.taboo):
                hints.append(f"角色「{c.name}」禁忌相关表述可能冲突：{p}")
                break
    return hints[:5]


def re_split_taboo(taboo: str) -> list[str]:
    import re

    return re.split(r"[,，、；;/\n]+", taboo)
