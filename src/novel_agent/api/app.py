from __future__ import annotations

import asyncio
from pathlib import Path

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from novel_agent.api.schemas import (
    CreateSessionRequest,
    GlobalSummaryRequest,
    InterruptRequest,
    LlmConfigUpdate,
    NovelRefProbeRequest,
    RenameSessionRequest,
    UpdateConfigRequest,
    UpdateOutlineRequest,
    UpsertCharacterRequest,
    UpsertEventRequest,
    WriteRequest,
    WriteResponse,
)
from novel_agent.core.engine import NovelEngine
from novel_agent.core.library import delete_card, list_cards, upsert_card
from novel_agent.core.models import CharacterCard, ChatMessage
from novel_agent.core.speed_meter import sample_local
from novel_agent.plugins.loader import list_plugins, resolve_asset, set_enabled
from novel_agent.settings import get_settings, save_llm_file

settings = get_settings()
engine = NovelEngine(settings)

app = FastAPI(title="NovelAgent", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _key_hint(key: str) -> str:
    key = key.strip()
    if len(key) < 8:
        return ""
    return "…" + key[-4:]


@app.get("/api/llm-config")
def get_llm_config():
    return {
        "base_url": settings.llm_primary_base_url,
        "model": settings.llm_primary_model,
        "key_set": bool(settings.llm_primary_api_key.strip()),
        "key_hint": _key_hint(settings.llm_primary_api_key),
    }


@app.put("/api/llm-config")
def put_llm_config(body: LlmConfigUpdate):
    if body.base_url.strip():
        object.__setattr__(settings, "llm_primary_base_url", body.base_url.strip().rstrip("/"))
    if body.model.strip():
        object.__setattr__(settings, "llm_primary_model", body.model.strip())
    if body.api_key.strip():
        object.__setattr__(settings, "llm_primary_api_key", body.api_key.strip())
    save_llm_file(settings)
    engine.router.refresh()
    engine._wire_novelref()
    return get_llm_config()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "profile": settings.deploy_profile,
        "model_mode": settings.model_mode,
        "local_llm_enabled": settings.effective_local_enabled(),
        "novelref": settings.novelref_enabled,
        "novelref_llm_judge": settings.novelref_llm_judge,
        "home": str(settings.novelagent_home),
        "features": ["p1_characters", "p2_novelref", "p3_memory", "plugins"],
    }


@app.get("/api/sessions")
def list_sessions():
    return engine.store.list_sessions()


@app.post("/api/sessions")
def create_session(body: CreateSessionRequest):
    s = engine.store.create(body.session_name)
    return s.model_dump()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s.model_dump()


@app.patch("/api/sessions/{session_id}/name")
def rename_session(session_id: str, body: RenameSessionRequest):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s.session_name = body.session_name.strip() or s.session_name
    return engine.store.save(s).model_dump()


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if not engine.store.delete(session_id):
        raise HTTPException(404, "session not found")
    return {"ok": True}


@app.put("/api/sessions/{session_id}/outline")
def update_outline(session_id: str, body: UpdateOutlineRequest):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s.novel_outline = body.novel_outline
    return engine.store.save(s).model_dump()


@app.put("/api/sessions/{session_id}/config")
def update_config(session_id: str, body: UpdateConfigRequest):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s.session_config = body.session_config
    return engine.store.save(s).model_dump()


@app.post("/api/sessions/{session_id}/characters")
def upsert_character(session_id: str, body: UpsertCharacterRequest):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    char = body.character
    replaced = False
    for i, c in enumerate(s.character_list):
        if c.char_id == char.char_id:
            s.character_list[i] = char
            replaced = True
            break
    if not replaced:
        s.character_list.append(char)
    return engine.store.save(s).model_dump()


@app.post("/api/sessions/{session_id}/characters/{char_id}/optimize")
async def optimize_character(session_id: str, char_id: str):
    try:
        char = await engine.optimize_char(session_id, char_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(502, f"optimize failed: {e}") from e
    return char.model_dump()


@app.delete("/api/sessions/{session_id}/characters/{char_id}")
def delete_character(session_id: str, char_id: str):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s.character_list = [c for c in s.character_list if c.char_id != char_id]
    return engine.store.save(s).model_dump()


@app.post("/api/sessions/{session_id}/write", response_model=WriteResponse)
async def write(session_id: str, body: WriteRequest):
    try:
        result = await engine.write(
            session_id,
            body.instruction,
            use_reference_keyword=body.use_reference_keyword,
            scene_keywords=body.scene_keywords,
            auto=body.auto,
        )
    except KeyError:
        raise HTTPException(404, "session not found") from None
    except Exception as e:
        raise HTTPException(502, f"llm error: {e}") from e
    return result


@app.get("/api/local-speed")
async def local_speed():
    return await sample_local(settings)


@app.post("/api/sessions/{session_id}/write-stream")
async def write_stream(session_id: str, body: WriteRequest):
    from novel_agent.core.workflow import stream_auto_turn

    async def events():
        try:
            async for event in stream_auto_turn(
                engine,
                session_id,
                body.instruction,
                force_keyword=body.use_reference_keyword,
                force_scene_keywords=body.scene_keywords,
                use_reference=body.auto,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/sessions/{session_id}/interrupt")
def interrupt_turn(session_id: str, body: InterruptRequest):
    session = engine.store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    instruction = body.instruction.strip()
    partial = body.partial.strip()
    if session.chat_history and session.chat_history[-1].role == "user" and session.chat_history[-1].content == instruction:
        pass
    else:
        session.chat_history.append(ChatMessage(role="user", content=instruction))
    session.chat_history.append(
        ChatMessage(
            role="assistant",
            content=partial or "（生成已中断）",
            meta={"interrupted": True, "workflow": "paused"},
        )
    )
    engine.store.save(session)
    return {"ok": True, "chars": len(partial)}


@app.post("/api/sessions/{session_id}/regenerate", response_model=WriteResponse)
async def regenerate(session_id: str):
    try:
        return await engine.regenerate(session_id)
    except KeyError:
        raise HTTPException(404, "session not found") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(502, f"llm error: {e}") from e


@app.post("/api/sessions/{session_id}/auto-write", response_model=WriteResponse)
async def auto_write(session_id: str, body: WriteRequest):
    """显式全自动入口（与 write?auto=true 等价）。"""
    body.auto = True
    return await write(session_id, body)


@app.get("/api/sessions/{session_id}/memory")
def get_memory(session_id: str):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s.memory.model_dump()


@app.put("/api/sessions/{session_id}/memory/summary")
def put_summary(session_id: str, body: GlobalSummaryRequest):
    try:
        s = engine.set_global_summary(session_id, body.global_summary)
    except KeyError:
        raise HTTPException(404, "session not found") from None
    return s.memory.model_dump()


@app.post("/api/sessions/{session_id}/memory/events")
def upsert_event(session_id: str, body: UpsertEventRequest):
    try:
        s = engine.add_event(session_id, body.event)
    except KeyError:
        raise HTTPException(404, "session not found") from None
    return s.memory.model_dump()


@app.delete("/api/sessions/{session_id}/memory/events/{event_id}")
def delete_event(session_id: str, event_id: str):
    try:
        s = engine.remove_event(session_id, event_id)
    except KeyError:
        raise HTTPException(404, "session not found") from None
    return s.memory.model_dump()


@app.get("/api/sessions/{session_id}/tool-logs")
def tool_logs(session_id: str):
    s = engine.store.load(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return [t.model_dump() for t in s.tool_logs]


@app.get("/api/novelref/tags")
def novelref_tags():
    if not settings.novelref_enabled:
        return []
    from novel_ref import list_tags

    return list_tags()


@app.post("/api/novelref/probe")
async def novelref_probe(body: NovelRefProbeRequest):
    """手动探测参考抓取（双站+API审稿，不写入会话正文）。"""
    if not settings.novelref_enabled:
        raise HTTPException(400, "NovelRef disabled")
    _ = engine
    from novel_ref import get_reference

    result = await asyncio.to_thread(
        get_reference,
        body.keyword,
        body.scene_keywords,
        body.max_paragraphs,
        require_both_sites=True,
    )
    return result.model_dump()


class PluginToggle(BaseModel):
    enabled: bool


@app.get("/api/plugins")
def plugins():
    return list_plugins(settings)


@app.post("/api/plugins/{plugin_id}/enabled")
def toggle_plugin(plugin_id: str, body: PluginToggle):
    try:
        return set_enabled(settings, plugin_id, body.enabled)
    except KeyError:
        raise HTTPException(404, "plugin not found") from None


@app.get("/api/plugins/{plugin_id}/assets/{filename}")
def plugin_asset(plugin_id: str, filename: str):
    path = resolve_asset(settings, plugin_id, filename)
    if not path:
        raise HTTPException(404, "asset not found")
    return FileResponse(path)


@app.get("/api/library/characters")
def library_characters():
    return [c.model_dump() for c in list_cards(settings)]


@app.post("/api/library/characters")
def library_save(body: UpsertCharacterRequest):
    cards = upsert_card(settings, body.character)
    return [c.model_dump() for c in cards]


@app.delete("/api/library/characters/{char_id}")
def library_delete(char_id: str):
    return [c.model_dump() for c in delete_card(settings, char_id)]


@app.post("/api/sessions/{session_id}/characters/import/{char_id}")
def import_library_character(session_id: str, char_id: str):
    session = engine.store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    source = next((c for c in list_cards(settings) if c.char_id == char_id), None)
    if source is None:
        raise HTTPException(404, "library card not found")
    copied = CharacterCard.model_validate(source.model_dump())
    replaced = False
    for i, old in enumerate(session.character_list):
        if old.char_id == copied.char_id:
            session.character_list[i] = copied
            replaced = True
            break
    if not replaced:
        session.character_list.append(copied)
    return engine.store.save(session).model_dump()


web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

    @app.get("/")
    def index():
        index_file = web_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return {"message": "NovelAgent API", "docs": "/docs"}
