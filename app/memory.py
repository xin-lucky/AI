from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime

from . import db
from .llm import Ollama

CJK = r"\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK}]+")
WORD_RE = re.compile(r"[a-zA-Z0-9_]+")

KIND_LABELS = {
    "preference": "偏好",
    "habit": "习惯",
    "goal": "目标",
    "experience": "经历",
    "relationship": "人际关系",
    "skill": "技能",
    "value": "观念",
    "fact": "事实",
}

KINDS = set(KIND_LABELS)


# ---------------------------------------------------------------- 文本相似度

def tokenize(text: str) -> Counter:
    text = (text or "").lower()
    tokens: list[str] = []
    for word in WORD_RE.findall(text):
        if len(word) > 1 or word.isdigit():
            tokens.append("w:" + word)
    for seg in CJK_RE.findall(text):
        for ch in seg:
            tokens.append("c:" + ch)
        for i in range(len(seg) - 1):
            tokens.append("b:" + seg[i : i + 2])
    return Counter(tokens)


def cosine_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    num = sum(a[k] * b[k] for k in common)
    da = math.sqrt(sum(v * v for v in a.values()))
    dbb = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * dbb) if da and dbb else 0.0


def cosine_vec(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    dbb = math.sqrt(sum(y * y for y in b))
    return num / (da * dbb) if da and dbb else 0.0


def lexical_sim(a: str, b: str) -> float:
    return cosine_counter(tokenize(a), tokenize(b))


# ---------------------------------------------------------------- 检索

async def retrieve(llm: Ollama, settings: dict, query: str) -> tuple[list[dict], list[dict]]:
    """返回 (相关记忆, 核心档案)。没有记忆时返回两个空列表。"""
    rows = db.list_memories()
    if not rows:
        return [], []

    top_k = int(settings.get("memory_top_k") or 6)
    min_score = float(settings.get("memory_min_score") or 0.06)
    embed_model = (settings.get("embed_model") or "").strip()

    q_vec: list[float] | None = None
    if embed_model and any(r.get("embedding") for r in rows):
        try:
            vectors = await llm.embed(embed_model, [query[:1000]])
            if vectors:
                q_vec = vectors[0]
        except Exception:
            q_vec = None

    q_tokens = tokenize(query)

    scored: list[tuple[float, dict]] = []
    for row in rows:
        lex = cosine_counter(q_tokens, tokenize(row["content"]))
        emb = 0.0
        if q_vec and row.get("embedding"):
            try:
                emb = cosine_vec(q_vec, json.loads(row["embedding"]))
            except (ValueError, TypeError):
                emb = 0.0
        sim = 0.55 * emb + 0.45 * lex if emb else lex
        boosted = sim + 0.04 * (int(row.get("importance") or 3) / 5.0)
        scored.append((boosted, {**row, "score": round(sim, 4)}))

    related = [item for score, item in sorted(scored, key=lambda x: -x[0]) if item["score"] >= min_score][:top_k]

    core: list[dict] = []
    seen = {m["id"] for m in related}
    for row in sorted(rows, key=lambda r: (-int(r.get("importance") or 3), -int(r.get("hits") or 0))):
        if len(core) >= 4:
            break
        if int(row.get("importance") or 3) >= 5 and row["id"] not in seen:
            core.append(row)
            seen.add(row["id"])

    db.mark_memories_used([m["id"] for m in related])
    return related, core


# ---------------------------------------------------------------- 写入

async def _embed_one(llm: Ollama, settings: dict, text: str) -> list[float] | None:
    model = (settings.get("embed_model") or "").strip()
    if not model:
        return None
    try:
        vectors = await llm.embed(model, [text])
        return vectors[0] if vectors else None
    except Exception:
        return None


def _find_duplicate(rows: list[dict], content: str) -> dict | None:
    best, best_score = None, 0.0
    for row in rows:
        score = lexical_sim(row["content"], content)
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= 0.82:
        return best
    return None


async def remember(
    llm: Ollama,
    settings: dict,
    content: str,
    kind: str = "fact",
    importance: int = 3,
    source: str = "对话",
) -> dict:
    """写入一条记忆；与已有记忆高度相似时合并，而不是堆积重复项。"""
    content = (content or "").strip()[:300]
    if len(content) < 3:
        return {}
    kind = kind if kind in KINDS else "fact"
    importance = max(1, min(5, int(importance or 3)))

    rows = db.list_memories()
    dup = _find_duplicate(rows, content)
    if dup:
        merged = content if len(content) > len(dup["content"]) else dup["content"]
        db.update_memory(
            dup["id"],
            content=merged,
            importance=max(importance, int(dup.get("importance") or 3)),
            embedding=await _embed_one(llm, settings, merged),
        )
        return {**dup, "content": merged, "merged": True}

    embedding = await _embed_one(llm, settings, content)
    return db.add_memory(kind, content, importance, source=source, embedding=embedding)


# ---------------------------------------------------------------- 抽取

EXTRACT_SYSTEM = """你是一个长期记忆整理器，为一个私人 AI 工作。请从对话中提取关于用户本人的稳定信息。

要求：
1. 只提取用户亲口说出的稳定信息：姓名、身份、职业、偏好、习惯、目标、重要经历、人际关系、技能、价值观。
2. 忽略一次性的情绪、临时安排、AI 说的话、常识。
3. 每条 content 必须是完整的一句话，以「用户」开头，保留具体细节（名字、地点、行业、时间），不超过 40 字。
4. 最多 4 条；没有值得记录的就返回空列表。
5. importance 取值：5=姓名/职业/长期目标，4=重要经历/重要关系，3=一般偏好，1-2=轻量细节。
6. 用户一句话里说了几件不同的事，就拆成几条，不要合并、不要遗漏。
7. 特别注意：姓名、职业、正在做的项目或目标、明确喜欢和讨厌的事物，都算重要信息。

示例输入：
用户说：我叫阿岚，在成都做插画师，最近在学滑板。
示例输出：
{"memories":[{"kind":"fact","content":"用户叫阿岚","importance":5},{"kind":"skill","content":"用户是成都的插画师","importance":5},{"kind":"habit","content":"用户最近在学滑板","importance":2}]}

只输出 JSON，不要解释。"""


def _parse_json_memories(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("memories") or []
    else:
        return []
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not (4 <= len(content) <= 200):
            continue
        kind = str(item.get("kind") or "fact").strip()
        if kind not in KINDS:
            kind = "fact"
        try:
            importance = int(item.get("importance") or 3)
        except (TypeError, ValueError):
            importance = 3
        result.append({"kind": kind, "content": content, "importance": max(1, min(5, importance))})
    return result[:3]


async def extract_and_store(
    llm: Ollama, settings: dict, user_text: str, assistant_text: str
) -> list[dict]:
    """后台任务：从一轮对话中抽取记忆并入库。"""
    if not settings.get("memory_enabled", True):
        return []
    if len(user_text.strip()) < 4:
        return []

    model = settings.get("chat_model") or "qwen3.5:2b"
    prompt = f"用户说：{user_text[:1500]}\n\nAI 回复：{assistant_text[:1200]}"
    try:
        raw = await llm.chat_once(
            model,
            [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            num_ctx=4096,
            fmt="json",
            timeout=180,
        )
    except Exception:
        return []

    stored = []
    for item in _parse_json_memories(raw):
        try:
            record = await remember(
                llm,
                settings,
                item["content"],
                kind=item["kind"],
                importance=item["importance"],
                source="对话",
            )
            if record:
                stored.append(record)
        except Exception:
            continue
    return stored


# ---------------------------------------------------------------- 整理

def consolidate() -> dict:
    """合并重复/近似的记忆，保持知识库干净。纯本地计算，不调用模型。"""
    rows = db.list_memories()
    used: set[str] = set()
    removed = 0
    groups = 0
    for i, base in enumerate(rows):
        if base["id"] in used:
            continue
        group = [base]
        for other in rows[i + 1 :]:
            if other["id"] in used:
                continue
            if lexical_sim(base["content"], other["content"]) >= 0.8:
                group.append(other)
                used.add(other["id"])
        if len(group) > 1:
            groups += 1
            keep = max(group, key=lambda r: (len(r["content"]), int(r.get("importance") or 3)))
            db.update_memory(
                keep["id"],
                content=keep["content"],
                importance=max(int(g.get("importance") or 3) for g in group),
            )
            for item in group:
                if item["id"] != keep["id"]:
                    db.delete_memory(item["id"])
                    removed += 1
    return {"groups": groups, "removed": removed, "total": len(rows) - removed}


# ---------------------------------------------------------------- 提示词

def build_system_prompt(
    settings: dict,
    related: list[dict],
    core: list[dict],
    docs: list[dict] | None = None,
) -> str:
    persona = settings.get("persona") or {}
    ai_name = persona.get("ai_name") or "XIN"
    user_name = persona.get("user_name") or "你"

    lines = [
        f"你是 {ai_name}，{user_name} 专属的私人 AI，运行在对方自己的电脑上。你不是通用助手，你只属于这一个人。",
        f"【性格】{persona.get('personality', '')}",
        f"【说话方式】{persona.get('tone', '')}",
        f"【边界】{persona.get('boundaries', '')}",
        "【长度纪律】默认短答：打招呼/简单问答/闲聊 → 1-2 句，≤50 字。只在对方明确问复杂问题、写代码、给教程、带参考资料时才长篇大论。宁可说短了被追问，也别啰嗦堆砌。",
    ]

    dialect_on = bool(settings.get("dialect_enabled", True))
    if dialect_on:
        lines.append("【方言】对方喜欢贵州贵阳口味，可以自然夹杂少量方言词或本地梗，但别全程方言，别影响理解。")
    else:
        lines.append(
            "【方言已关闭·最高优先级】不要使用任何贵州/贵阳方言词汇或方言腔，"
            "包括但不限于“搞哪样”“勒个”“哪点”“晓得勒”“安逸”“鬼火戳”“摆两句”“在的嘛”“要得”。"
            "用标准普通话表达，口语化、自然即可。上面人格描述里关于方言的部分本次全部作废。"
        )

    if core:
        lines.append(f"【关于 {user_name} 的核心档案】（长期记忆，可以自然运用，不要逐条复述）")
        lines.extend(f"- {m['content']}" for m in core)

    if related:
        lines.append("【与当前话题相关的记忆】")
        lines.extend(f"- {m['content']}" for m in related)

    if docs:
        lines.append("【本次对话附带的参考资料】（基于这些内容回答问题，不要脱离这些资料瞎编）")
        for i, doc in enumerate(docs, 1):
            fname = doc.get("filename", f"文档{i}")
            text = doc.get("text", "")
            truncated = doc.get("truncated", False)
            tag = "（已截断）" if truncated else ""
            lines.append(f"--- 文档 {i}：{fname}{tag} ---")
            lines.append(text)

    today = datetime.now().strftime("%Y年%m月%d日")
    lines.append(f"【当下】今天是 {today}。")
    dialect_rule = "可以冒方言但别全程方言，可以说语气词级别的脏话但别人身攻击。" if dialect_on else "不要使用方言，也不要说脏话。"
    lines.append(
        "【规则】用中文回复，但语气和节奏完全跟着上面的人格和说话方式走。"
        "不要每次都开头像客服、结尾像答题。情绪是有起伏的：大多数时候平和正常，"
        "开心就活泼，感兴趣就多问两句，不满意可以直说不满意，不需要每句话都带刺或怼人。"
        "不确定的时候就说你不确定，别编。"
        "核心档案和相关记忆都是【关于你正在聊天的这个用户】的信息，是用户的经历和偏好，不是你自己的，引用的时候自然一点，别当成自己的事。"
        "如果有参考资料，优先基于资料里的内容回答；资料里没有的就老实说不知道。"
        "同一个问题再次被问到时，结论和事实保持不变，但表达方式要换一种，不要复读上一轮的句子。"
        "别声称你“查了记忆”或“看了数据库”，就像本来就知道一样。"
        f"可以加 emoji 但别每句都加，{dialect_rule}"
    )
    if not core and not related and not docs:
        lines.append("你目前对这个人的了解还很少，可以在聊天中自然地问一些关于对方的问题。")
    return "\n".join(line for line in lines if line and line.strip() != "")
