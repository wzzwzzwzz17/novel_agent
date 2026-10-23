from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from novel_agent.core.models import CharacterCard, EventMemory, SessionConfig


class CreateSessionRequest(BaseModel):
    session_name: str = "未命名小说"


class UpdateOutlineRequest(BaseModel):
    novel_outline: str


class UpdateConfigRequest(BaseModel):
    session_config: SessionConfig


class UpsertCharacterRequest(BaseModel):
    character: CharacterCard


class LlmConfigUpdate(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class InterruptRequest(BaseModel):
    instruction: str
    partial: str = ""


class WriteRequest(BaseModel):
    instruction: str
    use_reference_keyword: str | None = None
    scene_keywords: list[str] = Field(default_factory=list)
    auto: bool = True


class WriteResponse(BaseModel):
    session_id: str
    text: str
    reference: dict[str, Any] | None = None
    provider: str
    ooc_hints: list[str] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    tool_logs: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    workflow: str | None = None
    chars: int = 0
    retried_for_length: bool = False


class UpsertEventRequest(BaseModel):
    event: EventMemory


class GlobalSummaryRequest(BaseModel):
    global_summary: str


class NovelRefProbeRequest(BaseModel):
    keyword: str
    scene_keywords: list[str] = Field(default_factory=list)
    max_paragraphs: int = 3


class RenameSessionRequest(BaseModel):
    session_name: str
