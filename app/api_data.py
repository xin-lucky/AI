from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from . import db, memory
from .config import DEFAULT_PERSONA, UPLOAD_DIR, load_settings, save_settings
from .llm import Ollama

router = APIRouter()


# ---------------------------------------------------------------- 对话

@router.get("/api/conversations")
async def conversations():
    return db.list_conversations()


@router.post("/api/conversations")
async def new_conversation(payload: dict):
    cid = db.create_conversation(title=str(payload.get("title") or "新对话"))["id"]
    return {"ok": True, "id": cid}


@router.patch("/api/conversations/{cid}")
async def patch_conversation(cid: str, payload: dict):
    title = payload.get("title")
    if title:
        db.update_conversation(cid, title=str(title))
    return {"ok": True}


@router.delete("/api/conversations")
async def del_all_conversations():
    count = db.delete_all_conversations()
    # 对话全删后，上传的聊天图片/文档也没用了，一并清掉释放磁盘
    removed = 0
    try:
        if UPLOAD_DIR.exists():
            for f in UPLOAD_DIR.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)
                    removed += 1
    except OSError:
        pass
    return {"ok": True, "deleted": count, "files": removed}


@router.delete("/api/conversations/{cid}")
async def del_conversation(cid: str):
    db.delete_conversation(cid)
    return {"ok": True}


@router.post("/api/conversations/{cid}/clear")
async def clear_conversation(cid: str):
    with db.conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
    return {"ok": True}


@router.get("/api/conversations/{cid}/messages")
async def conversation_messages(cid: str):
    cdata = db.get_conversation(cid)
    if not cdata:
        raise HTTPException(404, "对话不存在")
    return {"conversation": cdata, "messages": db.list_messages(cid)}


# ---------------------------------------------------------------- 记忆

@router.get("/api/memories")
async def memories(q: str | None = None):
    items = db.list_memories(q.strip() if q else None)
    for item in items:
        item.pop("embedding", None)
    return {"items": items}


@router.post("/api/memories")
async def create_memory(payload: dict):
    content = str(payload.get("content") or "").strip()
    if len(content) < 2:
        raise HTTPException(400, "记忆内容太短")
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])
    record = await memory.remember(
        llm,
        settings,
        content,
        kind=str(payload.get("kind") or "fact"),
        importance=int(payload.get("importance") or 3),
        source="手动",
    )
    record.pop("embedding", None)
    return {"ok": True, "memory": record}


@router.patch("/api/memories/{mid}")
async def patch_memory(mid: str, payload: dict):
    existing = [m for m in db.list_memories() if m["id"] == mid]
    if not existing:
        raise HTTPException(404, "记忆不存在")
    content = payload.get("content")
    kind = payload.get("kind")
    importance = payload.get("importance")
    db.update_memory(
        mid,
        content=str(content) if isinstance(content, str) else None,
        kind=str(kind) if isinstance(kind, str) else None,
        importance=int(importance) if isinstance(importance, (int, float)) else None,
    )
    return {"ok": True}


@router.delete("/api/memories/{mid}")
async def remove_memory(mid: str):
    db.delete_memory(mid)
    return {"ok": True}


@router.post("/api/memories/consolidate")
async def consolidate_memories():
    return {"ok": True, **memory.consolidate()}


# ---------------------------------------------------------------- 人格

@router.get("/api/persona")
async def get_persona():
    settings = load_settings()
    return {"persona": settings["persona"], "defaults": DEFAULT_PERSONA}


@router.put("/api/persona")
async def put_persona(payload: dict):
    persona = payload.get("persona") if isinstance(payload.get("persona"), dict) else payload
    settings = save_settings({"persona": persona})
    return {"ok": True, "persona": settings["persona"]}


# ---------------------------------------------------------------- 设置

@router.get("/api/settings")
async def get_settings():
    return load_settings()


@router.put("/api/settings")
async def put_settings(payload: dict):
    return {"ok": True, "settings": save_settings(payload or {})}


# ---------------------------------------------------------------- 统计 / 导出

@router.get("/api/stats")
async def get_stats():
    return db.stats()


@router.get("/api/export")
async def export_data():
    data = db.export_all()
    data["settings"] = load_settings()
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="xin-export.json"'},
    )
