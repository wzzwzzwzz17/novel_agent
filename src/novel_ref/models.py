from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReferenceParagraph(BaseModel):
    text: str
    source_novel: str = ""
    source_chapter: str = ""
    source_site: str = ""
    score: float = 0.0


class ReferenceResult(BaseModel):
    status: Literal["ok", "empty", "error"]
    paragraphs: list[ReferenceParagraph] = Field(default_factory=list)
    reason: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    note: str = ""
