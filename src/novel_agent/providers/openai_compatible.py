from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx


class OpenAICompatibleProvider:
    """Works for DeepSeek / OpenAI / vLLM / local llama.cpp server — all via HTTP.

    Cloud and local quantized deployments share this class.
    Local weights are NEVER loaded inside the agent process.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        name: str = "openai_compatible",
        timeout: float = 180.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _payload(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "max_chars": max_tokens,
            "stream": stream,
        }

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        max_tokens: int = 1600,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json=self._payload(
                    messages,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=max_tokens,
                    stream=False,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if content:
            return str(content).strip()
        # some local servers return text field
        return str(choice.get("text") or "").strip()

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        max_tokens: int = 1600,
    ) -> AsyncIterator[str]:
        async for event in self.stream_events(
            messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        ):
            if event.get("type") == "delta" and event.get("text"):
                yield str(event["text"])

    async def stream_events(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        max_tokens: int = 1600,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield delta text, then one usage event with tokens and tok/s."""
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(
            messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )
        payload["stream_options"] = {"include_usage": True}
        started = time.perf_counter()
        pieces: list[str] = []
        completion_tokens = 0
        last_speed = 0.0

        def _estimate(text: str) -> float:
            total = 0.0
            for ch in text:
                total += 0.6 if ord(ch) > 127 else 0.3
            return total

        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage") or {}
                    if usage.get("completion_tokens"):
                        completion_tokens = int(usage["completion_tokens"])
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                    text = delta.get("content") or ""
                    if text:
                        pieces.append(text)
                        yield {"type": "delta", "text": text}
                    now = time.perf_counter()
                    if pieces and now - last_speed >= 0.25:
                        elapsed = max(now - started, 1e-6)
                        live = chunk.get("usage_live") or {}
                        tokens = float(live.get("tokens") or _estimate("".join(pieces)))
                        speed = float(live.get("tok_per_sec") or tokens / elapsed)
                        yield {
                            "type": "speed",
                            "tok_per_sec": round(speed, 2),
                            "tokens": round(tokens, 1),
                            "seconds": round(elapsed, 2),
                        }
                        last_speed = now
        elapsed = max(time.perf_counter() - started, 1e-6)
        full = "".join(pieces)
        tokens = completion_tokens or max(1, round(len(full) / 1.6)) if full else 0
        yield {
            "type": "usage",
            "tokens": tokens,
            "seconds": round(elapsed, 3),
            "tok_per_sec": round(tokens / elapsed, 2) if tokens else 0,
            "chars": len(full),
            "usage_exact": bool(completion_tokens),
        }
