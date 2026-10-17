"""Async client wrapper for local Ollama.

Uses httpx. We call the native `/api/chat` endpoint with `stream=False` to
keep the interface simple and JSON-only. Connect, timeout and HTTP errors are
mapped to exceptions that the FastAPI handlers translate to user-facing
error messages without leaking internal details.

This module never touches hosted models, blockchain, RPC, wallet, etc.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic_core import to_json

from .config import settings


class OllamaError(Exception):
    """Base exception for Ollama-related errors."""


class OllamaUnavailableError(OllamaError):
    """Raised when Ollama endpoint is not reachable."""


class OllamaTimeoutError(OllamaError):
    """Raised on request timeout."""


class OllamaHttpError(OllamaError):
    """Raised on non-2xx responses from Ollama."""


async def list_models() -> list[str]:
    """Return available model IDs from the local Ollama instance.

    Returns empty list if unreachable — the caller decides UX (e.g. health
    shows model_unavailable and UI disables actions).
    """
    try:
        async with httpx.AsyncClient(timeout=settings.timeout) as client:
            resp = await client.get(f"{settings.ollama_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = data.get("models", [])
        ids = []
        for m in models:
            # Native tags API returns 'name'. Keep consistent.
            mid = m.get("name") or m.get("id") or m.get("model")
            if mid:
                ids.append(mid)
        # If a model is specified in settings but not in tags, include it
        # to avoid confusing the UI; but still treat as available only if tags
        # were reachable.
        return ids
    except (httpx.ConnectError, httpx.TimeoutException):
        return []
    except Exception:
        return []


async def chat(
    model: str | None,
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
    num_predict: int | None = None,
    temperature: float | None = None,
) -> tuple[str, str, int]:
    """Call Ollama `/api/chat` with `stream=False`.

    Returns (content, model_used, elapsed_ms).

    Raises OllamaUnavailableError, OllamaTimeoutError, OllamaHttpError.
    """
    selected = model or settings.model
    tmo = timeout or settings.timeout
    np = num_predict or settings.num_predict
    temp = temperature or settings.temperature

    payload = {
        "model": selected,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temp,
            "num_predict": np,
            "stop": [],
        },
    }

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=tmo) as client:
            resp = await client.post(f"{settings.ollama_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaUnavailableError(str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise OllamaTimeoutError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise OllamaHttpError(f"HTTP {exc.response.status_code}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)

    msg = data.get("message", {})
    content = msg.get("content", "") or data.get("response", "")

    # Safety net: if content is non-string, serialize.
    if not isinstance(content, str):
        try:
            content = to_json(content).decode("utf-8")
        except Exception:
            content = str(content)

    return content, selected, elapsed_ms


@dataclass
class ToolChatResult:
    """Result of a tool-enabled chat turn.

    `tool_calls` are the raw Ollama ``message.tool_calls`` objects (possibly
    empty). Nothing here executes anything — the caller decides whether to run
    an approved tool.
    """

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model_used: str = ""
    elapsed_ms: int = 0


async def chat_with_tools(
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    timeout: float | None = None,
    num_predict: int | None = None,
    temperature: float | None = None,
) -> ToolChatResult:
    """Call Ollama `/api/chat` with a `tools` list and `stream=False`.

    Returns a :class:`ToolChatResult`. Raises the same exceptions as ``chat``.

    We only forward the model's *proposed* tool calls; we never execute them
    here.
    """
    selected = model or settings.model
    tmo = timeout or settings.timeout
    np = num_predict or settings.num_predict
    temp = temperature or settings.temperature

    payload: dict[str, Any] = {
        "model": selected,
        "messages": messages,
        "stream": False,
        "tools": tools,
        "options": {
            "temperature": temp,
            "num_predict": np,
            "stop": [],
        },
    }

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=tmo) as client:
            resp = await client.post(f"{settings.ollama_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaUnavailableError(str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise OllamaTimeoutError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise OllamaHttpError(f"HTTP {exc.response.status_code}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)

    msg = data.get("message", {}) or {}
    content = msg.get("content", "") or data.get("response", "")
    if not isinstance(content, str):
        try:
            content = to_json(content).decode("utf-8")
        except Exception:
            content = str(content)

    raw_calls = msg.get("tool_calls") or []
    tool_calls: list[dict[str, Any]] = [c for c in raw_calls if isinstance(c, dict)]

    return ToolChatResult(
        content=content,
        tool_calls=tool_calls,
        model_used=selected,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "OllamaError",
    "OllamaUnavailableError",
    "OllamaTimeoutError",
    "OllamaHttpError",
    "ToolChatResult",
    "list_models",
    "chat",
    "chat_with_tools",
]
