"""反馈 / 消息操作 API — 点赞、点踩、重生成、反馈中心。"""

from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from . import db, memory
from .config import load_settings
from .llm import Ollama, OllamaError

router = APIRouter()


# ---------------------------------------------------------------- 点赞点踩

@router.post("/api/messages/{mid}/feedback")
async def message_feedback(mid: str, payload: dict):
    """对一条 AI 消息点赞或点踩。

    payload:
      vote: 'up' | 'down'
      reasons: [str]     点踩时的原因（可选）
    """
    msg = db.get_message(mid) if hasattr(db, "get_message") else None
    # db.py 没有 get_message，自己查
    msg = _get_message(mid)
    if not msg:
        raise HTTPException(404, "消息不存在")
    if msg["role"] != "assistant":
        raise HTTPException(400, "只能对 AI 回复反馈")

    vote = payload.get("vote")
    if vote not in ("up", "down"):
        raise HTTPException(400, "vote 必须是 'up' 或 'down'")

    # 找同对话的上一条（用户问题）
    prev = _get_prev_user_message(msg["conversation_id"], mid)

    feedback = db.add_feedback(
        message_id=mid,
        conversation_id=msg["conversation_id"],
        role="assistant",
        user_question=prev["content"] if prev else "(未知)",
        ai_reply=msg["content"],
        vote=vote,
        model=msg.get("model"),
        reasons=payload.get("reasons") or [],
    )
    return {"ok": True, "feedback": feedback}


def _get_message(mid: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["images"] = json.loads(item.get("images") or "[]")
        item["meta"] = json.loads(item.get("meta") or "{}")
        return item


def _get_prev_user_message(cid: str, mid: str) -> dict | None:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND rowid < (SELECT rowid FROM messages WHERE id = ?) AND role = 'user' ORDER BY rowid DESC LIMIT 1",
            (cid, mid),
        ).fetchall()
    return dict(rows[0]) if rows else None


# ---------------------------------------------------------------- 重生成（SSE）

@router.post("/api/messages/{mid}/regenerate")
async def regenerate(mid: str):
    """让模型重新回答上一个问题。流返回，同 /api/chat。"""
    msg = _get_message(mid)
    if not msg:
        raise HTTPException(404, "消息不存在")
    prev = _get_prev_user_message(msg["conversation_id"], mid)
    if not prev:
        raise HTTPException(400, "找不到对应的用户问题")

    # 把原回复标记为已替换
    db.conn()  # ensure conn context available
    with db.conn() as c:
        c.execute("UPDATE messages SET meta = json_set(COALESCE(meta,'{}'), '$.regenerated', 1) WHERE id = ?", (mid,))

    # 用 /api/chat 的同样逻辑重新生成一条新的 assistant 消息
    settings = load_settings()
    llm = Ollama(settings["ollama_url"])

    # 从该用户问题开始构建历史（不包含旧的 assistant 回复）
    history = [prev] + db.list_messages(msg["conversation_id"])
    # 过滤掉旧的 assistant 回复和之后的消息
    filtered: list[dict] = []
    skip = False
    for m in history:
        if m["id"] == mid:
            skip = True
            continue
        if skip and m["role"] == "user":
            skip = False
        if not skip:
            filtered.append(m)
    # 去重
    seen = set()
    dedup = []
    for m in filtered:
        if m["id"] not in seen:
            seen.add(m["id"])
            dedup.append(m)

    user_q = prev["content"]
    # 如果有点踩原因，拼进 query 让模型知道哪里不好
    down = db.list_feedbacks(vote="down", limit=500)
    related_feedback = [f for f in down if f.get("message_id") == mid or any(r in user_q for r in (f.get("reasons") or []))]
    extra_hint = ""
    if related_feedback:
        reasons = set()
        for f in related_feedback:
            reasons.update(f.get("reasons") or [])
        if reasons:
            extra_hint = f"\n\n【上一次回复的用户反馈】：{', '.join(reasons)}。请根据这些反馈重新组织答案。"

    query = user_q + extra_hint

    if settings.get("memory_enabled", True):
        related, core = await memory.retrieve(llm, settings, user_q)
    else:
        related, core = [], []

    system_prompt = memory.build_system_prompt(settings, related, core)

    # 新的 assistant 消息
    new_msg = db.add_message(msg["conversation_id"], "assistant", "", model=settings["chat_model"])

    messages = [{"role": "system", "content": system_prompt}] + [
        {"role": m["role"], "content": m.get("content") or ""} for m in dedup
    ]
    # 最后一条必须是用户问题
    if not messages or messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": user_q})

    async def event_stream() -> AsyncGenerator[str, None]:
        collected: list[str] = []
        thinking_parts: list[str] = []
        stats: dict = {}
        try:
            yield _sse({"type": "meta", "message_id": new_msg["id"], "conversation_id": msg["conversation_id"]})
            async for event in llm.chat_stream(
                settings["chat_model"],
                messages,
                temperature=float(settings.get("temperature") or 0.85),
                num_ctx=int(settings.get("num_ctx") or 8192),
            ):
                if event["type"] == "done":
                    stats = event.get("stats") or {}
                    continue
                if event["type"] == "thinking":
                    thinking_parts.append(event["text"])
                elif event["type"] == "token":
                    collected.append(event["text"])
                yield _sse(event)
            text = "".join(collected).strip()
            if text:
                db.update_message_content(new_msg["id"], text, meta={"stats": stats, "regenerated_from": mid})
                # 更新 feedback 的 revised_reply
                _update_feedback_revised(mid, text)
                yield _sse({"type": "done", "message": {"id": new_msg["id"], "content": text, "model": settings["chat_model"]}})
        except httpx.ConnectError:
            yield _sse({"type": "error", "message": "连不上本地模型服务"})
        except OllamaError as exc:
            yield _sse({"type": "error", "message": exc.message})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(data: dict) -> str:
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def _update_feedback_revised(orig_mid: str, revised: str) -> None:
    try:
        rows = db.list_feedbacks(limit=1000)
        for f in rows:
            if f.get("message_id") == orig_mid:
                db.update_feedback(f["id"], revised_reply=revised)
    except Exception:
        pass


# ---------------------------------------------------------------- 反馈中心

@router.get("/api/feedbacks")
async def feedback_list(vote: str | None = None, limit: int = 200):
    items = db.list_feedbacks(vote=vote, limit=limit)
    return {"items": items, "stats": db.feedback_stats()}


@router.delete("/api/feedbacks/{fid}")
async def feedback_delete(fid: str):
    db.delete_feedback(fid)
    return {"ok": True}


@router.post("/api/feedbacks/{fid}/choice")
async def feedback_choice(fid: str, payload: dict):
    """用户在原回复和重生成回复之间做选择。"""
    choice = payload.get("choice")
    if choice not in ("original", "revised"):
        raise HTTPException(400, "choice 必须是 'original' 或 'revised'")
    db.update_feedback(fid, final_choice=choice)
    return {"ok": True}


@router.delete("/api/feedbacks")
async def feedback_clear(vote: str | None = None):
    removed = db.clear_feedbacks(vote=vote)
    return {"ok": True, "removed": removed}
