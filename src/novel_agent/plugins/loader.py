"""插件加载：借鉴 SillyTavern manifest.json 扩展，以及 Dsh 式可插拔面板。

扫描两处目录：
- 仓库 plugins/（内置）
- NOVELAGENT_HOME/plugins/（用户后装）
启用状态写在 NOVELAGENT_HOME/plugins_state.json，不改插件源文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_agent.core.models import NovelSession
from novel_agent.settings import Settings


def builtin_root() -> Path:
    return Path(__file__).resolve().parents[3] / "plugins"


def user_root(settings: Settings) -> Path:
    path = settings.novelagent_home / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(settings: Settings) -> Path:
    return settings.novelagent_home / "plugins_state.json"


def _read_state(settings: Settings) -> dict[str, bool]:
    path = _state_path(settings)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(k): bool(v) for k, v in (data or {}).items()}


def _write_state(settings: Settings, state: dict[str, bool]) -> None:
    _state_path(settings).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _scan_dir(root: Path, origin: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not root.is_dir():
        return found
    for folder in sorted(root.iterdir()):
        manifest_path = folder / "manifest.json"
        if not folder.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        plugin_id = str(manifest.get("id") or folder.name)
        prompt = ""
        prompt_file = manifest.get("prompt_file")
        if prompt_file:
            pf = folder / str(prompt_file)
            if pf.is_file():
                prompt = pf.read_text(encoding="utf-8").strip()
        found.append(
            {
                "id": plugin_id,
                "display_name": manifest.get("display_name") or plugin_id,
                "version": manifest.get("version") or "0.0.0",
                "author": manifest.get("author") or "",
                "description": manifest.get("description") or "",
                "loading_order": int(manifest.get("loading_order") or 0),
                "default_enabled": bool(manifest.get("enabled", True)),
                "js": manifest.get("js") or "",
                "css": manifest.get("css") or "",
                "origin": origin,
                "folder": str(folder),
                "prompt": prompt,
            }
        )
    return found


def list_plugins(settings: Settings) -> list[dict[str, Any]]:
    state = _read_state(settings)
    items = _scan_dir(builtin_root(), "builtin") + _scan_dir(user_root(settings), "user")
    # 同 id 时用户目录覆盖内置
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        merged[item["id"]] = item
    out = []
    for item in merged.values():
        enabled = state.get(item["id"], item["default_enabled"])
        public = {k: v for k, v in item.items() if k != "prompt"}
        public["enabled"] = enabled
        public["has_prompt"] = bool(item.get("prompt"))
        out.append(public)
    out.sort(key=lambda x: (x["loading_order"], x["id"]))
    return out


def set_enabled(settings: Settings, plugin_id: str, enabled: bool) -> dict[str, Any]:
    known = {p["id"]: p for p in list_plugins(settings)}
    if plugin_id not in known:
        raise KeyError(plugin_id)
    state = _read_state(settings)
    state[plugin_id] = bool(enabled)
    _write_state(settings, state)
    known[plugin_id]["enabled"] = bool(enabled)
    return known[plugin_id]


def _all_with_prompt(settings: Settings) -> list[dict[str, Any]]:
    state = _read_state(settings)
    items = _scan_dir(builtin_root(), "builtin") + _scan_dir(user_root(settings), "user")
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        merged[item["id"]] = item
    enabled = []
    for item in merged.values():
        if state.get(item["id"], item["default_enabled"]) and item.get("prompt"):
            enabled.append(item)
    enabled.sort(key=lambda x: (x["loading_order"], x["id"]))
    return enabled


def prompt_injections(settings: Settings, session: NovelSession, instruction: str) -> list[str]:
    """把已启用插件的提示词注入系统段。支持少量占位符。"""
    turns = len([m for m in session.chat_history if m.role == "assistant"])
    if turns < 3:
        phase = "开端：建立人物、场景与核心矛盾"
    elif turns < 8:
        phase = "发展：升级冲突，让人物选择付出代价"
    else:
        phase = "转折与收束：回收伏笔，但不要提前写完结局"
    names = "、".join(c.name for c in session.character_list if c.active) or "（暂无活跃角色）"
    blocks = []
    for plugin in _all_with_prompt(settings):
        text = plugin["prompt"]
        text = (
            text.replace("{{instruction}}", instruction)
            .replace("{{phase}}", phase)
            .replace("{{characters}}", names)
            .replace("{{outline}}", session.novel_outline[:800])
        )
        blocks.append(f"【插件·{plugin['display_name']}】\n{text}")
    return blocks


def resolve_asset(settings: Settings, plugin_id: str, filename: str) -> Path | None:
    """只允许插件目录内的 js/css，防止路径穿越。"""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    for origin_root in (user_root(settings), builtin_root()):
        for folder in origin_root.glob("*"):
            manifest_path = folder / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if str(manifest.get("id") or folder.name) != plugin_id:
                continue
            target = (folder / filename).resolve()
            if folder.resolve() not in target.parents and target != folder.resolve():
                return None
            if target.is_file() and target.suffix in {".js", ".css"}:
                return target
    return None
