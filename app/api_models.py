from __future__ import annotations

import json

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .config import load_settings, save_settings
from .llm import Ollama, OllamaError

router = APIRouter()


@router.get("/api/health")
async def health():
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])
    info = {"ollama": False, "version": "", "models": [], "chat_ok": False}
    try:
        version = await llm.version()
        models = await llm.list_models()
        names = {m["name"] for m in models}
        info.update(
            {
                "ollama": True,
                "version": version,
                "models": models,
                "chat_ok": settings["chat_model"] in names,
                "vision_ok": bool(settings["vision_model"]) and settings["vision_model"] in names,
            }
        )
    except Exception:
        pass
    return info


@router.get("/api/models")
async def models():
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])
    try:
        items = await llm.list_models()
    except httpx.HTTPError:
        return {"items": [], "error": "连不上 Ollama，请先启动本地模型服务。", "settings": settings}
    except Exception as exc:
        return {"items": [], "error": str(exc), "settings": settings}
    embedding_candidates = [
        m["name"] for m in items if "embedding" in (m.get("caps") or []) or "embed" in m["name"].lower()
    ]
    return {"items": items, "settings": settings, "embed_candidates": embedding_candidates}


@router.post("/api/models/pull")
async def pull_model(payload: dict):
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "模型名不能为空"}
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])

    async def stream():
        try:
            async for chunk in llm.pull(name):
                event = {
                    "type": "progress",
                    "status": chunk.get("status", ""),
                    "completed": chunk.get("completed", 0),
                    "total": chunk.get("total", 0),
                }
                if event["total"]:
                    event["percent"] = round(event["completed"] / event["total"] * 100, 1)
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"type": "done", "name": name}, ensure_ascii=False) + "\n\n"
        except OllamaError as exc:
            yield "data: " + json.dumps({"type": "error", "message": exc.message}, ensure_ascii=False) + "\n\n"
        except httpx.ConnectError:
            yield "data: " + json.dumps(
                {"type": "error", "message": "连不上 Ollama，无法拉取模型。"}, ensure_ascii=False
            ) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/models/select")
async def select_model(payload: dict):
    role = str(payload.get("role") or "")
    name = str(payload.get("name") or "").strip()
    mapping = {"chat": "chat_model", "vision": "vision_model", "embed": "embed_model"}
    if role not in mapping:
        return {"ok": False, "error": "未知的模型用途"}
    settings = save_settings({mapping[role]: name})
    return {"ok": True, "settings": settings}


@router.post("/api/models/remove")
async def remove_model(payload: dict):
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "模型名不能为空"}
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])
    try:
        await llm.delete(name)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}



