from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class CharacterCard(BaseModel):
    char_id: str = Field(default_factory=new_id)
    name: str
    raw_input: str = ""
    optimized: str = ""
    appearance: str = ""
    personality: str = ""
    background: str = ""
    speech_style: str = ""
    taboo: str = ""
    goal: str = ""
    relation: str = ""
    active: bool = True


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str = Field(default_factory=_now)
    meta: dict[str, Any] = Field(default_factory=dict)


class EventMemory(BaseModel):
    event_id: str = Field(default_factory=new_id)
    content: str
    weight: Literal["high", "medium", "low"] = "medium"
    source: str = ""
    created_at: str = Field(default_factory=_now)


class MemoryBank(BaseModel):
    global_summary: str = ""
    event_memory: list[EventMemory] = Field(default_factory=list)
    compressed_digests: list[str] = Field(default_factory=list)


class SessionConfig(BaseModel):
    temperature: float = 0.85
    top_p: float = 0.9
    max_tokens: int = 6000
    model_mode: str = ""
    enable_novelref: bool = True
    enable_auto_memory: bool = True
    enable_ooc_hint: bool = True
    recent_turns: int = 12
    min_reply_chars: int = 800


class ToolLogEntry(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    reason: str = ""
    confidence: str = ""
    preview: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class NovelSession(BaseModel):
    session_id: str = Field(default_factory=new_id)
    session_name: str = "未命名小说"
    novel_outline: str = ""
    character_list: list[CharacterCard] = Field(default_factory=list)
    chat_history: list[ChatMessage] = Field(default_factory=list)
    memory: MemoryBank = Field(default_factory=MemoryBank)
    session_config: SessionConfig = Field(default_factory=SessionConfig)
    tool_logs: list[ToolLogEntry] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)
