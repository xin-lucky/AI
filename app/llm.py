from __future__ import annotations

import json
import random
from typing import AsyncIterator

import httpx


class OllamaError(Exception):
    """Ollama 返回的错误，message 用于直接展示给用户。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class Ollama:
    def __init__(self, base_url: str):
        self.url = (base_url or "http://127.0.0.1:11434").rstrip("/")

    # ------------------------------------------------------------ 基础

    @staticmethod
    def _timeout(read: float | None = None) -> httpx.Timeout:
        return httpx.Timeout(connect=8.0, read=read, write=120.0, pool=10.0)

    async def version(self) -> str:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(6)) as client:
            resp = await client.get(self.url + "/api/version")
            resp.raise_for_status()
            return str(resp.json().get("version", "unknown"))

    async def list_models(self) -> list[dict]:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(8)) as client:
            resp = await client.get(self.url + "/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = []
        for item in data.get("models", []):
            details = item.get("details") or {}
            models.append(
                {
                    "name": item.get("name", ""),
                    "size": int(item.get("size") or 0),
                    "family": details.get("family", ""),
                    "params": details.get("parameter_size", ""),
                    "quant": details.get("quantization_level", ""),
                    "modified": item.get("modified_at", ""),
                    "caps": item.get("capabilities") or [],
                }
            )
        models.sort(key=lambda m: m["name"])
        return models

    # ------------------------------------------------------------ 对话

    async def _stream_raw(self, payload: dict) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(None)) as client:
            async with client.stream("POST", self.url + "/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise OllamaError(body.strip() or f"HTTP {resp.status_code}", resp.status_code)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError(str(chunk["error"]))
                    message = chunk.get("message") or {}
                    if message.get("thinking"):
                        yield {"type": "thinking", "text": message["thinking"]}
                    if message.get("content"):
                        yield {"type": "token", "text": message["content"]}
                    if chunk.get("done"):
                        yield {
                            "type": "done",
                            "stats": {
                                "eval_count": chunk.get("eval_count", 0),
                                "eval_duration": chunk.get("eval_duration", 0),
                                "prompt_eval_count": chunk.get("prompt_eval_count", 0),
                                "total_duration": chunk.get("total_duration", 0),
                            },
                        }

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        num_ctx: int = 8192,
        think: bool = False,
    ) -> AsyncIterator[dict]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "top_p": 0.95,
                "repeat_penalty": 1.1,
                "presence_penalty": 0.4,
                "frequency_penalty": 0.3,
                # 每次请求换随机种子，避免同一问题得到逐字相同的回答
                "seed": random.randint(1, 2**31 - 1),
            },
        }
        payload["think"] = bool(think)
        try:
            async for event in self._stream_raw(payload):
                yield event
        except OllamaError as exc:
            # 非思考模型会拒绝 think 参数，去掉后重试一次
            if "think" in str(exc.message).lower():
                payload.pop("think", None)
                async for event in self._stream_raw(payload):
                    yield event
            else:
                raise

    async def chat_once(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        num_ctx: int = 4096,
        think: bool = False,
        fmt: str | None = None,
        timeout: float = 240.0,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": bool(think),
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        if fmt:
            payload["format"] = fmt
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(timeout)) as client:
            resp = await client.post(self.url + "/api/chat", json=payload)
            if resp.status_code >= 400:
                text = resp.text
                if "think" in text.lower():
                    payload.pop("think", None)
                    resp = await client.post(self.url + "/api/chat", json=payload)
                if resp.status_code >= 400:
                    raise OllamaError(resp.text.strip() or f"HTTP {resp.status_code}", resp.status_code)
            data = resp.json()
        return str((data.get("message") or {}).get("content", "") or "")

    # ------------------------------------------------------------ 向量

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(120)) as client:
            resp = await client.post(
                self.url + "/api/embed", json={"model": model, "input": inputs}
            )
            if resp.status_code >= 400:
                raise OllamaError(resp.text.strip() or f"HTTP {resp.status_code}", resp.status_code)
            data = resp.json()
        return data.get("embeddings") or []

    # ------------------------------------------------------------ 模型管理

    async def delete(self, name: str) -> None:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(30)) as client:
            resp = await client.request(
                "DELETE", self.url + "/api/delete", json={"model": name}
            )
            if resp.status_code >= 400:
                raise OllamaError(resp.text.strip() or f"HTTP {resp.status_code}", resp.status_code)

    async def pull(self, name: str) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout(None)) as client:
            async with client.stream(
                "POST", self.url + "/api/pull", json={"model": name, "stream": True}
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise OllamaError(body.strip() or f"HTTP {resp.status_code}", resp.status_code)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
