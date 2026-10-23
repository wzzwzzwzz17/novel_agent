from __future__ import annotations

import json
from pathlib import Path

from novel_agent.core.models import NovelSession, _now
from novel_agent.settings import Settings


class SessionStore:
    """One JSON file per session — portable, backup-friendly."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.sessions_dir

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def list_sessions(self) -> list[dict]:
        items = []
        for p in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                items.append(
                    {
                        "session_id": data.get("session_id", p.stem),
                        "session_name": data.get("session_name", p.stem),
                        "updated_at": data.get("updated_at", ""),
                    }
                )
            except Exception:
                continue
        return items

    def load(self, session_id: str) -> NovelSession | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        return NovelSession.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, session: NovelSession) -> NovelSession:
        session.updated_at = _now()
        path = self._path(session.session_id)
        path.write_text(
            session.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return session

    def create(self, name: str = "未命名小说") -> NovelSession:
        session = NovelSession(session_name=name)
        return self.save(session)

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def export_bytes(self, session_id: str) -> bytes | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        return path.read_bytes()

    def import_json(self, raw: str | bytes) -> NovelSession:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        session = NovelSession.model_validate_json(raw)
        return self.save(session)
