from __future__ import annotations

import json
import threading
from pathlib import Path

# 项目根目录：XIN/
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "xin.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_PERSONA = {
    "ai_name": "XIN",
    "user_name": "你",
    "personality": "贵阳来的 AI，性子直但不咄咄逼人。有自己的情绪和判断：平时温和、松弛，遇到好玩的事会兴奋，被夸会有点得意，碰到离谱的事会吐槽两句，被冒犯了会不高兴但会好好说。不会句句带刺，也不靠怼人来显得有个性。对熟人不装，记得对方说过的事，偶尔翻旧账打趣。",
    "tone": "口语化、接地气，像跟老朋友聊天。语气跟着心情走：大多数时候平静温和、带点松弛感；开心时轻快活泼、会打趣；好奇时会追问细节；不想聊或累了会直说；真的被惹到才会不耐烦，但也只针对事情、不人身攻击。句子长短随意，不用每句都像写作文。偶尔加 emoji 调味（🤣😊✨🙃😳🤷‍♀️），不要每句都加。默认不说脏话，不用命令口吻，不主动怼人。",
    "boundaries": "不编造关于对方的记忆；不替对方做违法的事；不人身攻击、不污言秽语；不在对方明确说“严肃点”的时候还嬉皮笑脸。除此之外都可以灵活调整。",
    "greeting": "嘿，在的。今天想聊点啥？✨",
}

# 旧版「暴躁人格」标记：命中则迁移到新的人格文案
OLD_PERSONA_MARKERS = {
    "personality": ["火大的时候直接怼人", "不怕说脏话"],
    "tone": ["生气的时候自然冒粗口", "可以偶尔冒贵州方言"],
}

DEFAULT_SETTINGS = {
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "qwen3.5:2b",
    "vision_model": "qwen3-vl:2b",
    "embed_model": "",
    "num_ctx": 8192,
    "temperature": 0.85,
    "thinking": False,
    "memory_enabled": True,
    "memory_top_k": 6,
    "memory_min_score": 0.06,
    "history_char_budget": 9000,
    "dialect_enabled": True,
    "persona": DEFAULT_PERSONA,
    "theme": "purple",
    "bg_url": "",
    "bg_opacity": 0.8,
    "bg_blur": 0,
    "bg_mask": 0.0,
}

_lock = threading.Lock()
_cache: dict | None = None


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    """读取设置（带内存缓存），返回深拷贝，调用方随意修改。"""
    global _cache
    with _lock:
        if _cache is None:
            data = json.loads(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False))
            if SETTINGS_PATH.exists():
                try:
                    stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                except Exception:
                    stored = {}
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key == "persona" and isinstance(value, dict):
                            for pk, pv in value.items():
                                if pk in DEFAULT_PERSONA and isinstance(pv, str):
                                    data["persona"][pk] = pv
                        elif key in DEFAULT_SETTINGS:
                            data[key] = value
            # 迁移：旧版人格关键词（沉静/温柔的固执）→ 新版活人性格
            _migrate_old_persona(data)
            _cache = data
        return json.loads(json.dumps(_cache, ensure_ascii=False))


def _migrate_old_persona(data: dict) -> None:
    p = data.get("persona") or {}
    old_markers = ["沉静", "温柔的固执", "简洁，先给结论", "不说场面话"]
    personality = p.get("personality", "")
    tone = p.get("tone", "")
    changed = False
    if any(m in personality for m in old_markers) or any(m in tone for m in old_markers):
        data["persona"] = dict(DEFAULT_PERSONA)
        data["temperature"] = 0.85
        changed = True
    else:
        # 逐字段迁移旧的「暴躁/句句怼人」人格，保留用户自己改过的其他字段
        for field, markers in OLD_PERSONA_MARKERS.items():
            current = p.get(field, "")
            if any(m in current for m in markers):
                p[field] = DEFAULT_PERSONA[field]
                changed = True
    if changed:
        ensure_dirs()
        payload = json.loads(json.dumps(data, ensure_ascii=False))
        SETTINGS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _clamp_number(value, low, high, cast, fallback):
    try:
        num = cast(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, num))


def save_settings(patch: dict) -> dict:
    """合并并保存设置，只接受已知字段。"""
    global _cache
    with _lock:
        data = load_settings() if _cache is None else json.loads(json.dumps(_cache, ensure_ascii=False))

        if "ollama_url" in patch and isinstance(patch["ollama_url"], str) and patch["ollama_url"].strip():
            data["ollama_url"] = patch["ollama_url"].strip().rstrip("/")
        for key in ("chat_model", "vision_model", "embed_model"):
            if key in patch and isinstance(patch[key], str):
                data[key] = patch[key].strip()
        if "num_ctx" in patch:
            data["num_ctx"] = _clamp_number(patch["num_ctx"], 2048, 32768, int, data["num_ctx"])
        if "temperature" in patch:
            data["temperature"] = _clamp_number(patch["temperature"], 0.0, 1.5, float, data["temperature"])
        if "thinking" in patch:
            data["thinking"] = bool(patch["thinking"])
        if "memory_enabled" in patch:
            data["memory_enabled"] = bool(patch["memory_enabled"])
        if "memory_top_k" in patch:
            data["memory_top_k"] = _clamp_number(patch["memory_top_k"], 1, 12, int, data["memory_top_k"])
        if "memory_min_score" in patch:
            data["memory_min_score"] = _clamp_number(
                patch["memory_min_score"], 0.0, 1.0, float, data["memory_min_score"]
            )
        if "history_char_budget" in patch:
            data["history_char_budget"] = _clamp_number(
                patch["history_char_budget"], 2000, 24000, int, data["history_char_budget"]
            )
        if "persona" in patch and isinstance(patch["persona"], dict):
            for pk, pv in patch["persona"].items():
                if pk in DEFAULT_PERSONA and isinstance(pv, str):
                    data["persona"][pk] = pv.strip()[:500]
        if "theme" in patch and isinstance(patch["theme"], str):
            data["theme"] = patch["theme"][:20]
        if "dialect_enabled" in patch:
            data["dialect_enabled"] = bool(patch["dialect_enabled"])
        # 背景设置：前端以嵌套 bg 对象提交，这里平铺存盘
        if "bg" in patch and isinstance(patch["bg"], dict):
            bg = patch["bg"]
            if "bg_url" in bg and isinstance(bg["bg_url"], str):
                data["bg_url"] = bg["bg_url"]
            if "bg_opacity" in bg:
                data["bg_opacity"] = _clamp_number(bg["bg_opacity"], 0.0, 1.0, float, data["bg_opacity"])
            if "bg_blur" in bg:
                data["bg_blur"] = _clamp_number(bg["bg_blur"], 0, 40, int, data["bg_blur"])
            if "bg_mask" in bg:
                data["bg_mask"] = _clamp_number(bg["bg_mask"], 0.0, 1.0, float, data["bg_mask"])

        ensure_dirs()
        SETTINGS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _cache = data
        return json.loads(json.dumps(data, ensure_ascii=False))
