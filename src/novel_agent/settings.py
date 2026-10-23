from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_home() -> Path:
    env = os.environ.get("NOVELAGENT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # portable: relative to CWD, never hardcode user machine paths
    return (Path.cwd() / ".novelagent").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    novelagent_home: Path = Field(default_factory=_default_home)
    deploy_profile: Literal["local", "cloud"] = "cloud"
    local_llm_enabled: bool = False

    llm_primary_base_url: str = "https://api.deepseek.com/v1"
    llm_primary_api_key: str = ""
    llm_primary_model: str = "deepseek-chat"

    llm_aux_base_url: str = ""
    llm_aux_api_key: str = ""
    llm_aux_model: str = ""

    model_mode: Literal["api_only", "api_first", "local_first", "local_only"] = "api_only"

    novelref_enabled: bool = True
    novelref_llm_judge: bool = True
    novelref_proxy: str = "auto"
    novelref_trust_env: bool = True

    local_llm_base_url: str = "http://127.0.0.1:8001/v1"
    local_llm_model: str = ""

    host: str = "0.0.0.0"
    port: int = 8787

    @property
    def data_dir(self) -> Path:
        p = self.novelagent_home / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sessions_dir(self) -> Path:
        p = self.data_dir / "sessions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def config_path(self) -> Path:
        return self.novelagent_home / "config.json"

    def effective_local_enabled(self) -> bool:
        if self.deploy_profile == "cloud":
            return False
        return bool(self.local_llm_enabled)

    def aux_base_url(self) -> str:
        return self.llm_aux_base_url or self.llm_primary_base_url

    def aux_api_key(self) -> str:
        return self.llm_aux_api_key or self.llm_primary_api_key

    def aux_model(self) -> str:
        return self.llm_aux_model or self.llm_primary_model

    @property
    def llm_file(self) -> Path:
        return self.novelagent_home / "llm.json"


def apply_llm_file(settings: Settings) -> None:
    path = settings.llm_file
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for field in ("llm_primary_base_url", "llm_primary_api_key", "llm_primary_model"):
        value = str((data or {}).get(field) or "").strip()
        if value:
            object.__setattr__(settings, field, value)


def save_llm_file(settings: Settings) -> None:
    settings.novelagent_home.mkdir(parents=True, exist_ok=True)
    payload = {
        "llm_primary_base_url": settings.llm_primary_base_url,
        "llm_primary_api_key": settings.llm_primary_api_key,
        "llm_primary_model": settings.llm_primary_model,
    }
    settings.llm_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # cloud hard-disable local path
    if s.deploy_profile == "cloud":
        object.__setattr__(s, "local_llm_enabled", False)
        if s.model_mode in ("local_first", "local_only"):
            object.__setattr__(s, "model_mode", "api_only")
    s.novelagent_home.mkdir(parents=True, exist_ok=True)
    apply_llm_file(s)
    return s
