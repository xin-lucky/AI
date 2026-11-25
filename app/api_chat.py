from __future__ import annotations

import asyncio
import base64
import json
import re

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from . import db, memory, parser
from .config import UPLOAD_DIR, load_settings
from .llm import Ollama, OllamaError

router = APIRouter()

DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", re.I | re.S)
# 文档 DataURL：data:<mime>;base64,<payload>
DOC_MIME_RE = re.compile(r"^data:([a-zA-Z0-9_+.-]+/[a-zA-Z0-9_+.-]+);base64,(.+)$", re.I | re.S)
# MIME → 后缀映射（openpyxl 不认 .xls / .doc，跳过）
MIME_TO_EXT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": None,  # .xls 需要 xlrd，暂时跳过
    "application/msword": None,          # .doc 旧版，跳过
    "text/plain": "txt",
    "text/csv": "csv",
    "text/markdown": "md",
    "text/x-markdown": "md",
}
_background: set[asyncio.Task] = set()


def sse(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def save_images(images: list, prefix: str) -> list[str]:
    urls: list[str] = []
    for index, raw in enumerate((images or [])[:4]):
        if not isinstance(raw, str) or not raw.strip():
            continue
        match = DATA_URL_RE.match(raw.strip())
        if match:
            ext = "jpg" if match.group(1).lower() in ("jpeg", "jpg") else match.group(1).lower()
            payload = match.group(2)
        else:
            ext, payload = "png", raw.strip()
        try:
            data = base64.b64decode(payload, validate=True)
        except Exception:
            continue
        if not data or len(data) > 8 * 1024 * 1024:
            continue
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}_{index}.{ext}"
        (UPLOAD_DIR / name).write_bytes(data)
        urls.append(f"/uploads/{name}")
    return urls


def save_documents(docs: list, prefix: str) -> list[str]:
    """保存上传的文档（base64 DataURL 列表）到 uploads/，返回保存后的绝对路径列表。"""
    paths: list[str] = []
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for index, raw in enumerate((docs or [])[:5]):  # 最多 5 个文档
        if not isinstance(raw, str) or not raw.strip():
            continue
        match = DOC_MIME_RE.match(raw.strip())
        if not match:
            continue
        mime = match.group(1).lower()
        payload = match.group(2)
        ext = MIME_TO_EXT.get(mime)
        if ext is None:
            continue
        try:
            data = base64.b64decode(payload, validate=True)
        except Exception:
            continue
        if not data or len(data) > 50 * 1024 * 1024:  # 50MB 上限
            continue
        name = f"{prefix}_doc{index}.{ext}"
        save_path = UPLOAD_DIR / name
        save_path.write_bytes(data)
        paths.append(str(save_path))
    return paths


def extract_docs(paths: list[str]) -> list[dict]:
    """遍历文档路径，抽文本。返回 parser.parse_document() 结果列表。"""
    results: list[dict] = []
    for path in paths:
        info = parser.parse_document(path)
        results.append(info)
    return results


def image_b64(url: str) -> str | None:
    path = UPLOAD_DIR / url.rsplit("/", 1)[-1]
    try:
        if path.exists():
            return base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None
    return None


def build_history(cid: str, budget: int) -> list[dict]:
    rows = db.list_messages(cid)
    selected: list[dict] = []
    total = 0
    for msg in reversed(rows):
        size = len(msg.get("content") or "")
        if selected and total + size > budget:
            break
        selected.append(msg)
        total += size
        if len(selected) >= 24:
            break
    selected.reverse()
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected


def to_ollama_messages(selected: list[dict]) -> list[dict]:
    result: list[dict] = []
    last_index = len(selected) - 1
    for index, msg in enumerate(selected):
        item = {"role": msg["role"], "content": msg.get("content") or ""}
        if msg["role"] == "user" and index == last_index and msg.get("images"):
            encoded = [b for b in (image_b64(u) for u in msg["images"]) if b]
            if encoded:
                item["images"] = encoded
                if not item["content"].strip():
                    item["content"] = "请看这张图片。"
        result.append(item)
    return result


def is_repeat_question(cid: str, content: str, image_count: int) -> bool:
    """当前这条用户消息是否与此前某条完全相同（文本 + 图片数量）。"""
    rows = db.list_messages(cid)
    norm = re.sub(r"\s+", "", content or "")
    skipped_current = False
    for msg in reversed(rows):
        if msg["role"] != "user":
            continue
        if not skipped_current:
            skipped_current = True  # 跳过刚写入的当前消息
            continue
        imgs = msg.get("images") or []
        if isinstance(imgs, str):
            try:
                imgs = json.loads(imgs)
            except Exception:
                imgs = []
        if re.sub(r"\s+", "", msg.get("content") or "") == norm and len(imgs) == image_count:
            return True
    return False


@router.post("/api/chat")
async def chat(payload: dict):
    content = str(payload.get("content") or "").strip()
    image_data = payload.get("images") or []
    doc_data = payload.get("documents") or []
    if not content and not image_data and not doc_data:
        raise HTTPException(400, "消息不能为空")

    settings = load_settings()
    llm = Ollama(settings["ollama_url"])

    cid = payload.get("conversation_id")
    convo = db.get_conversation(cid) if cid else None
    if not convo:
        title = re.sub(r"\s+", " ", content).strip()[:20] or "图片对话"
        convo = db.create_conversation(title, settings["chat_model"])
    cid = convo["id"]

    saved_images = save_images(image_data, db.new_id())
    saved_doc_paths = save_documents(doc_data, db.new_id())
    parsed_docs: list[dict] = []
    if saved_doc_paths:
        parsed_docs = extract_docs(saved_doc_paths)
    # 清理文档临时文件（抽到文本后就没用了）
    for p_str in saved_doc_paths:
        try:
            pathlib.Path(p_str).unlink(missing_ok=True)
        except Exception:
            pass
    # 只保留成功抽到文本的文档
    parsed_docs = [d for d in parsed_docs if d.get("text") and not d.get("error")]
    user_msg = db.add_message(cid, "user", content, images=saved_images)

    use_vision = bool(saved_images) and bool(settings.get("vision_model"))
    model = settings["vision_model"] if use_vision else settings["chat_model"]
    if payload.get("model") and isinstance(payload["model"], str):
        model = payload["model"]
    think = bool(payload.get("think", settings.get("thinking")))

    query = content or "请描述这张图片的内容"
    if settings.get("memory_enabled", True):
        related, core = await memory.retrieve(llm, settings, query)
    else:
        related, core = [], []
    system_prompt = memory.build_system_prompt(settings, related, core, docs=parsed_docs if parsed_docs else None)
    repeat = is_repeat_question(cid, content, len(saved_images))
    if repeat:
        system_prompt += (
            "\n【重复提问】对方又在问同一个问题。事实和结论必须和上次一致，"
            "但绝对不要复用上一次的措辞、句式和开场白：换一个角度、换一种说法重新表达，"
            "能举例就换个例子，能分点就换种结构。"
        )

    history = build_history(cid, int(settings.get("history_char_budget") or 9000))
    if repeat:
        # 把此前完全相同的问答对从上下文里剔除，避免模型照抄上一轮的句子
        norm = re.sub(r"\s+", "", content or "")
        kept: list[dict] = []
        drop_reply = False
        for msg in history[:-1]:
            if drop_reply and msg["role"] == "assistant":
                drop_reply = False
                continue
            if msg["role"] == "user" and re.sub(r"\s+", "", msg.get("content") or "") == norm:
                drop_reply = True
                continue
            kept.append(msg)
        if history:
            kept.append(history[-1])
        history = kept
    messages = [{"role": "system", "content": system_prompt}] + to_ollama_messages(history)

    async def event_stream():
        collected: list[str] = []
        thinking_parts: list[str] = []
        stats: dict = {}
        saved = False
        try:
            yield sse(
                {
                    "type": "meta",
                    "conversation_id": cid,
                    "title": convo["title"],
                    "user_message": user_msg,
                    "model": model,
                    "memories": [
                        {"id": m["id"], "content": m["content"], "kind": m["kind"]}
                        for m in (related + core)[:8]
                    ],
                    "documents": [
                        {"filename": d["filename"], "truncated": d.get("truncated", False), "chars": len(d.get("text", ""))}
                        for d in parsed_docs
                    ],
                }
            )

            async for event in llm.chat_stream(
                model,
                messages,
                temperature=float(settings.get("temperature") or 0.85),
                num_ctx=int(settings.get("num_ctx") or 8192),
                think=think,
            ):
                if event["type"] == "done":
                    stats = event.get("stats") or {}
                    continue
                if event["type"] == "thinking":
                    thinking_parts.append(event["text"])
                elif event["type"] == "token":
                    collected.append(event["text"])
                yield sse(event)

            text = "".join(collected).strip()
            if text:
                assistant = db.add_message(
                    cid,
                    "assistant",
                    text,
                    model=model,
                    meta={
                        "stats": stats,
                        "memory_ids": [m["id"] for m in related],
                        "core_ids": [m["id"] for m in core],
                    },
                )
                saved = True
                yield sse({"type": "done", "message": assistant})
                if settings.get("memory_enabled", True):
                    spawn(
                        memory.extract_and_store(
                            Ollama(load_settings()["ollama_url"]),
                            load_settings(),
                            content,
                            text,
                        )
                    )
            else:
                yield sse({"type": "error", "message": "模型没有返回内容，请重试或换一个模型。"})

        except asyncio.CancelledError:
            raise
        except httpx.ConnectError:
            yield sse(
                {
                    "type": "error",
                    "message": "连不上本地模型服务。请确认 Ollama 已启动（运行 start.bat 即可）。",
                }
            )
        except OllamaError as exc:
            message = exc.message
            if "not found" in message.lower():
                message = f"模型 {model} 不存在。请到「模型」页面拉取，或换一个已安装的模型。"
            yield sse({"type": "error", "message": message})
        except Exception as exc:  # 兜底，避免流中断无提示
            yield sse({"type": "error", "message": f"出错了：{exc}"})
        finally:
            if not saved and collected:
                db.add_message(
                    cid,
                    "assistant",
                    "".join(collected).strip(),
                    model=model,
                    meta={"incomplete": True, "stats": stats},
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
