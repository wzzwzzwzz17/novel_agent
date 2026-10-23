from __future__ import annotations

from typing import Literal

from novel_agent.providers.openai_compatible import OpenAICompatibleProvider
from novel_agent.settings import Settings

TaskKind = Literal["write", "aux"]


class ModelRouter:
    """Selects providers. Cloud profile never constructs a weight-loading backend."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.primary = OpenAICompatibleProvider(
            base_url=settings.llm_primary_base_url,
            api_key=settings.llm_primary_api_key,
            model=settings.llm_primary_model,
            name="primary",
        )
        self.aux = OpenAICompatibleProvider(
            base_url=settings.aux_base_url(),
            api_key=settings.aux_api_key(),
            model=settings.aux_model(),
            name="aux",
        )

    def refresh(self) -> None:
        settings = self.settings
        self.primary = OpenAICompatibleProvider(
            base_url=settings.llm_primary_base_url,
            api_key=settings.llm_primary_api_key,
            model=settings.llm_primary_model,
            name="primary",
        )
        self.aux = OpenAICompatibleProvider(
            base_url=settings.aux_base_url(),
            api_key=settings.aux_api_key(),
            model=settings.aux_model(),
            name="aux",
        )

    def for_task(self, kind: TaskKind, mode: str | None = None) -> OpenAICompatibleProvider:
        mode = (mode or self.settings.model_mode).strip() or self.settings.model_mode
        # cloud: always API
        if self.settings.deploy_profile == "cloud":
            return self.primary if kind == "write" else self.aux
        if mode == "api_only":
            return self.primary if kind == "write" else self.aux
        if mode == "local_only":
            return self.primary
        if mode == "local_first":
            return self.primary if kind == "write" else self.aux
        if mode == "api_first":
            return self.aux if kind == "write" else self.primary
        return self.primary
