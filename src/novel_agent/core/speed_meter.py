"""本地模型 token 速度：优先读推理服务的 /metrics 计数器，生成时用流式 usage 覆盖。"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from novel_agent.settings import Settings

_COUNTERS = (
    "llamacpp:tokens_predicted_total",
    "vllm:generation_tokens_total",
    "sglang:generation_tokens_total",
)
_counter_re = re.compile(r"^([a-zA-Z0-9_:]+)(\{[^}]*\})?\s+([0-9eE+\-.]+)\s*$")

_state: dict[str, Any] = {
    "online": False,
    "tok_per_sec": None,
    "tokens": None,
    "source": "idle",
    "model": "",
    "base_url": "",
    "note": "未检测",
    "updated_at": 0.0,
}
_prev: tuple[float, float] | None = None


def record_stream(sample: dict[str, Any], *, model: str, base_url: str) -> dict[str, Any]:
    global _state
    _state = {
        "online": True,
        "tok_per_sec": sample.get("tok_per_sec"),
        "tokens": sample.get("tokens"),
        "source": "stream" if sample.get("usage_exact") else "stream-estimate",
        "model": model,
        "base_url": base_url,
        "note": "来自最近一次生成",
        "updated_at": time.time(),
    }
    return dict(_state)


def snapshot() -> dict[str, Any]:
    return dict(_state)


def _sum_predicted(text: str) -> float | None:
    total = 0.0
    found = False
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        matched = _counter_re.match(line.strip())
        if not matched:
            continue
        name, _labels, value = matched.groups()
        if name in _COUNTERS or name.endswith("tokens_predicted_total"):
            total += float(value)
            found = True
    return total if found else None


async def sample_local(settings: Settings) -> dict[str, Any]:
    """探测本地 OpenAI 兼容服务。有 metrics 时用计数器差值算 tok/s。"""
    global _state, _prev
    base = (settings.local_llm_base_url or "http://127.0.0.1:8001/v1").rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    model = settings.local_llm_model or ""
    online = False
    note = "本地服务无响应"
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        try:
            models = await client.get(f"{base}/models")
            online = models.status_code < 500
            if models.status_code == 200:
                data = models.json()
                ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
                if ids and not model:
                    model = str(ids[0])
                note = "在线，空闲" if online else note
        except Exception as exc:
            note = f"离线：{type(exc).__name__}"
        metrics_text = ""
        if online:
            for url in (f"{root}/metrics", f"{base}/metrics"):
                try:
                    resp = await client.get(url)
                except Exception:
                    continue
                if resp.status_code == 200 and "token" in resp.text:
                    metrics_text = resp.text
                    break
    tok_per_sec = _state.get("tok_per_sec")
    source = _state.get("source") or "idle"
    if metrics_text:
        current = _sum_predicted(metrics_text)
        now = time.time()
        if current is not None and _prev is not None:
            prev_t, prev_v = _prev
            dt = now - prev_t
            if dt >= 0.5:
                tok_per_sec = round(max(0.0, (current - prev_v) / dt), 2)
                source = "metrics"
                note = "metrics 计数器"
        if current is not None:
            _prev = (now, current)
    elif not online:
        tok_per_sec = None
        source = "offline"
    _state = {
        "online": online,
        "tok_per_sec": tok_per_sec,
        "tokens": _state.get("tokens"),
        "source": source,
        "model": model,
        "base_url": base,
        "note": note,
        "updated_at": time.time(),
    }
    return dict(_state)
