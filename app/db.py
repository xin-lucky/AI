from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    model TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    images TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_convo ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 3,
    embedding TEXT,
    source TEXT DEFAULT '',
    hits INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);

CREATE TABLE IF NOT EXISTS feedbacks (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    user_question TEXT NOT NULL,
    ai_reply TEXT NOT NULL,
    model TEXT,
    vote TEXT NOT NULL,           -- 'up' / 'down'
    reasons TEXT NOT NULL DEFAULT '[]',  -- 点踩原因列表 JSON
    revised_reply TEXT,           -- 重生成后的回复（如果有）
    final_choice TEXT,            -- 'original' / 'revised' / null
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedbacks_msg ON feedbacks(message_id);
CREATE INDEX IF NOT EXISTS idx_feedbacks_vote ON feedbacks(vote);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def conn():
    ensure_dirs()
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- 对话

def create_conversation(title: str, model: str | None = None) -> dict:
    cid = new_id()
    ts = now()
    with conn() as c:
        c.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at, model) VALUES (?,?,?,?,?)",
            (cid, title or "新的对话", ts, ts, model),
        )
    return {"id": cid, "title": title or "新的对话", "created_at": ts, "updated_at": ts, "model": model}


def list_conversations() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
               FROM conversations c WHERE c.archived = 0 ORDER BY c.updated_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def update_conversation(cid: str, *, title: str | None = None, archived: bool | None = None) -> None:
    fields, values = [], []
    if title is not None:
        fields.append("title = ?")
        values.append(title.strip()[:60] or "未命名")
    if archived is not None:
        fields.append("archived = ?")
        values.append(1 if archived else 0)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(now())
    values.append(cid)
    with conn() as c:
        c.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?", values)


def touch_conversation(cid: str) -> None:
    with conn() as c:
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), cid))


def delete_conversation(cid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        c.execute("DELETE FROM conversations WHERE id = ?", (cid,))


def delete_all_conversations() -> int:
    """删除全部对话和消息，并 VACUUM 回收磁盘空间。返回删除的对话数量。"""
    with conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()
        count = int(row["n"] if row else 0)
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM conversations")
    try:
        vacuum = sqlite3.connect(DB_PATH, timeout=15)
        try:
            vacuum.execute("VACUUM")
        finally:
            vacuum.close()
    except sqlite3.Error:
        pass
    return count


# ---------------------------------------------------------------- 消息

def update_message_content(mid: str, content: str, meta: dict | None = None) -> None:
    with conn() as c:
        current_meta = {}
        row = c.execute("SELECT meta FROM messages WHERE id = ?", (mid,)).fetchone()
        if row:
            try:
                current_meta = json.loads(row["meta"] or "{}")
            except Exception:
                current_meta = {}
        if meta:
            current_meta.update(meta)
        c.execute(
            "UPDATE messages SET content = ?, meta = ? WHERE id = ?",
            (content, json.dumps(current_meta, ensure_ascii=False), mid),
        )

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    images: list[str] | None = None,
    model: str | None = None,
    meta: dict | None = None,
) -> dict:
    mid = new_id()
    ts = now()
    images_json = json.dumps(images or [], ensure_ascii=False)
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    with conn() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, content, images, model, meta, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mid, conversation_id, role, content, images_json, model, meta_json, ts),
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conversation_id))
    return {
        "id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "images": images or [],
        "model": model,
        "meta": meta or {},
        "created_at": ts,
    }


def list_messages(conversation_id: str, limit: int | None = None) -> list[dict]:
    sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY rowid"
    params: list = [conversation_id]
    if limit:
        sql += " DESC LIMIT ?"
        params.append(limit)
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["images"] = json.loads(item.get("images") or "[]")
        item["meta"] = json.loads(item.get("meta") or "{}")
        items.append(item)
    if limit:
        items.reverse()
    return items


# ---------------------------------------------------------------- 记忆

def add_memory(
    kind: str,
    content: str,
    importance: int = 3,
    *,
    source: str = "对话",
    embedding: list[float] | None = None,
) -> dict:
    mid = new_id()
    ts = now()
    with conn() as c:
        c.execute(
            "INSERT INTO memories (id, kind, content, importance, embedding, source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                mid,
                kind,
                content,
                importance,
                json.dumps(embedding) if embedding else None,
                source,
                ts,
                ts,
            ),
        )
    return {
        "id": mid,
        "kind": kind,
        "content": content,
        "importance": importance,
        "source": source,
        "hits": 0,
        "created_at": ts,
        "updated_at": ts,
        "embedding": embedding,
    }


def list_memories(query: str | None = None) -> list[dict]:
    sql = "SELECT * FROM memories"
    params: list = []
    if query:
        sql += " WHERE content LIKE ? OR kind LIKE ?"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY importance DESC, updated_at DESC"
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_memory(
    mid: str,
    *,
    content: str | None = None,
    kind: str | None = None,
    importance: int | None = None,
    embedding: list[float] | None = None,
) -> None:
    fields, values = [], []
    if content is not None:
        fields.append("content = ?")
        values.append(content.strip()[:400])
    if kind is not None:
        fields.append("kind = ?")
        values.append(kind)
    if importance is not None:
        fields.append("importance = ?")
        values.append(max(1, min(5, int(importance))))
    if embedding is not None:
        fields.append("embedding = ?")
        values.append(json.dumps(embedding))
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(now())
    values.append(mid)
    with conn() as c:
        c.execute(f"UPDATE memories SET {', '.join(fields)} WHERE id = ?", values)


def delete_memory(mid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM memories WHERE id = ?", (mid,))


def mark_memories_used(ids: list[str]) -> None:
    if not ids:
        return
    ts = now()
    with conn() as c:
        for mid in ids:
            c.execute(
                "UPDATE memories SET hits = hits + 1, last_used_at = ? WHERE id = ?", (ts, mid)
            )


# ---------------------------------------------------------------- 统计

def stats() -> dict:
    with conn() as c:
        conversations = c.execute("SELECT COUNT(*) FROM conversations WHERE archived = 0").fetchone()[0]
        messages = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        memories = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        since = c.execute("SELECT MIN(created_at) FROM messages").fetchone()[0]
        week = c.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= datetime('now','localtime','-7 day')"
        ).fetchone()[0]
        new_memories = c.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= datetime('now','localtime','-7 day')"
        ).fetchone()[0]
    days = 0
    if since:
        try:
            days = (datetime.now().date() - datetime.strptime(since[:10], "%Y-%m-%d").date()).days + 1
        except ValueError:
            days = 1
    return {
        "conversations": conversations,
        "messages": messages,
        "memories": memories,
        "days": days,
        "since": since,
        "messages_7d": week,
        "memories_7d": new_memories,
    }


def export_all() -> dict:
    with conn() as c:
        conversations = [dict(r) for r in c.execute("SELECT * FROM conversations ORDER BY created_at").fetchall()]
        messages = []
        for r in c.execute("SELECT * FROM messages ORDER BY rowid").fetchall():
            item = dict(r)
            item["images"] = json.loads(item.get("images") or "[]")
            item["meta"] = json.loads(item.get("meta") or "{}")
            messages.append(item)
        memories = []
        for r in c.execute("SELECT * FROM memories ORDER BY created_at").fetchall():
            item = dict(r)
            item.pop("embedding", None)
            memories.append(item)
    return {"conversations": conversations, "messages": messages, "memories": memories}


# ---------------------------------------------------------------- 反馈

def add_feedback(
    *,
    message_id: str,
    conversation_id: str,
    role: str,
    user_question: str,
    ai_reply: str,
    vote: str,
    model: str | None = None,
    reasons: list[str] | None = None,
    revised_reply: str | None = None,
) -> dict:
    fid = new_id()
    ts = now()
    reasons_json = json.dumps(reasons or [], ensure_ascii=False)
    with conn() as c:
        # 同一条消息同一个 vote 只保留最新一条
        c.execute(
            "DELETE FROM feedbacks WHERE message_id = ? AND vote = ?",
            (message_id, vote),
        )
        c.execute(
            """INSERT INTO feedbacks (id, message_id, conversation_id, role, user_question,
               ai_reply, model, vote, reasons, revised_reply, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (fid, message_id, conversation_id, role, user_question,
             ai_reply, model, vote, reasons_json, revised_reply, ts),
        )
    return {"id": fid, "vote": vote, "created_at": ts}


def update_feedback(fid: str, **fields) -> None:
    allowed = {"reasons", "revised_reply", "final_choice"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "reasons":
            v = json.dumps(v or [], ensure_ascii=False)
        cols.append(f"{k} = ?")
        vals.append(v)
    if not cols:
        return
    cols.append("created_at = ?")
    vals.append(now())
    vals.append(fid)
    with conn() as c:
        c.execute(f"UPDATE feedbacks SET {', '.join(cols)} WHERE id = ?", vals)


def list_feedbacks(vote: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM feedbacks"
    params: list = []
    if vote:
        sql += " WHERE vote = ?"
        params.append(vote)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["reasons"] = json.loads(item.get("reasons") or "[]")
        items.append(item)
    return items


def delete_feedback(fid: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM feedbacks WHERE id = ?", (fid,))


def clear_feedbacks(vote: str | None = None) -> int:
    with conn() as c:
        if vote:
            c.execute("DELETE FROM feedbacks WHERE vote = ?", (vote,))
        else:
            c.execute("DELETE FROM feedbacks")
    return c.total_changes


def feedback_stats() -> dict:
    with conn() as c:
        up = c.execute("SELECT COUNT(*) FROM feedbacks WHERE vote = 'up'").fetchone()[0]
        down = c.execute("SELECT COUNT(*) FROM feedbacks WHERE vote = 'down'").fetchone()[0]
        chosen = c.execute("SELECT COUNT(*) FROM feedbacks WHERE final_choice IS NOT NULL").fetchone()[0]
    return {"up": up, "down": down, "with_choice": chosen}
